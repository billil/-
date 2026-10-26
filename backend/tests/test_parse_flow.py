"""查询解析与追问流程测试（mock 大模型与检索，不发真实请求、不加载向量模型）。

覆盖：模糊查询触发追问、清晰查询不追问、会话内最多追问 1 次、
二次仍 ambiguous 走"直接检索 + 说明假设"、会话缓存保存/读取、时间条件透传。
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app.llm as llm_module
import app.agent as agent_module
from app.agent import answer_flow, get_session, reset_session

TODAY = "2026-10-25"

# 模糊查询的解析结果：时间无法确定
AMBIGUOUS_PARSE = {
    "keywords": ["活动"],
    "time": {"type": "none", "start": None, "end": None, "raw": None},
    "category": "any",
    "file_hint": None,
    "ambiguous": {
        "field": "time",
        "question": "你指的是哪段时间的活动？",
        "options": ["最近一周", "上个月", "都可以"],
    },
}

# 清晰查询的解析结果：上周的火锅局
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


@pytest.fixture(autouse=True)
def _clean_sessions():
    """每个用例前后清理会话缓存，保证隔离。"""
    for sid in ["s1", "s2", "s3"]:
        reset_session(sid)
    yield
    for sid in ["s1", "s2", "s3"]:
        reset_session(sid)


@pytest.fixture
def fake_llm(monkeypatch):
    """替换 _call_llm，按顺序返回预设响应。"""

    class Fake:
        def __init__(self):
            self.calls = 0
            self.responses: list[str] = []

        def __call__(self, messages, timeout=None):
            idx = min(self.calls, len(self.responses) - 1)
            self.calls += 1
            return self.responses[idx]

    fake = Fake()
    monkeypatch.setattr(llm_module, "_call_llm", fake)
    return fake


@pytest.fixture
def fake_search(monkeypatch):
    """替换 hybrid_search，记录调用参数。"""

    class Fake:
        def __init__(self):
            self.calls: list[dict] = []

        def __call__(self, **kwargs):
            self.calls.append(kwargs)
            return [
                {
                    "msg_id": "m1",
                    "content": "周六晚上聚餐，都来啊",
                    "sender": "班长",
                    "send_time": "2026-10-20 12:00:00",
                    "score": 0.032,
                    "sources": ["kw", "vec"],
                }
            ]

    fake = Fake()
    monkeypatch.setattr(agent_module, "hybrid_search", fake)
    # 答案生成在 T3-2 接入，这里用桩隔离，避免干扰本文件的大模型调用次数断言
    monkeypatch.setattr(
        agent_module,
        "generate_answer",
        lambda *a, **k: {"status": "ok", "answer": "", "events": []},
    )
    return fake


def _json(obj):
    return json.dumps(obj, ensure_ascii=False)


def test_vague_query_asks(fake_llm, fake_search):
    """"帮我找那个活动" → 返回 ask，并保存会话缓存。"""
    fake_llm.responses = [_json(AMBIGUOUS_PARSE)]

    result = answer_flow("帮我找那个活动", "s1", today=TODAY)

    assert result["status"] == "ask"
    assert result["session_id"] == "s1"
    assert result["question"] == "你指的是哪段时间的活动？"
    assert result["options"] == ["最近一周", "上个月", "都可以"]
    assert fake_llm.calls == 1
    assert fake_search.calls == []  # 追问阶段不检索


def test_clear_query_no_ask(fake_llm, fake_search):
    """"上周的火锅局" → 不追问，keywords 含"火锅"，time.type=relative。"""
    fake_llm.responses = [_json(CLEAR_PARSE)]

    result = answer_flow("上周的火锅局", "s2", today=TODAY)

    assert result["status"] == "answer"
    assert "火锅" in result["parse"]["keywords"]
    assert result["parse"]["time"]["type"] == "relative"
    assert result["assumptions"] == []
    assert result["message"] == ""
    assert get_session("s2") is None  # 未追问，不残留会话
    assert len(fake_search.calls) == 1
    assert len(result["candidates"]) == 1


def test_second_ambiguous_no_second_ask(fake_llm, fake_search):
    """首次追问后仍 ambiguous → 不再追问，直接检索并说明假设。"""
    fake_llm.responses = [_json(AMBIGUOUS_PARSE), _json(AMBIGUOUS_PARSE)]

    first = answer_flow("帮我找那个活动", "s3", today=TODAY)
    assert first["status"] == "ask"

    second = answer_flow("都可以", "s3", today=TODAY)

    assert second["status"] == "answer"  # 第二次不再 ask
    assert second["assumptions"]  # 必须说明假设
    assert second["message"].startswith("说明：")
    assert "未能确认时间范围" in second["message"]
    assert fake_llm.calls == 2  # 解析两次，没有第三次追问
    assert get_session("s3") is None  # 缓存已清理
    assert len(fake_search.calls) == 1


def test_session_cache_save_and_read(fake_llm, fake_search):
    """会话缓存正确保存与读取。"""
    fake_llm.responses = [_json(AMBIGUOUS_PARSE)]

    answer_flow("帮我找那个活动", "s1", today=TODAY)
    cached = get_session("s1")

    assert cached is not None
    assert cached["pending"] is True
    assert cached["query"] == "帮我找那个活动"
    assert cached["parse"]["ambiguous"]["field"] == "time"

    reset_session("s1")
    assert get_session("s1") is None


def test_time_filter_passed_to_search(fake_llm, fake_search):
    """parse 得到的 time.start / time.end 正确传给 hybrid_search。"""
    fake_llm.responses = [_json(CLEAR_PARSE)]

    answer_flow("上周的火锅局", "s2", today=TODAY)

    kwargs = fake_search.calls[0]
    assert kwargs["time_start"] == "2026-10-19 00:00:00"
    assert kwargs["time_end"] == "2026-10-25 23:59:59"
    assert kwargs["keywords"] == ["火锅", "局"]


def test_invalid_ambiguous_not_asking(fake_llm, fake_search):
    """ambiguous 字段非法（选项少于 2 个）时不追问，按普通查询处理。"""
    bad = dict(CLEAR_PARSE)
    bad["ambiguous"] = {"field": "time", "question": "哪段时间？", "options": ["都可以"]}
    fake_llm.responses = [_json(bad)]

    result = answer_flow("上周的火锅局", "s2", today=TODAY)

    assert result["status"] == "answer"
    assert result["assumptions"] == []
