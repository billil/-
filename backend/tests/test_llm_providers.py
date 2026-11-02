"""provider 层测试：不发真实网络请求，全部用假的 urlopen。

覆盖两件事：
1. WorkBuddy 网关的协议细节（URL / 请求头 / stream=true / SSE 拼装）不能写错；
2. provider 失败要折叠成 2003（LLM_UNAVAILABLE），而不是 500 内部错误。
"""

import io
import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app.llm as llm_module
import app.providers as providers
from app.errors import AppError, LLM_UNAVAILABLE, MESSAGES
from app.llm import LLMFormatError, chat_json
from app.providers import (
    ProviderUnavailable,
    WorkBuddyProvider,
    get_provider,
    provider_status,
)

ENDPOINT = "https://qunyi-demo.workbuddy.link"
KEY = "wb_pk_test_0123456789"


class FakeResponse:
    """最小可用的 urlopen 返回值：支持 with / 迭代行 / read。"""

    def __init__(self, lines: list[bytes], status: int = 200):
        self._lines = lines
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._lines)

    def read(self) -> bytes:
        return b"".join(self._lines)

    def close(self):
        pass


def _sse(*frames: str) -> FakeResponse:
    """按 SSE 规范把 data 帧拼成响应体（每帧以空行结束）。"""
    out: list[bytes] = []
    for frame in frames:
        for line in frame.split("\n"):
            out.append(line.encode("utf-8") + b"\n")
        out.append(b"\n")
    return FakeResponse(out)


def _chunk(content: str, role: str | None = None) -> str:
    delta = {}
    if role:
        delta["role"] = role
    delta["content"] = content
    return "data: " + json.dumps({"choices": [{"delta": delta}]}, ensure_ascii=False)


