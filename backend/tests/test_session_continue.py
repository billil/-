"""U1：回答后继续更正（会话保留 + TTL + 意图判定）测试。

全部 mock 大模型与检索，不发真实请求、不加载向量模型。
"""

import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app.llm as llm_module
import app.agent as agent_module
from app.agent import (
    SESSION_STORE,
    answer_flow,
    followup_or_new,
    get_session,
    reset_session,
    save_session,
)
from app.session import (
    STATUS_ANSWERED,
    InMemorySessionStore,
    Session,
)

TODAY = "2026-10-25"  # 周日：这周 10-19~10-25，下周 10-26~11-01

PARSE_THIS_WEEK = {
    "keywords": ["志愿", "活动"],
    "time": {
        "type": "relative",
        "start": "2026-10-19 00:00:00",
        "end": "2026-10-25 23:59:59",
        "raw": "这周",
    },
    "category": "event",
    "file_hint": None,
    "ambiguous": None,
}

CORRECT_NEXT_WEEK = {
    "keywords": ["志愿", "活动"],
    "time": {
        "type": "relative",
        "start": "2026-10-26 00:00:00",
        "end": "2026-11-01 23:59:59",
        "raw": "下周",
    },
    "category": "event",
    "file_hint": None,
    "ambiguous": None,
}

CORRECT_LAST_MONTH = {
    "keywords": ["志愿", "活动"],
    "time": {
        "type": "relative",
        "start": "2026-09-01 00:00:00",
        "end": "2026-09-30 23:59:59",
        "raw": "上个月",
    },
    "category": "event",
    "file_hint": None,
    "ambiguous": None,
}

FRESH_PARSE = {
    "keywords": ["志愿", "活动"],
    "time": {"type": "none", "start": None, "end": None, "raw": None},
    "category": "event",
    "file_hint": None,
    "ambiguous": None,
}


@pytest.fixture(autouse=True)
def _clean_sessions():
    for sid in [f"s{i}" for i in range(1, 10)]:
        reset_session(sid)
    yield
    for sid in [f"s{i}" for i in range(1, 10)]:
        reset_session(sid)


@pytest.fixture
def fake_llm(monkeypatch):
    """按顺序返回预设 JSON，并记录每次送入的 payload。"""

    class Fake:
        def __init__(self):
            self.calls = 0
            self.responses: list[str] = []
            self.payloads: list[dict] = []

        def __call__(self, messages, timeout=None):
            idx = min(self.calls, len(self.responses) - 1)
            self.calls += 1
            self.payloads.append(json.loads(messages[1]["content"]))
            return self.responses[idx]

    fake = Fake()
    monkeypatch.setattr(llm_module, "_call_llm", fake)
    return fake


@pytest.fixture
def fake_search(monkeypatch):
    """替换检索，返回一条候选并记录参数。"""

    class Fake:
        def __init__(self):
            self.calls: list[dict] = []

        def __call__(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "results": [
                    {
                        "msg_id": "m1",
                        "content": "周日上午九点在南门集合做志愿活动",
                        "sender": "班长",
                        "send_time": "2026-10-24 10:00:00",
                        "score": 0.04,
                        "sources": ["kw"],
                    }
                ],
                "debug": {
                    "applied_filters": [],
                    "soft_boost": {},
                    "result_count_before_filter": 1,
                    "result_count_after_filter": 1,
                },
            }

    fake = Fake()
    monkeypatch.setattr(agent_module, "hybrid_search", fake)
    monkeypatch.setattr(
        agent_module,
        "generate_answer",
        lambda *a, **k: {"status": "ok", "answer": "在南门集合", "events": []},
    )
    return fake


def _json(obj):
    return json.dumps(obj, ensure_ascii=False)


def test_first_answer_keeps_session(fake_llm, fake_search):
    """a. 首轮正常回答后会话仍在，status=answered 且保留 parse / result_summary。"""
    fake_llm.responses = [_json(PARSE_THIS_WEEK)]

    result = answer_flow("这周志愿活动在哪", "s1", today=TODAY)

    assert result["status"] == "answer"
    cached = get_session("s1")
    assert cached is not None
    assert cached["status"] == STATUS_ANSWERED
    assert cached["parse"]["time"]["raw"] == "这周"
    assert cached["result_summary"] == "在南门集合"
    assert cached["ask_count"] == 0


