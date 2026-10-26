"""异常兜底与无结果行为测试（mock 大模型，不发真实请求）。

FastAPI 的 TestClient 依赖 httpx（未安装），因此直接调用异常处理器验证响应结构，
不引入新依赖。
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app.api as api_module
import app.llm as llm_module
import app.agent as agent_module
from app.agent import answer_flow, reset_session
from app.errors import (
    AppError,
    EMPTY_QUERY,
    INTERNAL_ERROR,
    LLM_FORMAT_ERROR,
    LLM_TIMEOUT,
    MESSAGES,
)
from app.llm import LLMFormatError, chat_json

CLEAR_PARSE = {
    "keywords": ["火锅", "局"],
    "time": {
        "type": "relative",
        "start": "2026-10-19 00:00:00",
        "end": "2026-10-25 23:59:59",
        "raw": "上周",
    },
    "category": "event",
    "file_hint": None,
    "ambiguous": None,
}

CANDIDATE = {
    "msg_id": "m1",
    "content": "周六晚上6点在南门集合去团建",
    "sender": "班长",
    "send_time": "2026-10-20 10:00:00",
    "score": 0.03,
    "sources": ["kw"],
}

FABRICATED_ANSWER = {
    "answer": "团建改到周日上午了",
    "events": [
        {
            "summary": "班级团建",
            "time": None,
            "time_raw": "周日上午",
            "location": None,
            "participants": [],
            "sources": [{"msg_id": "m1", "quote": "改到周日上午11点"}],
        }
    ],
}


@pytest.fixture(autouse=True)
def _clean_sessions():
    reset_session("e1")
    yield
    reset_session("e1")


def _body(response):
    return json.loads(response.body)


def test_empty_query_code():
    """空查询 → EMPTY_QUERY，处理器返回 {code, message, data:null}。"""
    with pytest.raises(AppError) as excinfo:
        answer_flow("   ", "e1")

    error = excinfo.value
    assert error.code == EMPTY_QUERY
    assert error.message == MESSAGES[EMPTY_QUERY]

    response = asyncio.run(api_module.app_error_handler(None, error))
    assert response.status_code == 400
    assert _body(response) == {
        "code": EMPTY_QUERY,
        "message": MESSAGES[EMPTY_QUERY],
        "data": None,
    }


def test_known_handlers_registered():
    """三类已知异常都已注册全局处理器。"""
    handlers = api_module.app.exception_handlers
    assert handlers.get(AppError) is api_module.app_error_handler
    assert handlers.get(LLMFormatError) is api_module.llm_format_error_handler


def test_llm_timeout(monkeypatch):
    """mock 大模型超时 → LLM_TIMEOUT（504），且不重试。"""

    class Fake:
        def __init__(self):
            self.calls = 0

        def __call__(self, messages, timeout=None):
            self.calls += 1
            raise TimeoutError("timed out")

    fake = Fake()
    monkeypatch.setattr(llm_module, "_call_llm", fake)

    with pytest.raises(AppError) as excinfo:
        chat_json([{"role": "user", "content": "hi"}])

    assert excinfo.value.code == LLM_TIMEOUT
    assert fake.calls == 1  # 超时不重试

    response = asyncio.run(api_module.app_error_handler(None, excinfo.value))
    assert response.status_code == 504
    assert _body(response)["code"] == LLM_TIMEOUT


def test_llm_format_error(monkeypatch):
    """连续两次非法 JSON → 重试 1 次后抛 LLMFormatError → LLM_FORMAT_ERROR(2002)。"""

    class Fake:
        def __init__(self):
            self.calls = 0

        def __call__(self, messages, timeout=None):
            self.calls += 1
            return "这不是 JSON"

    fake = Fake()
    monkeypatch.setattr(llm_module, "_call_llm", fake)

    with pytest.raises(LLMFormatError):
        chat_json([{"role": "user", "content": "hi"}])
    assert fake.calls == 2  # 首次 + 重试 1 次

    response = asyncio.run(
        api_module.llm_format_error_handler(None, LLMFormatError("bad json"))
    )
    assert response.status_code == 502
    body = _body(response)
    assert body["code"] == LLM_FORMAT_ERROR
    assert body["message"] == MESSAGES[LLM_FORMAT_ERROR]
    assert body["data"] is None


def test_unknown_exception_internal_error():
    """未知异常 → 500 + INTERNAL_ERROR，不泄露内部细节。"""
    response = asyncio.run(
        api_module.unhandled_error_handler(None, ValueError("内部堆栈细节"))
    )

    assert response.status_code == 500
    body = _body(response)
    assert body["code"] == INTERNAL_ERROR
    assert body["data"] is None
    assert "内部堆栈细节" not in body["message"]


def test_no_result_suggestions(monkeypatch):
    """hybrid_search 为空 → no_result + 建议，且不调用大模型生成答案。"""

    class FakeLLM:
        def __init__(self):
            self.calls = 0

        def __call__(self, messages, timeout=None):
            self.calls += 1
            return json.dumps(CLEAR_PARSE, ensure_ascii=False)

    fake_llm = FakeLLM()
    monkeypatch.setattr(llm_module, "_call_llm", fake_llm)
    monkeypatch.setattr(agent_module, "hybrid_search", lambda **kwargs: [])

    response = answer_flow("上周的火锅局", "e1", today="2026-10-25")

    assert response["status"] == "no_result"
    assert response["session_id"] == "e1"
    assert response["suggestions"] == agent_module.NO_RESULT_SUGGESTIONS
    assert len(response["suggestions"]) == 3
    assert "result" not in response  # 没有走答案生成
    assert fake_llm.calls == 1  # 只有查询解析，没有答案生成调用


def test_no_result_when_quotes_all_dropped(monkeypatch):
    """引用全部被剔除 → no_result + 建议，不透出模型编造结论。"""

    class FakeLLM:
        def __init__(self):
            self.calls = 0
            self.responses = [
                json.dumps(CLEAR_PARSE, ensure_ascii=False),
                json.dumps(FABRICATED_ANSWER, ensure_ascii=False),
            ]

        def __call__(self, messages, timeout=None):
            idx = min(self.calls, len(self.responses) - 1)
            self.calls += 1
            return self.responses[idx]

    fake_llm = FakeLLM()
    monkeypatch.setattr(llm_module, "_call_llm", fake_llm)
    monkeypatch.setattr(agent_module, "hybrid_search", lambda **kwargs: [dict(CANDIDATE)])

    response = answer_flow("上周的火锅局", "e1", today="2026-10-25")

    assert response["status"] == "no_result"
    assert response["suggestions"] == agent_module.NO_RESULT_SUGGESTIONS
    assert response["result"]["status"] == "no_result"
    assert response["result"]["events"] == []
    assert fake_llm.calls == 2  # 1 次解析 + 1 次答案生成（被校验拒绝）
