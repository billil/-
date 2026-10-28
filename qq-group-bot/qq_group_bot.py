#!/usr/bin/env python3
"""QQ 官方群机器人：接收群消息并保存到本地 SQLite。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

BASE_DIR = Path(__file__).resolve().parent
API_BASE = "https://api.bot.qq.com"
GROUP_AND_C2C_EVENT = 1 << 25
LOGGER = logging.getLogger("qq_group_bot")


class BotError(RuntimeError):
    """机器人运行错误。"""


class ConfigurationError(BotError):
    """配置错误。"""


class AuthenticationError(BotError):
    """访问凭证错误。"""


class FatalGatewayError(BotError):
    """不应自动重试的网关错误。"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def load_env_file(path: Path) -> None:
    """加载简单的 KEY=VALUE 配置文件，不覆盖已有环境变量。"""
    if not path.exists():
        return
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise ConfigurationError(f"{path.name} 第 {line_number} 行缺少 '='")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if not key:
            raise ConfigurationError(f"{path.name} 第 {line_number} 行的变量名为空")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    app_id: str
    app_secret: str
    allowed_groups: frozenset[str]
    database_path: Path
    retention_days: int = 30

    @property
    def allow_all_groups(self) -> bool:
        return "*" in self.allowed_groups

    def allows(self, group_openid: str) -> bool:
        return self.allow_all_groups or group_openid in self.allowed_groups

    @classmethod
    def from_env(cls) -> "Settings":
        load_env_file(BASE_DIR / ".env")
        app_id = os.getenv("QQ_APP_ID", "").strip()
        app_secret = os.getenv("QQ_APP_SECRET", "").strip()
        if not app_id or not app_secret:
            raise ConfigurationError(
                "请先在 .env 中填写 QQ_APP_ID 和 QQ_APP_SECRET；"
                "模板见 .env.example。"
            )

        groups = frozenset(
            item.strip()
            for item in os.getenv("QQ_ALLOWED_GROUPS", "").split(",")
            if item.strip()
        )
        database_value = os.getenv("QQ_DATABASE_PATH", "data/qq_messages.db").strip()
        database_path = Path(database_value)
        if not database_path.is_absolute():
            database_path = BASE_DIR / database_path

        retention_value = os.getenv("QQ_RETENTION_DAYS", "30").strip()
        try:
            retention_days = int(retention_value)
        except ValueError as exc:
            raise ConfigurationError("QQ_RETENTION_DAYS 必须是整数") from exc
        if retention_days < 0:
            raise ConfigurationError("QQ_RETENTION_DAYS 不能小于 0；0 表示不自动删除")

        return cls(
            app_id=app_id,
            app_secret=app_secret,
            allowed_groups=groups,
            database_path=database_path,
            retention_days=retention_days,
        )


class MessageStore:
    """SQLite 消息仓库；消息 ID 主键同时负责跨事件去重。"""

    def __init__(self, database_path: Path):
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database_path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                group_openid TEXT NOT NULL,
                member_openid TEXT,
                username TEXT,
                member_role TEXT,
                content TEXT,
                message_type INTEGER,
                attachments_json TEXT,
                event_type TEXT NOT NULL,
                timestamp TEXT,
                received_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_group_time "
            "ON messages(group_openid, timestamp)"
        )
        self.connection.commit()

    def save(self, message: dict[str, Any], event_type: str) -> bool:
        message_id = str(message.get("id") or "").strip()
        group_openid = str(message.get("group_openid") or "").strip()
        if not message_id or not group_openid:
            raise ValueError("群消息缺少 id 或 group_openid")
        author = message.get("author") or {}
        attachments = message.get("attachments")
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO messages (
                id, group_openid, member_openid, username, member_role,
                content, message_type, attachments_json, event_type,
                timestamp, received_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                group_openid,
                author.get("member_openid") or author.get("id"),
                author.get("username"),
                author.get("member_role"),
                message.get("content", ""),
                message.get("message_type"),
                json.dumps(attachments, ensure_ascii=False) if attachments else None,
                event_type,
                message.get("timestamp"),
                utc_now_iso(),
            ),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def cleanup(self, retention_days: int) -> int:
        if retention_days <= 0:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat(
            timespec="milliseconds"
        )
        cursor = self.connection.execute(
            "DELETE FROM messages WHERE received_at < ?", (cutoff,)
        )
        self.connection.commit()
        return cursor.rowcount

    def count(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) FROM messages").fetchone()
        return int(row[0])

    def close(self) -> None:
        self.connection.close()