@pytest.fixture
def wb_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "workbuddy")
    monkeypatch.setenv("WB_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("WB_PUBLISHABLE_KEY", KEY)
    monkeypatch.setenv("WB_MODEL", "wb-model-test")
    monkeypatch.delenv("WB_ORIGIN", raising=False)
    return monkeypatch


def test_default_provider_is_openai_compatible(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")

    provider = get_provider()
    assert provider.name == "openai"
    assert provider.model == "deepseek-chat"


def test_workbuddy_missing_config_is_reported(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "workbuddy")
    monkeypatch.delenv("WB_ENDPOINT", raising=False)
    monkeypatch.delenv("WB_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("WB_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    with pytest.raises(ProviderUnavailable) as excinfo:
        get_provider()
    assert excinfo.value.code == "config_missing"
    assert "WB_ENDPOINT" in str(excinfo.value)


def test_workbuddy_request_shape(wb_env):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = {k.lower(): v for k, v in request.header_items()}
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _sse(_chunk("", role="assistant"), _chunk('{"ok": true}'), "data: [DONE]")

    wb_env.setattr(providers.urllib.request, "urlopen", fake_urlopen)

    text = get_provider().complete(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    )

    assert text == '{"ok": true}'
    assert captured["url"] == ENDPOINT + "/.cloud/llm/chat/completions"
    # 官方 SDK 的 publishableKey 头，写错会 401
    assert captured["headers"]["x-wb-webapp-access-key"] == KEY
    assert captured["headers"]["accept"] == "text/event-stream"
    # 服务端按 Origin 精确匹配校验，默认取 endpoint 的 协议://域名
    assert captured["headers"]["origin"] == ENDPOINT
    body = captured["body"]
    assert body["stream"] is True  # 网关不支持非流式
    assert body["model"] == "wb-model-test"
    assert body["temperature"] == 0
    assert body["messages"][0]["role"] == "system"


def test_workbuddy_origin_can_be_overridden(wb_env):
    captured = {}
    wb_env.setenv("WB_ORIGIN", "https://custom.example.com")

    def fake_urlopen(request, timeout=None):
        captured["headers"] = {k.lower(): v for k, v in request.header_items()}
        return _sse("data: [DONE]")

    wb_env.setattr(providers.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(ProviderUnavailable):  # 空内容
        get_provider().complete([{"role": "user", "content": "u"}])
    assert captured["headers"]["origin"] == "https://custom.example.com"


def test_sse_collects_only_content_deltas(wb_env):
    frames = (
        _chunk("", role="assistant"),          # 角色初始化帧，不渲染
        _chunk("火"),                           # 正文片段
        ': keep-alive',                         # SSE 注释帧（心跳）
        'data: {"choices":[{"delta":{"reasoning_content":"想一想"}}]}',  # 思维链不算正文
        _chunk("锅局"),                          # 正文片段
    )

    def fake_urlopen(request, timeout=None):
        return _sse(*frames, "data: [DONE]")

    wb_env.setattr(providers.urllib.request, "urlopen", fake_urlopen)

    assert get_provider().complete([{"role": "user", "content": "u"}]) == "火锅局"


def test_sse_error_event_maps_to_friendly_message(wb_env):
    def fake_urlopen(request, timeout=None):
        return _sse(
            'event: error\ndata: {"error":{"code":"auth_invalid_key","message":"bad key"}}'
        )

    wb_env.setattr(providers.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(ProviderUnavailable) as excinfo:
        get_provider().complete([{"role": "user", "content": "u"}])
    assert "凭据" in str(excinfo.value)
    assert "bad key" not in str(excinfo.value)  # 不把上游原文透给用户


def test_sse_without_done_is_interrupted(wb_env):
    def fake_urlopen(request, timeout=None):
        return _sse(_chunk("半截回答"))

    wb_env.setattr(providers.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(ProviderUnavailable) as excinfo:
        get_provider().complete([{"role": "user", "content": "u"}])
    assert excinfo.value.code == "stream_interrupted"


def test_http_401_maps_to_credential_message(wb_env):
    body = io.BytesIO(json.dumps({"error": {"code": "auth_origin_mismatch"}}).encode())

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, body)

    wb_env.setattr(providers.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(ProviderUnavailable) as excinfo:
        get_provider().complete([{"role": "user", "content": "u"}])
    assert excinfo.value.status == 401
    assert "来源不匹配" in str(excinfo.value)


def test_llm_maps_provider_failure_to_2003(monkeypatch):
    def fake_provider():
        raise ProviderUnavailable("WorkBuddy 大模型未配置：缺少 WB_ENDPOINT")

    monkeypatch.setattr(llm_module, "get_provider", fake_provider)

    with pytest.raises(AppError) as excinfo:
        chat_json([{"role": "user", "content": "hi"}])
    assert excinfo.value.code == LLM_UNAVAILABLE
    assert excinfo.value.http_status == 503
    assert excinfo.value.message == "WorkBuddy 大模型未配置：缺少 WB_ENDPOINT"
    assert MESSAGES[LLM_UNAVAILABLE]  # 兜底文案仍存在


def test_llm_does_not_retry_when_provider_unavailable(monkeypatch):
    calls = {"n": 0}

    def fake_provider():
        calls["n"] += 1
        raise ProviderUnavailable("额度不足")

    monkeypatch.setattr(llm_module, "get_provider", fake_provider)

    with pytest.raises(AppError):
        chat_json([{"role": "user", "content": "hi"}])
    assert calls["n"] == 1  # 不可用不重试，避免叠加等待


def test_workbuddy_still_retries_bad_json(wb_env):
    """provider 正常但输出不是 JSON → 仍按老规则重试一次，抛 LLMFormatError。"""
    calls = {"n": 0}

    def fake_urlopen(request, timeout=None):
        calls["n"] += 1
        return _sse(_chunk("这不是 JSON"), "data: [DONE]")

    wb_env.setattr(providers.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(LLMFormatError):
        chat_json([{"role": "user", "content": "hi"}])
    assert calls["n"] == 2


def test_provider_status_never_leaks_key(wb_env):
    status = provider_status()
    assert status["provider"] == "workbuddy"
    assert status["configured"] is True
    assert status["model"] == "wb-model-test"
    assert status["missing"] == []
    assert KEY not in json.dumps(status, ensure_ascii=False)


def test_provider_status_reports_missing(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "workbuddy")
    monkeypatch.delenv("WB_ENDPOINT", raising=False)
    monkeypatch.delenv("WB_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("WB_MODEL", raising=False)

    status = provider_status()
    assert status["configured"] is False
    assert set(status["missing"]) == {"WB_ENDPOINT", "WB_PUBLISHABLE_KEY", "WB_MODEL"}


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    with pytest.raises(ProviderUnavailable) as excinfo:
        get_provider()
    assert excinfo.value.code == "config_invalid"


def test_openai_provider_keeps_authorization_header(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")

    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = {k.lower(): v for k, v in request.header_items()}
        return FakeResponse([json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode()])

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)

    assert get_provider().complete([{"role": "user", "content": "u"}]) == "{}"
    assert captured["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer sk-test"
