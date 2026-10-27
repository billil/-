"""U5 无结果与故障降级测试。

覆盖四种场景：
a. 无关问题 → no_data，不生成答案
b. 低相关候选（余弦 0.30 < 阈值）→ low_relevance + low_confidence
c. 向量模型抛异常 → retrieval_degraded，仍返回关键词结果，并带降级说明
d. 分数正好等于阈值 → 保留（>= 边界）

全程离线：关键词/向量检索用固定命中替代，不加载真实模型。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app.agent as agent_module
import app.search_hybrid as search_module
from app.agent import answer_flow, reset_session
from app.db import init_db
from app.search import MIN_SCORE_VEC, split_by_threshold
from app.search_hybrid import hybrid_search

TODAY = "2026-10-26"

MESSAGES = [
    ("m1", "g1", "班长", "2026-10-20 10:00:00", "周末聚餐地点定在南门"),
]

# 解析结果固定，避免测试依赖大模型
PARSE = {
    "keywords": ["天气"],
    "time": {"type": "none", "start": None, "end": None, "raw": None},
    "time_field": "event_time",
    "category": None,
    "file_hint": None,
    "filters": {"explicit": [], "vague": []},
    "ambiguous": None,
}


@pytest.fixture(autouse=True)
def _clean_sessions():
    for session_id in ("u5-a", "u5-b", "u5-c", "u5-d"):
        reset_session(session_id)
    yield
    for session_id in ("u5-a", "u5-b", "u5-c", "u5-d"):
        reset_session(session_id)


@pytest.fixture
def db(tmp_path):
    """建一个只有一条消息的临时库，保证元信息查询可命中。"""
    path = str(tmp_path / "no_result.db")
    conn = init_db(path)
    try:
        conn.executemany(
            "INSERT INTO messages (msg_id, group_id, sender, send_time, content, content_terms) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(m[0], m[1], m[2], m[3], m[4], m[4]) for m in MESSAGES],
        )
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.fixture
def fake_retrieval(monkeypatch):
    """替换关键词/向量检索，命中集合由用例指定。"""
    hits: dict = {"kw": [], "vec": []}

    def _kw(query, limit=50, db_path=None):
        return [dict(h) for h in hits["kw"]]

    def _vec(query, top_k=50, db_path=None):
        return [dict(h) for h in hits["vec"]]

    monkeypatch.setattr(search_module, "kw_search", _kw)
    monkeypatch.setattr(search_module, "vec_search", _vec)
    return hits


def _hit(msg_id: str = "m1", score: float = 0.0) -> dict:
    row = next(m for m in MESSAGES if m[0] == msg_id)
    return {
        "msg_id": row[0],
        "content": row[4],
        "sender": row[2],
        "send_time": row[3],
        "score": score,
        "rank": 0.0,
    }


def _patch_flow(monkeypatch, search_out: dict, answer_calls: list):
    """固定解析与检索结果，并记录答案生成是否被调用。"""

    def _fake_answer(*args, **kwargs):
        answer_calls.append(1)
        return {"status": "ok", "answer": "根据群聊原文……", "events": []}

    monkeypatch.setattr(agent_module, "parse_query", lambda q, today=None: dict(PARSE))
    monkeypatch.setattr(agent_module, "hybrid_search", lambda **kwargs: search_out)
    monkeypatch.setattr(agent_module, "generate_answer", _fake_answer)


# ---------------- a. 无结果 ----------------

def test_irrelevant_query_returns_no_data(db, fake_retrieval, monkeypatch):
    """无关问题两路检索均为空 → outcome=no_data，且不调用大模型生成答案。"""
    out = hybrid_search("今天天气怎么样", db_path=db)
    assert out["results"] == []
    assert out["debug"]["outcome"] == "no_data"

    calls: list = []
    _patch_flow(monkeypatch, out, calls)
    response = answer_flow("今天天气怎么样", "u5-a", db_path=db, today=TODAY)

    assert response["status"] == "no_result"
    assert response["outcome"] == "no_data"
    assert "群里没有找到相关资料" in response["message"]
    assert response["suggestions"]  # 附带可操作建议
    assert calls == []  # 不生成答案


# ---------------- b. 低相关 ----------------

def test_low_similarity_candidate_marks_low_relevance(db, fake_retrieval, monkeypatch):
    """仅有 0.30 相似度候选 → low_relevance，候选带 low_confidence，不生成结论。"""
    fake_retrieval["vec"] = [_hit("m1", score=0.30)]

    out = hybrid_search("今天天气怎么样", db_path=db)
    assert out["debug"]["outcome"] == "low_relevance"
    assert out["results"]
    assert all(r["low_confidence"] is True for r in out["results"])

    calls: list = []
    _patch_flow(monkeypatch, out, calls)
    response = answer_flow("今天天气怎么样", "u5-b", db_path=db, today=TODAY)

    assert response["status"] == "low_relevance"
    assert response["outcome"] == "low_relevance"
    assert response["low_confidence"] is True
    assert "没有找到明确匹配" in response["message"]
    assert calls == []  # 低相关不生成答案


# ---------------- c. 向量故障降级 ----------------

def test_vector_failure_degrades_to_keyword_only(db, monkeypatch):
    """向量检索抛异常 → 只用关键词结果，并标记 retrieval_degraded + 降级说明。"""
    monkeypatch.setattr(search_module, "kw_search", lambda *a, **k: [_hit("m1")])

    def _raise(*args, **kwargs):
        raise RuntimeError("模型不可用")

    monkeypatch.setattr(search_module, "vec_search", _raise)
    # 隔离全局向量状态，避免其他用例的失败计数影响断言
    monkeypatch.setattr(
        search_module,
        "vector_status",
        lambda: {"degraded": False, "last_failed": False, "consecutive_failures": 1},
    )

    out = hybrid_search("聚餐", db_path=db)
    assert out["results"]
    assert out["debug"]["degraded"] is True
    assert out["debug"]["degraded_reason"] == "vector_unavailable"
    assert out["debug"]["outcome"] == "retrieval_degraded"

    calls: list = []
    _patch_flow(monkeypatch, out, calls)
    response = answer_flow("聚餐在哪", "u5-c", db_path=db, today=TODAY)

    assert response["degraded"] is True
    assert response["degraded_reason"] == "vector_unavailable"
    assert response["outcome"] == "retrieval_degraded"
    assert "本次仅使用关键词检索，结果可能不完整" in response["message"]
    assert calls  # 仍正常生成答案


# ---------------- d. 阈值边界 ----------------

def test_score_equal_to_threshold_is_kept(db, fake_retrieval):
    """分数正好等于阈值 → 保留（边界用 >=）。"""
    passed, below = split_by_threshold([{"score": MIN_SCORE_VEC}], "score", MIN_SCORE_VEC)
    assert len(passed) == 1
    assert below == 0

    fake_retrieval["vec"] = [_hit("m1", score=MIN_SCORE_VEC)]
    out = hybrid_search("聚餐", db_path=db)

    assert [r["msg_id"] for r in out["results"]] == ["m1"]
    assert out["results"][0]["low_confidence"] is False
    assert out["debug"]["outcome"] == "ok"


# ---------------- 向量熔断 ----------------

def test_vector_circuit_breaker_after_three_failures(monkeypatch):
    """连续失败 3 次后标记降级，后续检索不再等待向量化超时。"""
    import app.search_vec as search_vec

    search_vec.reset_vector_state()
    attempts: list = []

    def _raise(query):
        attempts.append(query)
        raise RuntimeError("超时")

    monkeypatch.setattr(search_vec, "_embed_query", _raise)
    try:
        for _ in range(3):
            assert search_vec.vec_search("聚餐") == []
        status = search_vec.vector_status()
        assert status["degraded"] is True
        assert status["consecutive_failures"] == 3

        assert search_vec.vec_search("聚餐") == []  # 已降级：直接返回空
        assert len(attempts) == 3  # 第 4 次不再尝试向量化
    finally:
        search_vec.reset_vector_state()
