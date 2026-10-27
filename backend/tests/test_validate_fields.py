"""U2：答案字段级校验（L1 引用 / L2 可推导 / L3 冲突）测试。

全部为纯函数测试，不调用大模型、不查数据库。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.validate import validate_answer


def _corpus(*entries):
    """entries: (msg_id, content, send_time)"""
    return {
        msg_id: {"content": content, "send_time": send_time}
        for msg_id, content, send_time in entries
    }


def _event(**kwargs):
    event = {
        "summary": None,
        "time": None,
        "time_raw": None,
        "location": None,
        "participants": None,
        "file_name": None,
        "sources": [],
    }
    event.update(kwargs)
    return event


def test_location_mismatch_dropped_and_conflict_marked():
    """a. 原文"南门"、答案"北门" → 字段被剔除并标 conflict，最终答案不出现"北门"。"""
    corpus = _corpus(("m1", "集合地点南门，周六下午六点集合", "2026-10-20 10:00:00"))
    raw = {
        "answer": "在北门集合",
        "events": [
            _event(
                time_raw="周六下午六点",
                location="北门",
                sources=[{"msg_id": "m1", "quote": "集合地点南门，周六下午六点集合"}],
            )
        ],
    }

    result = validate_answer(raw, corpus)

    assert result["status"] == "ok"
    event = result["events"][0]
    assert event["location"] is None
    conflict = event["conflicts"]["location"]
    assert conflict["reason"] == "conflicting_evidence"
    assert conflict["claimed"] == "北门"
    assert conflict["candidates"][0]["value"] == "南门"
    assert "北门" not in result["answer"]
    assert "存在不一致信息" in result["answer"]


def test_relative_time_correct_date_passes():
    """b. 原文"下周六"、答案给出换算后的正确日期 → 通过。"""
    corpus = _corpus(("m1", "下周六下午三点开班会，地点另行通知", "2026-10-24 09:00:00"))
    raw = {
        "answer": "下周六下午三点开班会",
        "events": [
            _event(
                time="2026-10-31 15:00:00",
                time_raw="下周六下午三点",
                sources=[
                    {"msg_id": "m1", "quote": "下周六下午三点开班会，地点另行通知"}
                ],
            )
        ],
    }

    result = validate_answer(raw, corpus)

    assert result["status"] == "ok"
    event = result["events"][0]
    assert event["time"] == "2026-10-31 15:00:00"
    assert event["time_raw"] == "下周六下午三点"
    assert any(
        ev["field"] == "time" and ev["method"] == "token_match"
        for ev in event["field_evidence"]
    )
    assert "2026-10-31" in result["answer"] or "下周六下午三点" in result["answer"]


def test_relative_time_wrong_date_dropped():
    """c. 原文"下周六"、答案给出错误日期 → 时间字段被剔除。"""
    corpus = _corpus(("m1", "下周六下午三点开班会，地点另行通知", "2026-10-24 09:00:00"))
    raw = {
        "answer": "下周六（2026-11-07）下午三点开班会",
        "events": [
            _event(
                time="2026-11-07 15:00:00",
                time_raw="下周六下午三点",
                sources=[
                    {"msg_id": "m1", "quote": "下周六下午三点开班会，地点另行通知"}
                ],
            )
        ],
    }

    result = validate_answer(raw, corpus)

    event = result["events"][0]
    assert event["time"] is None
    assert "time" in event["conflicts"]
    assert event["conflicts"]["time"]["candidates"][0]["value"] == "2026-10-31"
    assert "2026-11-07" not in result["answer"]


def test_conflicting_locations_across_sources():
    """d. 两个来源分别写南门、北门 → conflict，展示"存在不一致信息"。"""
    corpus = _corpus(
        ("m1", "集合地点南门，周六下午六点集合", "2026-10-20 10:00:00"),
        ("m2", "改到北门集合了，周六下午六点", "2026-10-21 10:00:00"),
    )
    raw = {
        "answer": "在南门集合",
        "events": [
            _event(
                time_raw="周六下午六点",
                location="南门",
                sources=[
                    {"msg_id": "m1", "quote": "集合地点南门，周六下午六点集合"},
                    {"msg_id": "m2", "quote": "改到北门集合了，周六下午六点"},
                ],
            )
        ],
    }

    result = validate_answer(raw, corpus)

    event = result["events"][0]
    assert event["location"] is None
    candidates = event["conflicts"]["location"]["candidates"]
    assert len(candidates) == 2
    assert {c["msg_id"] for c in candidates} == {"m1", "m2"}
    assert "存在不一致信息" in result["answer"]


def test_all_events_unverifiable_returns_uncertain():
    """e. 全部 event 被剔除 → uncertain，不返回任何结论句。"""
    corpus = _corpus(("m1", "大家记得带身份证", "2026-10-20 10:00:00"))
    raw = {
        "answer": "集合地点在南门，时间是周六下午六点",
        "events": [
            _event(
                location=None,
                time=None,
                time_raw=None,
                sources=[{"msg_id": "m1", "quote": "记得带身份证"}],
            )
        ],
    }

    result = validate_answer(raw, corpus)

    assert result["status"] == "uncertain"
    assert result["answer"] == ""
    assert result["events"] == []
    assert "南门" not in result["answer"]


def test_rebuilt_answer_never_contains_dropped_value():
    """f. 过滤后重建的 answer 不包含被剔除字段的值（"北门"不出现）。"""
    corpus = _corpus(
        ("m1", "集合地点南门，周六下午六点集合", "2026-10-20 10:00:00"),
        ("m2", "改到北门集合了，周六下午六点", "2026-10-21 10:00:00"),
    )
    raw = {
        "answer": "北门集合，原定周六下午六点",  # 模型原文含被剔除的值
        "events": [
            _event(
                time_raw="周六下午六点",
                location="南门",
                sources=[{"msg_id": "m1", "quote": "集合地点南门，周六下午六点集合"}],
            ),
            _event(
                time_raw="周六下午六点",
                location="北门",
                sources=[
                    {"msg_id": "m1", "quote": "集合地点南门，周六下午六点集合"},
                    {"msg_id": "m2", "quote": "改到北门集合了，周六下午六点"},
                ],
            ),
        ],
    }

    result = validate_answer(raw, corpus)

    assert result["status"] == "ok"
    assert "南门" in result["answer"]
    assert "北门" not in result["answer"]
    assert "存在不一致信息" in result["answer"]


def test_fabricated_quote_still_no_result():
    """L1 未被削弱：所有引用都不是原文时仍是 no_result（区别于 uncertain）。"""
    corpus = _corpus(("m1", "集合地点南门", "2026-10-20 10:00:00"))
    raw = {
        "answer": "在南门集合",
        "events": [
            _event(
                location="南门",
                sources=[{"msg_id": "m1", "quote": "改到北门集合"}],
            )
        ],
    }

    result = validate_answer(raw, corpus)

    assert result["status"] == "no_result"
    assert result["events"] == []