def test_followup_correction_keeps_topic(fake_llm, fake_search):
    """b. 接话"不是这周，是下周" → correction，保留"志愿活动"，时间整体替换。"""
    fake_llm.responses = [_json(PARSE_THIS_WEEK), _json(CORRECT_NEXT_WEEK)]

    answer_flow("这周志愿活动在哪", "s1", today=TODAY)
    result = answer_flow("不是这周，是下周", "s1", today=TODAY)

    assert fake_llm.calls == 2
    # 第二次走 correct 提示词：payload 带 original，且基线是上一轮 parse
    assert "original" in fake_llm.payloads[1]
    assert fake_llm.payloads[1]["original"]["time"]["raw"] == "这周"

    parse = result["parse"]
    assert "志愿" in parse["keywords"] and "活动" in parse["keywords"]
    assert parse["time"]["start"] == "2026-10-26 00:00:00"
    assert parse["time"]["raw"] == "下周"
    assert "2026-10-19" not in json.dumps(parse, ensure_ascii=False)
    assert fake_search.calls[1]["filters"]["time_range"]["start"] == "2026-10-26 00:00:00"
    assert get_session("s1")["status"] == STATUS_ANSWERED


def test_expired_session_falls_back_to_new(fake_llm, fake_search):
    """c. 会话过期后同样的话 → 走 new，并标记 context_lost=true。"""
    fake_llm.responses = [_json(PARSE_THIS_WEEK), _json(FRESH_PARSE)]
    answer_flow("这周志愿活动在哪", "s1", today=TODAY)

    # 手动把 updated_at 拨回 31 分钟前，触发 TTL 过期
    SESSION_STORE.get("s1").updated_at = datetime.now() - timedelta(minutes=31)

    result = answer_flow("不是这周，是下周", "s1", today=TODAY)

    assert result["status"] == "answer"
    assert result.get("context_lost") is True
    assert "original" not in fake_llm.payloads[1]  # 走 parse，不是 correct
    assert "query" in fake_llm.payloads[1]
    assert "不是这周" in fake_search.calls[1]["query"]
    # 新会话已重新建立
    assert get_session("s1")["status"] == STATUS_ANSWERED


def test_two_consecutive_corrections_use_latest(fake_llm, fake_search):
    """d. 连续两次更正都基于最新 parse，条件不累积。"""
    save_session("s9", "这周志愿活动在哪", PARSE_THIS_WEEK)
    fake_llm.responses = [_json(CORRECT_LAST_MONTH), _json(CORRECT_NEXT_WEEK)]

    answer_flow("不是这周，是上个月", "s9", today=TODAY)
    result = answer_flow("不是上个月，是下周", "s9", today=TODAY)

    parse = result["parse"]
    assert parse["time"]["start"] == "2026-10-26 00:00:00"
    dumped = json.dumps(parse, ensure_ascii=False)
    assert "2026-09" not in dumped  # 上个月已消失
    assert "2026-10-19" not in dumped  # 这周已消失
    # 第二次的基线是第一次更正后的 parse（上个月），不是最初的这周
    assert fake_llm.payloads[1]["original"]["time"]["raw"] == "上个月"


def test_followup_or_new_rules():
    """信号词 / 短查询关键词交集 / 其余判 new。"""
    session = Session(session_id="x", parse={"keywords": ["志愿", "活动"]})

    assert followup_or_new("不是这周，是下周", None) == "correction"
    assert followup_or_new("改成打球", None) == "correction"
    assert followup_or_new("我记错了", None) == "correction"
    assert followup_or_new("志愿活动", session) == "correction"  # 短且有交集
    assert followup_or_new("今天天气怎么样", session) == "new"  # 无交集
    assert followup_or_new("帮我找一下上周所有志愿活动相关的照片", session) == "new"  # 超 15 字
    assert followup_or_new("帮我找上周的火锅局", None) == "new"


def test_ttl_boundary():
    """TTL：29 分钟有效，31 分钟视为不存在。"""
    store = InMemorySessionStore()
    fresh = Session(session_id="t1", parse={})
    store.save(fresh)
    fresh.updated_at = datetime.now() - timedelta(minutes=29)
    assert store.get("t1") is not None

    fresh.updated_at = datetime.now() - timedelta(minutes=31)
    assert store.get("t1") is None


def test_correction_keeps_ask_count(fake_llm, fake_search):
    """更正不消耗追问预算：ask_count=1 的会话更正后仍为 1。"""
    SESSION_STORE.save(
        Session(
            session_id="s7",
            status=STATUS_ANSWERED,
            query="这周志愿活动在哪",
            parse=PARSE_THIS_WEEK,
            ask_count=1,
        )
    )
    fake_llm.responses = [_json(CORRECT_NEXT_WEEK)]

    answer_flow("不是这周，是下周", "s7", today=TODAY)

    assert get_session("s7")["ask_count"] == 1
