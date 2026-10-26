"""带引用答案生成测试（mock 大模型，不发真实请求）。

核心：代码层不信任大模型，quote 必须是原文子串；全部失效则降级 no_result。
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app.llm as llm_module
import app.agent as agent_module
from app.agent import answer_flow, reset_session
from app.answer import generate_answer

CANDIDATES = [
    {
        "msg_id": "m1",
        "content": "周六晚上6点在南门集合去团建，大家带身份证",
        "sender": "班长",
        "send_time": "2026-10-20 10:00:00",
        "score": 0.03,
        "sources": ["kw"],
    }
]

VALID_ANSWER = {
    "answer": "周六晚上6点在南门集合去团建",
    "events": [
        {
            "summary": "班级团建",
            "time": "2026-10-25 18:00:00",
            "time_raw": "周六晚上6点",
            "location": "南门",
            "participants": ["班长"],
            "sources": [
                {"msg_id": "m1", "quote": "周六晚上6点在南门集合去团建"}
            ],
        }
    ],
}

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
    reset_session("a1")
    yield
    reset_session("a1")


@pytest.fixture
def fake_llm(monkeypatch):
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


def _json(obj):
    return json.dumps(obj, ensure_ascii=False)


def test_answer_normal_with_valid_quotes(fake_llm):
    """正常生成，引用校验通过。"""
    fake_llm.responses = [_json(VALID_ANSWER)]

    result = generate_answer("团建什么时候", CANDIDATES)

    assert result["status"] == "ok"
    assert result["answer"] == "周六晚上6点在南门集合去团建"
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["time_raw"] == "周六晚上6点"
    assert event["location"] == "南门"
    assert event["sources"] == [{"msg_id": "m1", "quote": "周六晚上6点在南门集合去团建"}]
    assert event["msg_ids"] == ["m1"]


def test_fabricated_quote_dropped(fake_llm):
    """大模型编造 quote（不在原文中）→ 该 source 被剔除，真引用保留。"""
    fabricated = {
        "answer": "周六晚上6点在南门集合去团建",
        "events": [
            {
                "summary": "班级团建",
                "time": None,
                "time_raw": "周六晚上6点",
                "location": "南门",
                "participants": [],
                "sources": [
                    {"msg_id": "m1", "quote": "周六晚上7点在东门集合"},  # 编造
                    {"msg_id": "m1", "quote": "在南门集合"},  # 真实子串
                ],
            }
        ],
    }
    fake_llm.responses = [_json(fabricated)]

    result = generate_answer("团建时间", CANDIDATES)

    assert result["status"] == "ok"
    sources = result["events"][0]["sources"]
    assert len(sources) == 1
    assert sources[0]["quote"] == "在南门集合"


def test_all_quotes_dropped_degrades_to_no_result(fake_llm):
    """所有引用都被剔除 → 降级 no_result，不保留模型结论。"""
    fabricated = {
        "answer": "团建改到周日上午了",
        "events": [
            {
                "summary": "班级团建",
                "time": None,
                "time_raw": "周日上午",
                "location": None,
                "participants": [],
                "sources": [{"msg_id": "m1", "quote": "改到周日上午11点"}, {"msg_id": "m9", "quote": "随便"}],
            }
        ],
    }
    fake_llm.responses = [_json(fabricated)]

    result = generate_answer("团建时间", CANDIDATES)

    assert result["status"] == "no_result"
    assert result["events"] == []
    assert "无法给出确定答案" in result["answer"]
    # 模型原文被丢弃，避免把编造结论透给用户
    assert "周日上午" not in result["answer"]


def test_unverified_fields_stay_null(fake_llm):
    """answer 中引用之外的事实字段保持 null，不做补全。"""
    partial = {
        "answer": "根据群聊记录无法确定具体改期时间",
        "events": [
            {
                "summary": "班级团建",
                "time": None,
                "time_raw": None,
                "location": None,
                "participants": [],
                "sources": [{"msg_id": "m1", "quote": "周六晚上6点在南门集合去团建"}],
            }
        ],
    }
    fake_llm.responses = [_json(partial)]

    result = generate_answer("团建改到几点了", CANDIDATES)

    event = result["events"][0]
    assert event["time"] is None
    assert event["time_raw"] is None
    assert event["location"] is None
    assert event["participants"] is None


def test_end_to_end_answer_flow(fake_llm, monkeypatch):
    """检索命中后 answer_flow 返回带校验引用的 result。"""
    monkeypatch.setattr(agent_module, "hybrid_search", lambda **kwargs: list(CANDIDATES))
    fake_llm.responses = [_json(CLEAR_PARSE), _json(VALID_ANSWER)]

    response = answer_flow("上周的火锅局", "a1", today="2026-10-25")

    assert response["status"] == "answer"
    assert response["result"]["status"] == "ok"
    assert response["result"]["events"][0]["sources"][0]["msg_id"] == "m1"
    assert response["candidates"][0]["msg_id"] == "m1"
    assert fake_llm.calls == 2  # 1 次解析 + 1 次答案生成


def test_no_candidates_no_llm_call(fake_llm):
    """无检索候选时不调用大模型，直接 no_result。"""
    result = generate_answer("团建时间", [])

    assert result["status"] == "no_result"
    assert fake_llm.calls == 0
