"""事件关联（cluster）接入答案链路的测试（mock 大模型）。"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app.llm as llm_module
import app.agent as agent_module
from app.agent import answer_flow, reset_session
from app.answer import generate_answer
from app.db import get_conn, init_db

GROUP = "g_class"

M1 = {
    "msg_id": "m1",
    "group_id": GROUP,
    "sender": "班长",
    "sender_id": "u1",
    "send_time": "2026-10-20 09:12:00",
    "content": "这周六团建，下午6点在南门集合",
}
M2 = {
    "msg_id": "m2",
    "group_id": GROUP,
    "sender": "班长",
    "sender_id": "u1",
    "send_time": "2026-10-22 20:35:00",
    "content": "团建改到周日上午10点了，还是南门集合",
}

CANDIDATES = [
    {
        "msg_id": "m1",
        "content": M1["content"],
        "sender": M1["sender"],
        "send_time": M1["send_time"],
        "score": 0.03,
        "sources": ["kw"],
    },
    {
        "msg_id": "m2",
        "content": M2["content"],
        "sender": M2["sender"],
        "send_time": M2["send_time"],
        "score": 0.02,
        "sources": ["vec"],
    },
]

PARSE_RESPONSE = {
    "keywords": ["团建"],
    "time": {"type": "none", "start": None, "end": None, "raw": None},
    "category": "event",
    "file_hint": None,
    "ambiguous": None,
}

BOTH_SOURCES_ANSWER = {
    "answer": "团建原定周六晚6点，已改到周日上午10点",
    "events": [
        {
            "summary": "班级团建原定 10-25 18:00，后改到 10-26 10:00",
            "time": "2026-10-26 10:00:00",
            "time_raw": "周日上午10点",
            "location": "南门",
            "participants": None,
            "sources": [
                {"msg_id": "m1", "quote": "这周六团建，下午6点在南门集合"},
                {"msg_id": "m2", "quote": "团建改到周日上午10点了"},
            ],
        }
    ],
}

SINGLE_SOURCE_ANSWER = {
    "answer": "周六下午6点在南门集合团建",
    "events": [
        {
            "summary": "班级团建",
            "time": None,
            "time_raw": "周六下午6点",
            "location": "南门",
            "participants": None,
            "sources": [{"msg_id": "m1", "quote": "下午6点在南门集合"}],
        }
    ],
}


def _seed(db_path: str, with_leads: bool = True) -> None:
    init_db(db_path).close()
    conn = get_conn(db_path)
    try:
        for m in (M1, M2):
            conn.execute(
                "INSERT INTO messages (msg_id, group_id, sender, sender_id, send_time, content) "
                "VALUES (?,?,?,?,?,?)",
                (m["msg_id"], m["group_id"], m["sender"], m["sender_id"], m["send_time"], m["content"]),
            )
        if with_leads:
            conn.executemany(
                "INSERT INTO leads (lead_id, msg_id, category, title, event_time, time_raw, "
                "location, participants, file_hint, confidence, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [
                    ("l1", "m1", "event", "班级团建", "2026-10-25 18:00:00", "周六下午6点", "南门", None, None, 0.9, "2026-10-20 09:20:00"),
                    ("l2", "m2", "event", "班级团建", "2026-10-26 10:00:00", "周日上午10点", "南门", None, None, 0.95, "2026-10-22 20:40:00"),
                ],
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _clean_sessions():
    reset_session("c1")
    yield
    reset_session("c1")


@pytest.fixture
def fake_llm(monkeypatch):
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


def _json(obj):
    return json.dumps(obj, ensure_ascii=False)


def test_reschedule_cluster_injected(fake_llm, monkeypatch, tmp_path):
    """改期场景：cluster 合并成功、原时间保留、correction 传给大模型。"""
    db_path = str(tmp_path / "cluster.db")
    _seed(db_path)
    fake_llm.responses = [_json(PARSE_RESPONSE), _json(BOTH_SOURCES_ANSWER)]
    monkeypatch.setattr(agent_module, "hybrid_search", lambda **kwargs: [dict(c) for c in CANDIDATES])

    response = answer_flow("团建什么时候", "c1", today="2026-10-25", db_path=db_path)

    clusters = response["clusters"]
    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster["need_review"] is False
    assert cluster["event_time"] == "2026-10-25 18:00:00"  # 原时间必须保留
    assert [m["msg_id"] for m in cluster["members"]] == ["m1", "m2"]
    assert cluster["corrections"] == [
        {"type": "reschedule", "new_time": "2026-10-26 10:00:00", "from_msg_id": "m2"}
    ]

    # 大模型输入里能看到 cluster（含 corrections）
    sent_clusters = fake_llm.payloads[1]["clusters"]
    assert len(sent_clusters) == 1
    assert sent_clusters[0]["corrections"][0]["type"] == "reschedule"


def test_both_quotes_kept(fake_llm, monkeypatch, tmp_path):
    """改期答案同时引用原通知与改期消息，两条都通过原文子串校验。"""
    db_path = str(tmp_path / "cluster.db")
    _seed(db_path)
    fake_llm.responses = [_json(PARSE_RESPONSE), _json(BOTH_SOURCES_ANSWER)]
    monkeypatch.setattr(agent_module, "hybrid_search", lambda **kwargs: [dict(c) for c in CANDIDATES])

    response = answer_flow("团建什么时候", "c1", today="2026-10-25", db_path=db_path)

    result = response["result"]
    assert result["status"] == "ok"
    event = result["events"][0]
    assert event["msg_ids"] == ["m1", "m2"]
    assert event["time"] == "2026-10-26 10:00:00"
    assert len(event["sources"]) == 2


def test_no_leads_clusters_empty(fake_llm, monkeypatch, tmp_path):
    """尚未抽取线索时 clusters 为空，答案链路不报错（降级为只用原文）。"""
    db_path = str(tmp_path / "noleads.db")
    _seed(db_path, with_leads=False)
    fake_llm.responses = [_json(PARSE_RESPONSE), _json(SINGLE_SOURCE_ANSWER)]
    monkeypatch.setattr(agent_module, "hybrid_search", lambda **kwargs: [dict(c) for c in CANDIDATES])

    response = answer_flow("团建什么时候", "c1", today="2026-10-25", db_path=db_path)

    assert response["status"] == "answer"
    assert response["clusters"] == []
    assert fake_llm.payloads[1]["clusters"] == []
    assert response["result"]["status"] == "ok"


def test_generate_answer_clusters_default(fake_llm):
    """不传 clusters 时兼容旧调用：payload.clusters 为空数组。"""
    fake_llm.responses = [_json(SINGLE_SOURCE_ANSWER)]

    result = generate_answer("团建什么时候", [dict(CANDIDATES[0])])

    assert fake_llm.payloads[0]["clusters"] == []
    assert result["status"] == "ok"