class AccessTokenManager:
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token: str | None = None
        self._refresh_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> str:
        async with self._lock:
            if self._token and time.monotonic() < self._refresh_at:
                return self._token
            data = await asyncio.to_thread(self._fetch)
            token = data.get("access_token")
            if not token:
                code = data.get("code", "未知")
                message = data.get("message", "未返回 access_token")
                raise AuthenticationError(f"获取 QQ 访问凭证失败：code={code}, message={message}")
            try:
                expires_in = int(data.get("expires_in", 7200))
            except (TypeError, ValueError):
                expires_in = 7200
            self._token = str(token)
            self._refresh_at = time.monotonic() + max(1, expires_in - 300)
            LOGGER.info("QQ 访问凭证已获取，有效期约 %s 秒", expires_in)
            return self._token

    def _fetch(self) -> dict[str, Any]:
        body = json.dumps(
            {"appId": self.app_id, "clientSecret": self.app_secret}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{API_BASE}/app/getAppAccessToken",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return http_json(request)


def http_json(request: urllib.request.Request) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read(1024).decode("utf-8", errors="replace")
        raise BotError(f"QQ API 返回 HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise BotError(f"无法连接 QQ API：{exc.reason}") from exc
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise BotError("QQ API 返回了非 JSON 数据") from exc
    if not isinstance(data, dict):
        raise BotError("QQ API 返回格式不正确")
    return data


def get_gateway(access_token: str) -> str:
    request = urllib.request.Request(
        f"{API_BASE}/gateway",
        headers={"Authorization": f"QQBot {access_token}"},
    )
    data = http_json(request)
    url = data.get("url")
    if not url:
        raise BotError(f"获取 WebSocket 网关失败：{data}")
    return str(url)


class QQGroupBot:
    def __init__(self, settings: Settings, store: MessageStore):
        self.settings = settings
        self.store = store
        self.tokens = AccessTokenManager(settings.app_id, settings.app_secret)
        self.session_id: str | None = None
        self.sequence: int | None = None
        self._warned_groups: set[str] = set()

    async def run(self) -> None:
        attempt = 0
        while True:
            try:
                await self._connect_once()
                attempt = 0
                delay = 1
            except (AuthenticationError, ConfigurationError, FatalGatewayError):
                raise
            except asyncio.CancelledError:
                raise
            except ConnectionClosed as exc:
                delay = self._handle_close(exc)
                attempt += 1
            except Exception as exc:
                attempt += 1
                delay = min(60, 2 ** min(attempt, 6))
                LOGGER.warning("连接中断：%s；%s 秒后重连", exc, delay)
            await asyncio.sleep(delay)

    def _handle_close(self, exc: ConnectionClosed) -> int:
        code = exc.code
        reason = exc.reason
        fatal_codes = {4001, 4002, 4010, 4011, 4012, 4013, 4014, 4914, 4915}
        if code in fatal_codes:
            hint = ""
            if code == 4014:
                hint = "；请在开放平台确认群聊事件权限"
            raise FatalGatewayError(f"QQ 网关拒绝连接：code={code}, reason={reason}{hint}")
        if code in {4006, 4007}:
            self.session_id = None
            self.sequence = None
        delay = 5
        LOGGER.warning("WebSocket 已关闭：code=%s, reason=%s；%s 秒后重连", code, reason, delay)
        return delay

    async def _connect_once(self) -> None:
        access_token = await self.tokens.get()
        gateway = await asyncio.to_thread(get_gateway, access_token)
        LOGGER.info("正在连接 QQ WebSocket 网关")
        async with websockets.connect(
            gateway,
            ping_interval=None,
            close_timeout=10,
            max_size=10 * 1024 * 1024,
        ) as websocket:
            hello_raw = await asyncio.wait_for(websocket.recv(), timeout=20)
            hello = json.loads(hello_raw)
            if hello.get("op") != 10:
                raise BotError(f"网关首条消息不是 Hello：{hello}")
            interval_ms = int((hello.get("d") or {}).get("heartbeat_interval", 45000))
            await self._identify_or_resume(websocket, access_token)
            heartbeat = asyncio.create_task(self._heartbeat_loop(websocket, interval_ms / 1000))
            try:
                async for raw in websocket:
                    payload = json.loads(raw)
                    if isinstance(payload.get("s"), int):
                        self.sequence = payload["s"]
                    op = payload.get("op")
                    if op == 0:
                        await self._dispatch(payload)
                    elif op == 1:
                        await self._send_heartbeat(websocket)
                    elif op == 7:
                        LOGGER.info("网关要求重新连接")
                        return
                    elif op == 9:
                        LOGGER.warning("会话已失效，将重新鉴权")
                        self.session_id = None
                        self.sequence = None
                        return
            finally:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass

    async def _identify_or_resume(self, websocket: Any, access_token: str) -> None:
        token = f"QQBot {access_token}"
        if self.session_id and self.sequence is not None:
            payload = {
                "op": 6,
                "d": {
                    "token": token,
                    "session_id": self.session_id,
                    "seq": self.sequence,
                },
            }
        else:
            payload = {
                "op": 2,
                "d": {
                    "token": token,
                    "intents": GROUP_AND_C2C_EVENT,
                    "shard": [0, 1],
                    "properties": {
                        "$os": platform.system().lower(),
                        "$browser": "qq-group-sqlite-bot",
                        "$device": "qq-group-sqlite-bot",
                    },
                },
            }
        await websocket.send(json.dumps(payload))

    async def _heartbeat_loop(self, websocket: Any, interval_seconds: float) -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            await self._send_heartbeat(websocket)

    async def _send_heartbeat(self, websocket: Any) -> None:
        await websocket.send(json.dumps({"op": 1, "d": self.sequence}))

    async def _dispatch(self, payload: dict[str, Any]) -> None:
        event_type = str(payload.get("t") or "")
        data = payload.get("d") or {}
        if event_type == "READY":
            self.session_id = data.get("session_id")
            user = data.get("user") or {}
            LOGGER.info(
                "机器人已上线：%s；会话 ID 已建立",
                user.get("username") or user.get("id") or "未知机器人",
            )
            return
        if event_type == "RESUMED":
            LOGGER.info("WebSocket 会话已恢复")
            return
        if event_type in {"GROUP_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE"}:
            # SQLite 单次写入很快，保持在线程内也避免跨线程共享连接。
            self._save_group_message(data, event_type)
            return
        if event_type == "GROUP_ADD_ROBOT":
            group_openid = str(data.get("group_openid") or "")
            LOGGER.info("机器人已加入群：group_openid=%s", group_openid)
            if not self.settings.allows(group_openid):
                LOGGER.info("请把该 group_openid 加入 QQ_ALLOWED_GROUPS 后重启")
            return
        if event_type == "GROUP_DEL_ROBOT":
            LOGGER.info("机器人已被移出群：group_openid=%s", data.get("group_openid"))
            return
        if event_type == "GROUP_MSG_RECEIVE":
            LOGGER.info("群消息接收已开启：group_openid=%s", data.get("group_openid"))
            return
        LOGGER.debug("忽略事件：%s", event_type)

    def _save_group_message(self, message: dict[str, Any], event_type: str) -> None:
        group_openid = str(message.get("group_openid") or "")
        if not self.settings.allows(group_openid):
            if group_openid not in self._warned_groups:
                self._warned_groups.add(group_openid)
                LOGGER.info(
                    "发现未授权群 group_openid=%s；消息未保存。"
                    "如需处理，请加入 QQ_ALLOWED_GROUPS 并重启",
                    group_openid,
                )
            return
        inserted = self.store.save(message, event_type)
        if inserted:
            LOGGER.info(
                "已保存群消息：group=%s, message=%s, 当前总数=%s",
                group_openid,
                message.get("id"),
                self.store.count(),
            )
        else:
            LOGGER.debug("重复消息已忽略：%s", message.get("id"))


def setup_logging() -> None:
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    level_name = os.getenv("QQ_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        log_dir / "bot.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logging.basicConfig(level=level, handlers=[console, file_handler])


async def async_main() -> None:
    settings = Settings.from_env()
    store = MessageStore(settings.database_path)
    try:
        deleted = store.cleanup(settings.retention_days)
        if deleted:
            LOGGER.info("已按保留策略删除 %s 条过期消息", deleted)
        if settings.allow_all_groups:
            LOGGER.warning("QQ_ALLOWED_GROUPS='*'：机器人加入的所有群都会保存消息")
        elif not settings.allowed_groups:
            LOGGER.warning(
                "当前为群 ID 发现模式：不会保存消息。"
                "启动后把机器人加入群，记录 group_openid 并配置白名单"
            )
        else:
            LOGGER.info("已配置 %s 个授权群", len(settings.allowed_groups))
        bot = QQGroupBot(settings, store)
        await bot.run()
    finally:
        store.close()


def main() -> None:
    setup_logging()
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        LOGGER.info("机器人已停止")
    except BotError as exc:
        LOGGER.error("启动失败：%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
