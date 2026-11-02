"""大模型调用封装（provider 无关）。

对外只暴露 chat_json(messages)：
- temperature 固定 0，超时 30 秒（可在 config 中用 LLM_TIMEOUT 覆盖）
- 模型输出必须能 json.loads，解析失败自动重试 1 次
- 两次都失败抛 LLMFormatError

走哪家上游由 `LLM_PROVIDER` 决定，实现见 app.providers：
- `openai`（默认）：OpenAI 兼容接口，环境变量 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
- `workbuddy`：WorkBuddy 云服务免密钥大模型网关，环境变量 WB_ENDPOINT / WB_PUBLISHABLE_KEY / WB_MODEL

配置缺失或网关拒绝时抛 AppError(LLM_UNAVAILABLE)，上层按 2003 返回。
"""

import json
from contextvars import ContextVar
import socket
import time
import urllib.error
from typing import Any

from .errors import (
    AppError,
    LLM_TIMEOUT as LLM_TIMEOUT_CODE,
    LLM_UNAVAILABLE,
)
from .providers import ProviderUnavailable, get_provider

CALL_STATS = ContextVar('qunyi_b_call_stats', default=None)


class LLMFormatError(RuntimeError):
    """大模型返回内容无法解析为 JSON（已重试 1 次仍失败）。"""


def _strip_code_fence(text: str) -> str:
    """去掉模型偶尔包裹的 ```json ... ``` 代码块标记。"""
    s = (text or "").strip()
    if s.startswith("```"):
        # 去掉首行（``` 或 ```json）
        parts = s.split("\n", 1)
        s = parts[1] if len(parts) > 1 else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[: -3]
    return s.strip()


def _call_llm(messages: list[dict], timeout: float | None = None) -> str:
    """单次调用上游模型，返回模型原始文本。

    provider 的装配与协议细节都在 app.providers；这里只做错误归类，
    独立成函数便于测试中 monkeypatch（不发真实网络请求）。
    """
    try:
        stats = CALL_STATS.get()
        if stats is not None:
            if stats.get('max_calls') is not None and stats['attempted'] >= stats['max_calls']:
                raise RuntimeError('B_CALL_LIMIT')
            remaining = stats.get('deadline', float('inf')) - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('query deadline exceeded')
            timeout = min(remaining, stats.get('timeout', remaining), timeout or remaining)
            stats['attempted'] += 1
        result = get_provider().complete(messages, timeout=timeout)
        if stats is not None:
            stats['responses'] += 1
        return result
    except ProviderUnavailable as exc:
        # 配置缺失 / 网关拒绝 / 流中断：给前端一句能看懂的中文，不暴露堆栈
        raise AppError(LLM_UNAVAILABLE, str(exc) or None) from exc


def chat_json(messages: list[dict]) -> Any:
    """调用大模型并解析为 JSON，失败重试 1 次，仍失败抛 LLMFormatError。"""
    last_error: Exception | None = None
    last_text = ""

    for _ in range(2):  # 首次 + 重试 1 次
        try:
            last_text = _call_llm(messages)
        except (TimeoutError, socket.timeout) as exc:
            # 超时不重试，直接按 LLM_TIMEOUT 返回，避免用户长时间等待
            raise AppError(LLM_TIMEOUT_CODE) from exc
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout)):
                raise AppError(LLM_TIMEOUT_CODE) from exc
            raise
        try:
            return json.loads(_strip_code_fence(last_text))
        except Exception as exc:  # JSONDecodeError / TypeError 等
            last_error = exc

    raise LLMFormatError(
        f"大模型输出不是合法 JSON（已重试 1 次）：{last_error}；"
        f"原文片段={last_text[:200]!r}"
    )

