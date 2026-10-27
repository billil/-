"""U7 facade 测试。

全程 fake provider + 内存 store + monkeypatch LLM：
- 不连业务库（candidate_provider 返回固定候选）
- 不触发真实 LLM 调用（monkeypatch agent 模块上的 parse_query / correct_parse /
  generate_answer / load_clusters）
- 不触发真实向量模型（facade.retrieve 直接接 provider，完全绕过 hybrid_search）

覆盖四条核心路径：answer / ask / no_result / uncertain，外加 degraded 与 revise。
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app.agent as agent_mod
import app.facade as facade
from app.facade import analyze_query, build_answer, process, retrieve, revise, settings
from app.session import (
    STATUS_ANSWERED,
    STATUS_ASKED,
    STATUS_PARSED,
    InMemorySessionStore,
    Session,
)

TODAY = "2026-10-26"


# ---------------------------------------------------------------------------
# fake provider
# ---------------------------------------------------------------------------

MOCK_CANDIDATES = [
    {
        "msg_id": "m001",
        "content": "这周六团建，下午6点在南门集合",
        "sender": "班长",
        "send_time": "2026-10-20 10:00:00",
        "score": 0.82,
        "sources": ["kw", "vec"],
    },
    {
        "msg_id": "m002",
        "content": "高数作业周五晚上8点前交",
        "sender": "学习委员",
        "send_time": "2026-10-21 09:00:00",
        "score": 0.10,
        "sources": ["kw"],
    },
]


class FakeProvider:
    def __init__(self, candidates=None):
        self._candidates = list(candidates) if candidates is not None else list(MOCK_CANDIDATES)
        self.last_filters = None
        self.calls = 0

    def __call__(self, filters: dict):
        self.last_filters = filters
        self.calls += 1
        return list(self._candidates)

    def set(self, candidates):
        self._candidates = list(candidates)


# ---------------------------------------------------------------------------
# 测试 parse / answer 产物（替代真实 LLM）
# ---------------------------------------------------------------------------

PARSE_NORMAL = {
    "keywords": ["团建"],
    "time": {"type": "relative", "start": "2026-10-25 00:00:00", "end": "2026-10-31 23:59:59", "raw": "这周末"},
    "time_field": "event_time",
    "category": "event",
    "file_hint": None,
    "filters": {"explicit": ["category:event"], "vague": []},
    "ambiguous": None,
}

PARSE_AMBIGUOUS = {
    "keywords": ["聚餐"],
    "time": {"type": "none", "start": None, "end": None, "raw": None},
    "time_field": "event_time",
    "category": None,
    "file_hint": None,
    "filters": {"explicit": [], "vague": ["category:event"]},
    "ambiguous": {
        "field": "time",
        "question": "你想查哪一类？",
        "options": ["聚餐", "学习", "活动"],
    },
}

PARSE_AFTER_CORRECTION = {
    "keywords": ["团建", "周日"],
    "time": {"type": "absolute", "start": "2026-10-26 00:00:00", "end": "2026-10-26 23:59:59", "raw": "周日"},
    "time_field": "event_time",
    "category": "event",
    "file_hint": None,
    "filters": {"explicit": ["category:event"], "vague": []},
    "ambiguous": None,
}


def _fake_generate_answer(query, candidates, assumptions, clusters):
    return {
        "status": "ok",
        "answer": f"群里提到 {query} 在周日南门集合",
        "events": [{"title": "团建", "event_time": "2026-10-26"}],
        "evidence": [{"msg_id": c["msg_id"], "quote": (c.get("content") or "")[:30]} for c in candidates[:2]],
        "fields": {"event_time": "2026-10-26", "location": "南门"},
    }


def _fake_generate_uncertain(*args, **kwargs):
    return {
        "status": "uncertain",
        "answer": "",
        "events": [],
        "evidence": [{"msg_id": "m001", "quote": "这周六团建"}],
        "fields": {},
    }


def _fake_load_clusters(candidates, db_path=None):
    return []


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store():
    return InMemorySessionStore()


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture(autouse=True)
def _patch_all(monkeypatch):
    """替换 agent 模块上会触发真实 LLM / DB 的函数。

    facade.py 用 `from . import agent` 然后 agent.xxx(...) 访问，
    monkeypatch agent 模块的属性就能真正影响 facade 内部调用。
    """
    monkeypatch.setattr(agent_mod, "parse_query", lambda q, today=None: PARSE_NORMAL)
    monkeypatch.setattr(agent_mod, "correct_parse", lambda p, c, today=None: PARSE_AFTER_CORRECTION)
    monkeypatch.setattr(agent_mod, "generate_answer", _fake_generate_answer)
    monkeypatch.setattr(agent_mod, "load_clusters", _fake_load_clusters)

    old = settings.DEBUG
    settings.DEBUG = True
    yield
    settings.DEBUG = old


# ---------------------------------------------------------------------------
# analyze_query / revise / retrieve / build_answer
# ---------------------------------------------------------------------------

def test_analyze_query_uses_agent_parse(monkeypatch):
    calls = []

    def _track(q, today=None):
        calls.append(q)
        return dict(PARSE_NORMAL)

    monkeypatch.setattr(agent_mod, "parse_query", _track)

    parse = analyze_query("这周末的团建", TODAY)
    assert parse == PARSE_NORMAL
    assert calls == ["这周末的团建"]


def test_revise_updates_parse():
    parse = revise(dict(PARSE_NORMAL), "改成周日", TODAY)
    assert parse == PARSE_AFTER_CORRECTION


def test_retrieve_passes_filters_to_provider(provider):
    out = retrieve(dict(PARSE_NORMAL), provider, limit=5)
    assert out["results"] == MOCK_CANDIDATES
    assert out["debug"]["count"] == 2
    filters = provider.last_filters
    assert filters["category"] == "event"
    assert filters["_limit"] == 5


def test_retrieve_rejects_non_callable():
    with pytest.raises(TypeError):
        retrieve(dict(PARSE_NORMAL), "not callable", limit=5)


def test_build_answer_generates_evidence():
    result = build_answer(dict(PARSE_NORMAL), list(MOCK_CANDIDATES))
    assert result["status"] == "ok"
    assert result["answer"]
    assert result["evidence"]


# ---------------------------------------------------------------------------
# process 四条核心路径
# ---------------------------------------------------------------------------

def test_process_answer_path(provider, store):
    """正常 query → parse 无 ambiguous → retrieve → answer。"""
    session_id = uuid.uuid4().hex
    response = process(
        "团建在哪",
        {"session_id": session_id, "status": "answered", "parse": {}, "ask_count": 0},
        provider,
        store,
        today=TODAY,
    )

    assert response["status"] == "answer"
    assert response["parse"] == PARSE_NORMAL
    assert response["answer"]["status"] == "ok"
    assert response["evidence"]
    assert response["debug"]["filters"]["category"] == "event"

    session = store.get(session_id)
    assert session is not None
    assert session.status == STATUS_ANSWERED
    assert session.parse == PARSE_NORMAL


def test_process_ask_path(provider, store, monkeypatch):
    """query 命中 ambiguous 且 ask_count < 1 → 追问一次。"""
    monkeypatch.setattr(agent_mod, "parse_query", lambda q, today=None: PARSE_AMBIGUOUS)

    session_id = uuid.uuid4().hex
    response = process(
        "聚餐有什么活动",
        {"session_id": session_id},  # 带 session_id 但其他字段空
        provider,
        store,
        today=TODAY,
    )

    assert response["status"] == "ask"
    assert response["question"] == "你想查哪一类？"
    assert response["options"] == ["聚餐", "学习", "活动"]

    session = store.get(session_id)
    assert session is not None
    assert session.status == STATUS_ASKED
    assert session.ask_count == 1


def test_process_no_result_path(provider, store):
    """provider 返回空列表 → no_result + suggestions。"""
    provider.set([])
    response = process("不存在的关键词", None, provider, store, today=TODAY)

    assert response["status"] == "no_result"
    assert response["message"] == "群里没有找到相关资料"
    assert response["suggestions"]
    assert response["answer"] is None
    assert response["evidence"] == []


def test_process_uncertain_path(provider, store, monkeypatch):
    """有候选但 generate_answer 返回 uncertain。"""
    monkeypatch.setattr(agent_mod, "generate_answer", _fake_generate_uncertain)

    response = process("这次活动的时间对不上", None, provider, store, today=TODAY)

    assert response["status"] == "uncertain"
    assert response["message"].startswith("现有原文能对上")
    assert response["answer"]["status"] == "uncertain"
    assert response["evidence"]


def test_process_degraded_path(provider, store, monkeypatch):
    """retrieve 的 debug 带 degraded 标记 → status=degraded。"""
    import app.facade as facade_mod

    def _retrieve_with_degraded(parse, candidate_provider, limit=5):
        out = facade_mod.retrieve.__wrapped__(parse, candidate_provider, limit=limit) if hasattr(facade_mod.retrieve, "__wrapped__") else facade_mod.retrieve(parse, candidate_provider, limit=limit)
        out.setdefault("debug", {})
        out["debug"]["degraded"] = True
        out["debug"]["degraded_reason"] = "vector_unavailable"
        return out

    # 更直接的做法：monkeypatch retrieve 本身（不 __wrapped__）
    original_retrieve = facade_mod.retrieve

    def _fake_retrieve(parse, candidate_provider, limit=5):
        out = original_retrieve(parse, candidate_provider, limit=limit)
        out.setdefault("debug", {})
        out["debug"]["degraded"] = True
        out["debug"]["degraded_reason"] = "vector_unavailable"
        return out

    monkeypatch.setattr(facade_mod, "retrieve", _fake_retrieve)

    response = process("团建", None, provider, store, today=TODAY)
    assert response["status"] == "degraded"
    assert response["degraded_reason"] == "vector_unavailable"
    assert "本次仅使用关键词检索" in response["message"]


def test_process_revise_path(provider, store):
    """会话已存在，本轮 query 命中更正信号 → revise 分支。"""
    session_id = uuid.uuid4().hex
    store.save(
        Session(
            session_id=session_id,
            status=STATUS_ANSWERED,
            query="周六的活动",
            parse=PARSE_NORMAL,
            ask_count=0,
        )
    )

    response = process(
        "改成周日",
        {"session_id": session_id, "status": "answered", "parse": PARSE_NORMAL, "ask_count": 0},
        provider,
        store,
        today=TODAY,
    )

    assert response["status"] == "answer"
    # correct_parse 应该被调用，parse 带了修正后的 keywords
    assert response["parse"]["keywords"] == ["团建", "周日"]
    assert "周日" in " ".join(response["parse"].get("keywords") or [])
