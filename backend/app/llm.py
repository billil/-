"""大模型调用封装（OpenAI 兼容接口）。

对外只暴露 chat_json(messages)：
- temperature 固定 0，超时 30 秒（可在 config 中用 LLM_TIMEOUT 覆盖）
- 模型输出必须能 json.loads，解析失败自动重试 1 次
- 两次都失败抛 LLMFormatError

地址/密钥/模型名在每次调用时从环境变量读取：
LLM_BASE_URL、LLM_API_KEY、LLM_MODEL。
"""

import json
import os
import socket
import urllib.error
import urllib.request
from typing import Any

from .config import LLM_TEMPERATURE, LLM_TIMEOUT
from .errors import AppError, LLM_TIMEOUT as LLM_TIMEOUT_CODE


class LLMFormatError(RuntimeError):
    """大模型返回内容无法解析为 JSON（已重试 1 次仍失败）。"""


def _llm_settings() -> tuple[str, str, str]:
    """读取 LLM 配置。每次调用时读环境变量，便于测试替换。"""
    base_url = os.environ.get("LLM_BASE_URL", "").strip().rstrip("/")
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    model = os.environ.get("LLM_MODEL", "").strip()
    if not base_url or not model:
        raise RuntimeError(
            "大模型配置缺失：请设置环境变量 LLM_BASE_URL、LLM_API_KEY、LLM_MODEL"
        )
    return base_url, api_key, model


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
    """单次调用 chat/completions，返回模型原始文本。

    独立成函数便于测试中 monkeypatch（不发真实网络请求）。
    """
    base_url, api_key, model = _llm_settings()
    url = (
        base_url
        if base_url.endswith("/chat/completions")
        else base_url + "/chat/completions"
    )

    payload = json.dumps(
        {
            "model": model,
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
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=timeout or LLM_TIMEOUT) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    return body["choices"][0]["message"]["content"]


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
