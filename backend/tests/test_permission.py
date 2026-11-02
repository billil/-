"""T2 权限边界测试。

B 侧只消费 D 已授权的候选：
- 越权 group_id / 白名单外 msg_id → 丢弃并计数，不进答案
- 缺少 group_scope / allowed_msg_ids → permission_denied，且不调用大模型

全程离线：parse / 答案生成 / cluster 全部用 stub，不连库、不加载向量模型。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app.agent as agent_module
import app.answer as answer_module
import app.facade as facade_module
from app.answer import generate_answer
from app.facade import process, retrieve, settings
from app.session import InMemorySessionStore

TODAY = "2026-10-26"

PARSE = {
    "keywords": ["团建"],
    "time": {"type": "none", "start": None, "end": None, "raw": None},
    "time_field": "event_time",
    "category": None,
    "file_hint": None,
    "filters": {"explicit": [], "vague": []},
    "ambiguous": None,
}

IN_SCOPE = {
    "msg_id": "m-in",
    "group_id": "g1",
    "content": "这周六团建，南门集合",
    "sender": "班长",
    "send_time": "2026-10-20 10:00:00",
    "score": 0.8,
    "sources": ["kw"],
}
OUT_SCOPE = {
    "msg_id": "m-out",
    "group_id": "g2",
    "content": "别的群火锅局定在周五",
    "sender": "小B",
    "send_time": "2026-10-21 09:00:00",
    "score": 0.8,
    "sources": ["kw"],
}


class Provider:
    """记录调用次数与收到的 filters，返回固定候选。"""

    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = 0
        self.last_filters = None

    def __call__(self, filters):
        self.calls += 1
        self.last_filters = dict(filters)
        return [dict(row) for row in self.rows]


def _fake_answer(query, candidates, assumptions, clusters, **kwargs):
    """按收到的候选原样产出引用，便于断言越权候选是否漏进答案。"""
    return {
        "status": "ok",
        "answer": "根据群聊原文……",
        "events": [],
        "evidence": [
            {"msg_id": c["msg_id"], "quote": c["content"][:10]} for c in candidates
        ],
    }


@pytest.fixture(autouse=True)
def _debug_enabled():
    old = settings.DEBUG
    settings.DEBUG = True
    yield
    settings.DEBUG = old


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch):
    monkeypatch.setattr(agent_module, "parse_query", lambda q, today=None: dict(PARSE))
    monkeypatch.setattr(
        agent_module, "load_clusters", lambda candidates, db_path=None: []
    )
    monkeypatch.setattr(agent_module, "generate_answer", _fake_answer)


# ---------------------------------------------------------------------------
# retrieve：权限过滤与计数
# ---------------------------------------------------------------------------


def test_retrieve_drops_out_of_scope_candidate():
    """候选 group_id 不在 group_scope → 丢弃并计数，不进 results。"""
    provider = Provider([IN_SCOPE, OUT_SCOPE])
    out = retrieve(PARSE, provider, group_scope=["g1"])

    assert out["status"] == "ok"
    assert [r["msg_id"] for r in out["results"]] == ["m-in"]
    assert out["debug"]["dropped_out_of_scope"] == 1
    # 权限字段必须随 filters 一起交给 provider
    assert provider.last_filters["group_scope"] == ["g1"]
    assert provider.last_filters["allowed_msg_ids"] is None


def test_retrieve_drops_msg_outside_allowlist():
    """allowed_msg_ids 白名单之外的候选 → 丢弃并计数。"""
    provider = Provider([IN_SCOPE, OUT_SCOPE])
    out = retrieve(PARSE, provider, allowed_msg_ids=["m-in"])

    assert [r["msg_id"] for r in out["results"]] == ["m-in"]
    assert out["debug"]["dropped_not_allowed"] == 1
    assert provider.last_filters["allowed_msg_ids"] == ["m-in"]


def test_retrieve_without_any_permission_is_denied():
    """既无 group_scope 也无 allowed_msg_ids → permission_denied，且不调用 provider。"""
    provider = Provider([IN_SCOPE])
    out = retrieve(PARSE, provider)

    assert out["status"] == "permission_denied"
    assert out["results"] == []
    assert out["message"] == agent_module.MESSAGE_PERMISSION_DENIED
    assert provider.calls == 0


# ---------------------------------------------------------------------------
# process：越权候选不进答案；缺权限不调大模型
# ---------------------------------------------------------------------------


def test_process_answer_never_cites_out_of_scope_candidate():
    """provider 混入跨群候选 → 答案 evidence 只能引用授权范围内的消息。"""
    provider = Provider([IN_SCOPE, OUT_SCOPE])
    response = process(
        "团建在哪",
        None,
        provider,
        InMemorySessionStore(),
        today=TODAY,
        group_scope=["g1"],
    )

    assert response["status"] == "answer"
    cited = {item["msg_id"] for item in response["evidence"]}
    assert cited == {"m-in"}
    assert "m-out" not in cited


def test_process_without_permission_denied_and_no_llm(monkeypatch):
    """缺权限参数 → permission_denied，解析与答案生成都不调用大模型。"""
    llm_calls: list = []
    monkeypatch.setattr(
        agent_module,
        "parse_query",
        lambda q, today=None: llm_calls.append(q) or dict(PARSE),
    )
    monkeypatch.setattr(
        agent_module,
        "generate_answer",
        lambda *a, **k: llm_calls.append("answer") or {"status": "ok", "answer": ""},
    )

    provider = Provider([IN_SCOPE])
    response = process(
        "团建在哪", None, provider, InMemorySessionStore(), today=TODAY
    )

    assert response["status"] == "permission_denied"
    assert response["message"] == agent_module.MESSAGE_PERMISSION_DENIED
    assert response["answer"] is None
    assert response["evidence"] == []
    assert llm_calls == []
    assert provider.calls == 0


# ---------------------------------------------------------------------------
# L3：group_scope 必须继续传到 validate_answer
# ---------------------------------------------------------------------------


def test_process_forwards_scope_to_answer_generation(monkeypatch):
    """process 的 group_scope / allowed_msg_ids 必须透传给答案生成。"""
    captured: dict = {}

    def _capture(query, candidates, assumptions, clusters, **kwargs):
        captured.update(kwargs)
        return {"status": "ok", "answer": "", "events": [], "evidence": []}

    monkeypatch.setattr(agent_module, "generate_answer", _capture)
    provider = Provider([IN_SCOPE])

    response = process(
        "团建在哪",
        None,
        provider,
        InMemorySessionStore(),
        today=TODAY,
        group_scope=["g1"],
        allowed_msg_ids=["m-in"],
    )

    assert response["status"] == "answer"
    assert captured["group_scope"] == ["g1"]
    assert captured["allowed_msg_ids"] == ["m-in"]


def test_generate_answer_l3_blocks_cross_group_when_scope_passed(monkeypatch):
    """直连 generate_answer 传 group_scope 时，跨群引用必须被 L3 拦住。"""
    raw = {
        "answer": "别的群周五有火锅局",
        "events": [
            {
                "summary": None,
                "time": None,
                "time_raw": None,
                "deadline": None,
                "location": None,
                "participants": None,
                "file_name": None,
                "sources": [
                    {"msg_id": "m-out", "quote": "别的群火锅局定在周五"}
                ],
            }
        ],
    }
    monkeypatch.setattr(answer_module, "chat_json", lambda messages: raw)

    result = generate_answer(
        "周五有火锅吗", [OUT_SCOPE], group_scope=["g1"]
    )

    assert result["status"] == "uncertain"
    assert result["reason"] == "reference_not_authorized"
    assert any(c.get("check") == "L3" for c in result["conflicts"])
