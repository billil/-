"""D 位 SQLite 只读适配层（P0-1 联调）。

把成员 D 的 `messages` / `groups` 表映射成 B 位 facade 需要的 Candidate，
并落实权限收口：只读授权群、只读白名单内消息。

字段映射（D → B）：
- `messages.id`          → `msg_id`
- `messages.content`     → `content`
- `messages.sent_at`     → `send_time`（统一成 `YYYY-MM-DD HH:MM:SS`）
- `messages.group_label` → `group_id`（D 导入时 group_key 与 group_label 相同）
- `messages.revision`    → `version`
- `metadata.sender`      → `sender`（D 未入库发送者，通常为 None）
- `metadata.category`    → `category`
- `metadata.file_name` / `metadata.fields.文件` → `file_hint`

D 侧没有 `msg_status` / `edited_at` 列：删除即删行，故默认按 `normal` 处理；
不做任何推测。时间范围只在 `time_field=send_time` 时过滤，`event_time` 无对应列。

本模块只读，不写 D 库；不引入新依赖。
"""

import json
import os
import re
import sqlite3
from datetime import datetime
from typing import Optional

# 已带秒的 D 时间字符串直接复用，避免无谓的时区换算
_DT_SECOND_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
_DT_MINUTE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

VALID_CATEGORIES = {"event", "task", "file"}
VALID_STATUS = {"normal", "edited", "revoked"}


def _connect(db_path: str) -> sqlite3.Connection:
    """以只读意图打开 D 的 SQLite 文件；不建表、不写入。"""
    if not os.path.isfile(db_path):
        raise sqlite3.OperationalError(f"D 数据库不存在：{db_path}")
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _normalize_send_time(value) -> Optional[str]:
    """把 D 的 sent_at 规整为 B 约定的 `YYYY-MM-DD HH:MM:SS`。

    - 已带秒的字符串原样返回；
    - 只有分钟时补 `:00`；
    - 带时区 ISO 时间转为本机时区后再格式化；
    - 无法解析时原样返回，交给上层决定，不猜值。
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if _DT_SECOND_RE.match(text):
        return text
    if _DT_MINUTE_RE.match(text):
        return text + ":00"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _metadata(row: sqlite3.Row) -> dict:
    """解析 D 的 metadata JSON；坏数据按空字典处理，不抛异常。"""
    try:
        value = json.loads(row["metadata"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _row_to_candidate(row: sqlite3.Row) -> dict:
    """把一行 D 消息映射成 B 的 Candidate。"""
    meta = _metadata(row)
    status = meta.get("msg_status")
    fields = meta.get("fields")
    fields = fields if isinstance(fields, dict) else {}
    category = meta.get("category")
    file_hint = meta.get("file_name") or fields.get("文件")
    return {
        "msg_id": row["id"],
        "content": row["content"] or "",
        "sender": meta.get("sender"),
        "send_time": _normalize_send_time(row["sent_at"]),
        "group_id": row["group_label"],
        "category": category if category in VALID_CATEGORIES else None,
        "file_hint": file_hint if isinstance(file_hint, str) and file_hint else None,
        "score": 0.0,
        "sources": ["d_sqlite"],
        "version": int(row["revision"] or 1),
        "msg_status": status if status in VALID_STATUS else "normal",
        "edited_at": meta.get("edited_at"),
    }


def authorized_group_keys(db_path: str, workspace: str) -> list[str]:
    """返回 D 侧该数据空间内 `authorized=1` 的群 key，供调用方构造 group_scope。

    Args:
        db_path: D 的 SQLite 文件路径。
        workspace: D 的数据空间标识（由令牌映射得到，不能来自用户输入）。

    Returns:
        按 group_key 升序的授权群列表；无授权时返回空列表。

    Raises:
        sqlite3.Error: 数据库不存在或查询失败。
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT group_key FROM groups WHERE workspace=? AND authorized=1 "
            "ORDER BY group_key",
            (workspace,),
        ).fetchall()
        return [row["group_key"] for row in rows]
    finally:
        conn.close()


class DStoreCandidateProvider:
    """把 D 的 SQLite 候选适配成 facade 可调用的 candidate_provider。

    Args:
        db_path: D 的 SQLite 文件路径。
        workspace: D 的数据空间标识。
        group_scope: 默认授权群列表；filters 里带 group_scope 时以 filters 为准。
        allowed_msg_ids: 默认消息白名单；filters 里带 allowed_msg_ids 时以 filters 为准。
        default_limit: filters 未给 `_limit` 时的返回上限，默认 20。

    Raises:
        调用时读取库失败会抛 `sqlite3.Error`，facade 不吞。
    """

    def __init__(
        self,
        db_path: str,
        workspace: str,
        group_scope: Optional[list[str]] = None,
        allowed_msg_ids: Optional[list[str]] = None,
        default_limit: int = 20,
    ) -> None:
        self.db_path = db_path
        self.workspace = workspace
        self.group_scope = list(group_scope) if group_scope is not None else None
        self.allowed_msg_ids = (
            list(allowed_msg_ids) if allowed_msg_ids is not None else None
        )
        self.default_limit = default_limit
        self.calls = 0

    def _resolve_scope(self, filters: dict) -> tuple[Optional[list[str]], Optional[list[str]]]:
        """filters 优先，其次构造参数；两者都没有则视为无权限。"""
        scope = filters.get("group_scope")
        if scope is None:
            scope = self.group_scope
        allowed = filters.get("allowed_msg_ids")
        if allowed is None:
            allowed = self.allowed_msg_ids
        return (
            list(scope) if scope is not None else None,
            list(allowed) if allowed is not None else None,
        )

    def __call__(self, filters: dict) -> list[dict]:
        """按 filters 从 D 库取候选，返回 B Candidate 列表。

        filters 至少需要 group_scope / allowed_msg_ids 之一；两个都缺时返回空列表，
        不把"没有权限参数"当成"可以查全量"。
        """
        filters = filters or {}
        self.calls += 1
        scope, allowed = self._resolve_scope(filters)
        if scope is None and allowed is None:
            return []
        if scope is not None and not scope:
            return []
        if allowed is not None and not allowed:
            return []

        sql = (
            "SELECT id, content, group_label, sent_at, metadata, revision "
            "FROM messages WHERE workspace=?"
        )
        args: list = [self.workspace]
        if scope is not None:
            sql += " AND group_label IN (" + ",".join("?" * len(scope)) + ")"
            args.extend(scope)
        if allowed is not None:
            sql += " AND id IN (" + ",".join("?" * len(allowed)) + ")"
            args.extend(allowed)

        conn = _connect(self.db_path)
        try:
            rows = conn.execute(sql, args).fetchall()
        finally:
            conn.close()

        keywords = [
            word for word in (filters.get("keywords") or []) if isinstance(word, str) and word
        ]
        time_range = filters.get("time_range")
        time_field = filters.get("time_field")

        candidates: list[dict] = []
        for row in rows:
            candidate = _row_to_candidate(row)
            if time_range and time_field == "send_time":
                start = time_range.get("start")
                end = time_range.get("end")
                send_time = candidate["send_time"] or ""
                if start and send_time < start:
                    continue
                if end and send_time > end:
                    continue
            if keywords:
                hits = sum(1 for word in keywords if word in candidate["content"])
                if hits == 0:
                    continue
                candidate["score"] = round(hits / len(keywords), 4)
            candidates.append(candidate)

        candidates.sort(
            key=lambda item: (
                -item["score"],
                item["send_time"] or "",
                item["msg_id"],
            )
        )
        limit = filters.get("_limit") or self.default_limit
        return candidates[: int(limit)]
