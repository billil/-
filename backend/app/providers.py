"""大模型 provider 层：把「一次调用 → 一段文本」的实现与上层流程解耦。

两条通路，用 `LLM_PROVIDER` 选择：

| 值 | 上游 | 需要的环境变量 |
|----|------|----------------|
| `openai`（默认） | 任意 OpenAI 兼容接口，非流式 JSON | `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` |
| `workbuddy` | WorkBuddy 云服务免密钥大模型网关 | `WB_ENDPOINT` / `WB_PUBLISHABLE_KEY` / `WB_MODEL` |

## workbuddy 网关协议

依据官方 SDK `@tencent-ai/workbuddy-cloud-sdk`（`lib/chunk-*.js`）整理，不要凭记忆改动：

- 基址：`{WB_ENDPOINT}/.cloud/llm`
- 对话：`POST /chat/completions`
  - 请求头 `x-wb-webapp-access-key: <publishableKey>`（SDK 里的 `PUBLISHABLE_KEY_HEADER`）
  - 请求体必须 `stream: true`；**网关不提供非流式响应**，非流式会被直接拒绝
  - 响应是 SSE：`data:` 帧为 `ChatCompletionChunk`，`data: [DONE]` 表示正常结束，
    `event: error` 或非 2xx 表示失败
- 模型目录：`GET /models`
- publishableKey 本身不带权限，服务端按 **Origin 精确匹配**校验，所以默认把 endpoint 的
  `协议://域名` 作为 `Origin` 发出去（可用 `WB_ORIGIN` 覆盖）

流式片段在这里被拼回完整文本，因此上层 `llm.chat_json` 仍然是一次调用拿一段字符串。
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from .config import LLM_TEMPERATURE, LLM_TIMEOUT

PROVIDER_OPENAI = "openai"
PROVIDER_WORKBUDDY = "workbuddy"

_OPENAI_ALIASES = {PROVIDER_OPENAI, "openai-compatible", "compatible"}
_WORKBUDDY_ALIASES = {PROVIDER_WORKBUDDY, "wb"}

WORKBUDDY_CHAT_PATH = "/.cloud/llm/chat/completions"
WORKBUDDY_MODELS_PATH = "/.cloud/llm/models"
# 官方 SDK 的 PUBLISHABLE_KEY_HEADER，见模块文档
PUBLISHABLE_KEY_HEADER = "x-wb-webapp-access-key"


class ProviderUnavailable(RuntimeError):
    """provider 不可用：配置缺失 / 网关拒绝 / 流中断。

    message 已是面向用户的中文说明，可直接进 `AppError`；不包含密钥、堆栈等内部细节。
    """

    def __init__(self, message: str, *, code: str | None = None, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def provider_name() -> str:
    """当前选中的 provider（小写）；未设置时默认 openai 兼容接口。"""
    return (_env("LLM_PROVIDER") or PROVIDER_OPENAI).lower()


# ---------------------------------------------------------------------------
# OpenAI 兼容接口（原有通路，行为保持不变）
# ---------------------------------------------------------------------------


class OpenAICompatibleProvider:
    name = PROVIDER_OPENAI

    def __init__(self) -> None:
        base_url = _env("LLM_BASE_URL").rstrip("/")
        api_key = _env("LLM_API_KEY")
        model = _env("LLM_MODEL")
        if not base_url or not model:
            raise ProviderUnavailable(
                "大模型未配置：请设置 LLM_BASE_URL、LLM_API_KEY、LLM_MODEL"
                "（或设 LLM_PROVIDER=workbuddy 走 WorkBuddy 云服务）",
                code="config_missing",
            )
        self.base_url = base_url
        self.api_key = api_key
        self.model = model

    def endpoint(self) -> str:
        return self.base_url

    def complete(self, messages: list[dict], timeout: float | None = None) -> str:
        url = (
            self.base_url
            if self.base_url.endswith("/chat/completions")
            else self.base_url + "/chat/completions"
        )
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "temperature": LLM_TEMPERATURE,
            },
            ensure_ascii=False,
        ).encode("utf-8")

        request = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        with _open(request, timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderUnavailable(
                "大模型返回结构异常（缺少 choices[0].message.content）", code="bad_response"
            ) from exc


# ---------------------------------------------------------------------------
# WorkBuddy 云服务网关
# ---------------------------------------------------------------------------


class WorkBuddyProvider:
    name = PROVIDER_WORKBUDDY

    def __init__(self) -> None:
        endpoint = _env("WB_ENDPOINT").rstrip("/")
        key = _env("WB_PUBLISHABLE_KEY")
        model = _env("WB_MODEL") or _env("LLM_MODEL")
        missing = [
            name
            for name, value in (
                ("WB_ENDPOINT", endpoint),
                ("WB_PUBLISHABLE_KEY", key),
                ("WB_MODEL", model),
            )
            if not value
        ]
        if missing:
            raise ProviderUnavailable(
                "WorkBuddy 大模型未配置：缺少 " + "、".join(missing), code="config_missing"
            )
        self.endpoint = endpoint
        self.publishable_key = key
        self.model = model

    def endpoint_url(self) -> str:
        return self.endpoint

    def _headers(self, *, stream: bool) -> dict[str, str]:
        headers = {
            "Accept": "text/event-stream" if stream else "application/json",
            "Content-Type": "application/json",
            PUBLISHABLE_KEY_HEADER: self.publishable_key,
        }
        origin = _env("WB_ORIGIN") or _origin_of(self.endpoint)
        if origin:
            headers["Origin"] = origin
        return headers

    def list_models(self, timeout: float | None = None) -> list[dict]:
        """拉取该应用可用的模型目录（诊断用，不参与业务链路）。"""
        request = urllib.request.Request(
            self.endpoint + WORKBUDDY_MODELS_PATH,
            headers={"Accept": "application/json", PUBLISHABLE_KEY_HEADER: self.publishable_key},
            method="GET",
        )
        with _open(request, timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        raw = body if isinstance(body, list) else body.get("data") if isinstance(body, dict) else []
        return [m for m in raw or [] if isinstance(m, dict)]

    def complete(self, messages: list[dict], timeout: float | None = None) -> str:
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "stream": True,  # 网关只支持流式，见模块文档
                "temperature": LLM_TEMPERATURE,
            },
            ensure_ascii=False,
        ).encode("utf-8")

        request = urllib.request.Request(
            self.endpoint + WORKBUDDY_CHAT_PATH,
            data=payload,
            headers=self._headers(stream=True),
            method="POST",
        )

        with _open(request, timeout) as resp:
            text = _accumulate_sse(resp)

        if not text.strip():
            raise ProviderUnavailable("WorkBuddy 大模型返回空内容", code="empty_response")
        return text


# ---------------------------------------------------------------------------
# 工厂 & 状态
# ---------------------------------------------------------------------------


def get_provider() -> OpenAICompatibleProvider | WorkBuddyProvider:
    """按 `LLM_PROVIDER` 构造 provider；每次调用都读环境变量，便于测试替换。"""
    name = provider_name()
    if name in _WORKBUDDY_ALIASES:
        return WorkBuddyProvider()
    if name in _OPENAI_ALIASES:
        return OpenAICompatibleProvider()
    raise ProviderUnavailable(
        f"未知的 LLM_PROVIDER：{name}（可选 openai / workbuddy）", code="config_invalid"
    )


def provider_status() -> dict[str, Any]:
    """给 `/api/llm/status` 用的只读状态，不含任何密钥。"""
    name = provider_name()
    if name in _WORKBUDDY_ALIASES:
        required = (
            ("WB_ENDPOINT", _env("WB_ENDPOINT")),
            ("WB_PUBLISHABLE_KEY", _env("WB_PUBLISHABLE_KEY")),
            ("WB_MODEL", _env("WB_MODEL") or _env("LLM_MODEL")),
        )
    else:
        required = (
            ("LLM_BASE_URL", _env("LLM_BASE_URL")),
            ("LLM_MODEL", _env("LLM_MODEL")),
        )

    known = name in _WORKBUDDY_ALIASES or name in _OPENAI_ALIASES
    missing = [env_name for env_name, value in required if not value]
    info: dict[str, Any] = {
        "provider": name,
        "model": dict(required).get("WB_MODEL") or _env("LLM_MODEL"),
        "configured": known and not missing,
        "missing": missing,
        "endpoint": _env("WB_ENDPOINT") if name in _WORKBUDDY_ALIASES else _env("LLM_BASE_URL"),
    }
    if not known:
        info["reason"] = f"未知的 LLM_PROVIDER：{name}（可选 openai / workbuddy）"
    return info


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _origin_of(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return ""


def _open(request: urllib.request.Request, timeout: float | None):
    """统一处理连接层异常：超时原样抛出（上层映射 2001），其余转成用户可读的不可用。"""
    try:
        return urllib.request.urlopen(request, timeout=timeout or LLM_TIMEOUT)
    except urllib.error.HTTPError as exc:
        raise ProviderUnavailable(
            _http_error_message(exc), code="http_error", status=exc.code
        ) from exc
    except (TimeoutError, socket.timeout):
        raise
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout)):
            raise
        raise ProviderUnavailable("大模型服务连接失败，请检查网络或服务地址", code="network_error") from exc


def _http_error_message(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - 读 body 失败不影响错误分类
        raw = ""
    code, message = "", ""
    try:
        parsed = json.loads(raw)
        err = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(err, dict):
            code = str(err.get("code") or "")
            message = str(err.get("message") or "")
        elif isinstance(parsed, dict):
            code = str(parsed.get("code") or "")
            message = str(parsed.get("message") or "")
    except (json.JSONDecodeError, ValueError):
        message = raw.strip()[:200]
    return _user_message(code, message, status=exc.code)


def _user_message(code: str, message: str, status: int | None = None) -> str:
    """把网关错误码折叠成固定中文提示（错误码前缀语义见官方 SDK 文档）。"""
    if code.startswith("auth_"):
        return "WorkBuddy 云服务凭据无效或来源不匹配，请核对 WB_ENDPOINT / WB_PUBLISHABLE_KEY"
    if code.startswith("quota_"):
        return "WorkBuddy 模型额度不足或请求过于频繁，请稍后重试"
    if code.startswith("request_"):
        return f"大模型请求未被接受：{message}" if message else "大模型请求参数不被接受"
    if code.startswith(("gateway_", "model_", "internal_")):
        return "大模型服务暂不可用，请稍后重试"
    if status == 401 or status == 403:
        return "WorkBuddy 云服务凭据无效或来源不匹配，请核对 WB_ENDPOINT / WB_PUBLISHABLE_KEY"
    if status == 429:
        return "大模型请求过于频繁，请稍后重试"
    if status and status >= 500:
        return "大模型服务暂不可用，请稍后重试"
    return message or "大模型服务暂不可用，请稍后重试"


def _sse_error_message(payload: str) -> str:
    try:
        parsed = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return payload.strip()[:200] or "大模型流式响应返回错误"
    err = parsed.get("error") if isinstance(parsed, dict) else None
    if not isinstance(err, dict):
        err = parsed if isinstance(parsed, dict) else {}
    return _user_message(str(err.get("code") or ""), str(err.get("message") or ""))


def _accumulate_sse(resp) -> str:
    """把 SSE 流里的 `delta.content` 片段拼成完整文本。

    只取正文，不取 `reasoning_content`（思维链不属于可引用内容）。
    """
    chunks: list[str] = []
    event: str | None = None
    data_lines: list[str] = []
    done = False

    def flush() -> None:
        nonlocal event, done
        payload = "\n".join(data_lines).strip()
        data_lines.clear()
        current_event, event = event, None
        if not payload:
            return
        if payload == "[DONE]":
            done = True
            return
        if current_event == "error":
            raise ProviderUnavailable(_sse_error_message(payload), code="stream_error")
        try:
            obj = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            return
        if not isinstance(obj, dict):
            return
        if isinstance(obj.get("error"), dict):
            raise ProviderUnavailable(_sse_error_message(payload), code="stream_error")
        choices = obj.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            return
        delta = choices[0].get("delta") or {}
        piece = delta.get("content")
        if isinstance(piece, str) and piece:
            chunks.append(piece)

    for raw_line in resp:
        line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
        if line == "":
            flush()
            if done:
                break
            continue
        if line.startswith(":"):  # SSE 注释帧（心跳）
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event = value.strip()
        elif field == "data":
            data_lines.append(value)
    else:
        flush()  # 流结束但没收到空行收尾

    if not done:
        raise ProviderUnavailable("大模型流式响应中断，请稍后重试", code="stream_interrupted")
    return "".join(chunks)
