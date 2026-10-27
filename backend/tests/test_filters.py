"""U3 检索条件生效测试：解析条件 → filters → 硬过滤 / 软加权。

全程离线：关键词与向量检索用固定命中替代，只验证条件是否真的作用于结果集。
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app.agent as agent_module
import app.llm as llm_module
import app.search_hybrid as search_module
from app.agent import answer_flow, build_search_filters, reset_session
from app.db import get_conn, init_db
from app.search_hybrid import hybrid_search

TODAY = "2026-10-21"  # 周三：本周 10-19 ~ 10-25
THIS_WEEK = {
    "type": "relative",
    "start": "2026-10-19 00:00:00",
    "end": "2026-10-25 23:59:59",
    "raw": "这周",
}
LAST_WEEK = {
    "type": "relative",
    "start": "2026-10-12 00:00:00",
    "end": "2026-10-18 23:59:59",
    "raw": "上周",
}

# 种子数据：
# m3 发送时间在 9 月、活动在本周（用于验证 event_time 过滤仍能召回）
# m4 发送时间在本周、活动在 9 月（用于验证 event_time 过滤会排除）
MESSAGES = [
    ("m1", "g1", "班长", "2026-10-20 10:00:00", "秋季志愿活动报名表.pdf"),
    ("m2", "g1", "班长", "2026-10-20 10:05:00", "秋季志愿活动介绍"),
    ("m3", "g1", "小A", "2026-09-30 20:00:00", "这周六志愿活动集合通知"),
    ("m4", "g2", "小B", "2026-10-22 09:00:00", "上周讲座录音整理"),
]

LEADS = [
    ("l1", "m1", "file", "志愿活动报名表", "2026-10-24 09:00", None, None, None, "报名表", 0.9),
    ("l2", "m2", "event", "秋季志愿活动", "2026-10-24 09:00", None, None, None, None, 0.9),
    ("l3", "m3", "event", "周六志愿活动", "2026-10-24 09:00", None, None, None, None, 0.9),
    ("l4", "m4", "file", "上周讲座录音", "2026-09-18 19:00", None, None, None, "录音", 0.9),
]


def _seed(db_path: str) -> None:
    """建库并写入固定的消息与线索。"""
    conn = init_db(db_path)
    try:
        conn.executemany(
            "INSERT INTO messages (msg_id, group_id, sender, send_time, content, content_terms) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(m[0], m[1], m[2], m[3], m[4], m[4]) for m in MESSAGES],
        )
        conn.executemany(
            "INSERT INTO leads (lead_id, msg_id, category, title, event_time, time_raw, "
            "location, participants, file_hint, confidence) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            LEADS,
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "filters.db")
    _seed(path)
    return path


def _hit(msg_id: str) -> dict:
    """构造一条固定检索命中（内容与发送时间来自种子数据）。"""
    row = next(m for m in MESSAGES if m[0] == msg_id)
    return {
        "msg_id": row[0],
        "content": row[4],
        "sender": row[2],
        "send_time": row[3],
        "score": 0.5,
        "rank": 0.0,
    }


@pytest.fixture
def fake_hits(monkeypatch):
    """替换 kw / vec 检索，命中集合由用例指定。"""
    hits: dict = {"kw": [], "vec": []}

    def _kw(query, limit=50, db_path=None):
        return [dict(h) for h in hits["kw"]]

    def _vec(query, top_k=50, db_path=None):
        return [dict(h) for h in hits["vec"]]

    monkeypatch.setattr(search_module, "kw_search", _kw)
    monkeypatch.setattr(search_module, "vec_search", _vec)
    return hits


def _categories(db_path: str, msg_ids: list[str]) -> set:
    """查询结果消息对应的线索类别，用于断言"没有 category=event 的条目"。"""
    conn = get_conn(db_path)
    try:
        placeholders = ",".join("?" for _ in msg_ids)
        rows = conn.execute(
            f"SELECT category FROM leads WHERE msg_id IN ({placeholders})", msg_ids
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


# ---------------- a. 明确条件 → 硬过滤 ----------------


def test_explicit_file_filter_changes_result_set(db, fake_hits):
    """"找报名表，不是活动介绍" → category:file 硬过滤，结果集与无过滤不同。"""
    fake_hits["kw"] = [_hit("m1"), _hit("m2")]
    fake_hits["vec"] = [_hit("m2"), _hit("m1")]

    base = hybrid_search("报名表 活动介绍", limit=10, db_path=db)
    base_ids = {r["msg_id"] for r in base["results"]}

    parse = {
        "keywords": ["报名表"],
        "time": {"type": "none", "start": None, "end": None, "raw": None},
        "time_field": "event_time",
        "category": "file",
        "file_hint": "报名表",
        "filters": {
            "explicit": ["category:file", "file_hint:报名表"],
            "vague": [],
        },
        "ambiguous": None,
    }
    filtered = hybrid_search(
        "报名表 活动介绍", limit=10, filters=build_search_filters(parse), db_path=db
    )
    ids = {r["msg_id"] for r in filtered["results"]}

    assert "category:file" in filtered["debug"]["applied_filters"]
    assert ids == {"m1"}
    assert ids != base_ids  # msg_id 集合确实发生变化
    assert "m2" not in ids
    assert _categories(db, list(ids)) == {"file"}  # 没有 category=event 的条目


# ---------------- b. 事件时间过滤 ----------------


def test_event_time_filter_recalls_message_sent_outside_week(db, fake_hits):
    """"这周志愿活动" → 按 event_time 过滤；发送时间在 9 月但活动在本周仍召回。"""
    fake_hits["kw"] = [_hit("m3"), _hit("m4")]
    fake_hits["vec"] = [_hit("m4"), _hit("m3")]

    parse = {
        "keywords": ["志愿", "活动"],
        "time": THIS_WEEK,
        "time_field": "event_time",
        "category": "event",
        "file_hint": None,
        "filters": {"explicit": ["time:这周"], "vague": []},
        "ambiguous": None,
    }
    filters = build_search_filters(parse)
    out = hybrid_search("这周志愿活动", limit=10, filters=filters, db_path=db)
    ids = {r["msg_id"] for r in out["results"]}

    assert "m3" in ids  # send_time=2026-09-30 不在本周，但 event_time 在本周
    assert "m4" not in ids  # send_time 在本周，但 event_time 在 9 月
    assert "time_field:event_time" in out["debug"]["applied_filters"]


# ---------------- c. 发送时间字段 ----------------


def _json(obj):
    return json.dumps(obj, ensure_ascii=False)


def test_send_time_field_from_parse(monkeypatch):
    """"群里上周说的那个讲座" → parse.time_field=send_time，并透传到检索 filters。"""
    reset_session("f-send")
    parse_response = {
        "keywords": ["讲座"],
        "time": LAST_WEEK,
        "time_field": "send_time",
        "category": "any",
        "file_hint": None,
        "filters": {"explicit": ["time:上周"], "vague": []},
        "ambiguous": None,
    }

    class FakeLLM:
        def __call__(self, messages, timeout=None):
            return _json(parse_response)

    calls: list[dict] = []

    def fake_search(**kwargs):
        calls.append(kwargs)
        return {
            "results": [
                {
                    "msg_id": "m4",
                    "content": "上周讲座录音整理",
                    "sender": "小B",
                    "send_time": "2026-10-15 09:00:00",
                    "score": 0.03,
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

    monkeypatch.setattr(llm_module, "_call_llm", FakeLLM())
    monkeypatch.setattr(agent_module, "hybrid_search", fake_search)
    monkeypatch.setattr(
        agent_module,
        "generate_answer",
        lambda *a, **k: {"status": "ok", "answer": "", "events": []},
    )

    try:
        result = answer_flow("群里上周说的那个讲座", "f-send", today=TODAY)

        assert result["parse"]["time_field"] == "send_time"
        sent_filters = calls[0]["filters"]
        assert sent_filters["time_field"] == "send_time"
        assert sent_filters["time_range"]["start"] == "2026-10-12 00:00:00"
        assert result["search_debug"]["result_count_after_filter"] == 1
    finally:
        reset_session("f-send")


# ---------------- d. 模糊条件 → 软加权，不做硬过滤 ----------------


def test_vague_time_goes_soft_no_hard_filter(db, fake_hits):
    """"好像在十月左右有个活动" → 不产生硬过滤，只软加权，结果非空。"""
    fake_hits["kw"] = [_hit("m1"), _hit("m2"), _hit("m3"), _hit("m4")]
    fake_hits["vec"] = [_hit("m4"), _hit("m3"), _hit("m2"), _hit("m1")]

    parse = {
        "keywords": ["活动"],
        "time": {"type": "none", "start": None, "end": None, "raw": None},
        "time_field": "event_time",
        "category": "event",
        "file_hint": None,
        "filters": {"explicit": [], "vague": ["time:十月左右"]},
        "ambiguous": None,
    }
    filters = build_search_filters(parse)
    out = hybrid_search("十月 活动", limit=10, filters=filters, db_path=db)

    assert filters["time_range"] is None  # 模糊时间不产生硬过滤
    assert filters["category"] is None
    assert filters["soft_boost"]["category"] == "event"
    assert out["debug"]["applied_filters"] == []
    assert out["results"]  # 结果非空
    assert any(r["soft_matched"] for r in out["results"])


# ---------------- e/f. 兼容与降级 ----------------


def test_no_filters_backward_compatible(db, fake_hits):
    """不传 filters → 不做硬过滤，过滤前后数量一致。"""
    fake_hits["kw"] = [_hit(m[0]) for m in MESSAGES]
    fake_hits["vec"] = []

    out = hybrid_search("任意查询", limit=10, db_path=db)

    assert len(out["results"]) == len(MESSAGES)
    assert out["debug"]["applied_filters"] == []
    assert out["debug"]["result_count_before_filter"] == len(MESSAGES)
    assert out["debug"]["result_count_after_filter"] == len(MESSAGES)


def test_invalid_filters_degrade(db, fake_hits):
    """非法 category / time_field 降级为不生效，检索不抛错。"""
    fake_hits["kw"] = [_hit(m[0]) for m in MESSAGES]
    fake_hits["vec"] = []

    out = hybrid_search(
        "任意查询",
        limit=10,
        filters={"category": "bad", "time_field": "create_time"},
        db_path=db,
    )

    assert len(out["results"]) == len(MESSAGES)
    assert out["debug"]["applied_filters"] == []


# ---------------- g. 软加权只改分数与标记 ----------------


def test_soft_boost_keeps_result_set_only_raises_score(db, fake_hits):
    """soft_boost 不改变结果集，只把命中候选分数 ×1.15 并标 soft_matched。"""
    fake_hits["kw"] = [_hit("m1"), _hit("m2")]
    fake_hits["vec"] = []
    base = hybrid_search("活动", limit=10, db_path=db)
    base_scores = {r["msg_id"]: r["score"] for r in base["results"]}

    boosted = hybrid_search(
        "活动",
        limit=10,
        filters={"soft_boost": {"category": "event", "file_hint": None}},
        db_path=db,
    )
    ids = {r["msg_id"] for r in boosted["results"]}
    assert ids == {r["msg_id"] for r in base["results"]}  # 结果集不变

    m2 = next(r for r in boosted["results"] if r["msg_id"] == "m2")
    m1 = next(r for r in boosted["results"] if r["msg_id"] == "m1")
    assert m2["soft_matched"] is True
    assert m1["soft_matched"] is False
    assert m2["score"] == pytest.approx(base_scores["m2"] * 1.15)


# ---------------- h. 解析侧 filters 规整 ----------------


def test_parse_filter_lists_normalized():
    """explicit/vague 只保留合法键，去重；非法 time_field 降级为 event_time。"""
    parse = agent_module._normalize_parse(
        {
            "keywords": ["报名表"],
            "time": {"type": "none", "start": None, "end": None, "raw": None},
            "time_field": "create_time",
            "category": "file",
            "file_hint": "报名表",
            "filters": {
                "explicit": ["category:file", "category:file", "foo:bar", "time:这周"],
                "vague": ["time:上周左右", "no colon"],
            },
            "ambiguous": None,
        }
    )

    assert parse["time_field"] == "event_time"
    assert parse["filters"]["explicit"] == ["category:file", "time:这周"]
    assert parse["filters"]["vague"] == ["time:上周左右"]
