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


# ---------------------------------------------------------------------------
# T3：五级校验（L1 真实性 / L2 可推导 / L3 归属 / L4 版本 / L5 全文一致）
# ---------------------------------------------------------------------------


def _full_corpus(*entries):
    """entries: (msg_id, content, send_time, extra_dict)。

    extra_dict 可含 group_id / msg_status / version / edited_at。
    """
    corpus = {}
    for msg_id, content, send_time, extra in entries:
        corpus[msg_id] = {
            "content": content,
            "send_time": send_time,
            **extra,
        }
    return corpus


def _source(msg_id, quote):
    return {"msg_id": msg_id, "quote": quote}


def test_l2_location_mismatch_is_blocked_and_uncertain():
    """1. 原文"南门"、答案"北门"+真实引用 → L2 拦截，字段 null，status=uncertain。"""
    corpus = _full_corpus(
        ("m1", "集合地点南门", "2026-10-20 10:00:00", {"group_id": "g1"})
    )
    raw = {
        "answer": "在北门集合",
        "events": [
            _event(
                location="北门",
                sources=[_source("m1", "集合地点南门")],
            )
        ],
    }

    result = validate_answer(raw, corpus, group_scope=["g1"])

    assert result["status"] == "uncertain"
    assert result["events"] == []
    conflict = next(c for c in result["conflicts"] if c.get("check") == "L2")
    assert conflict["field"] == "location"
    assert conflict["claimed"] == "北门"
    assert conflict["candidates"][0]["value"] == "南门"


def test_l2_relative_week_offset_mismatch_dropped():
    """2. 原文"这周六"、答案"下周六" → 换算后日期不一致，时间字段被拦。"""
    corpus = _full_corpus(
        (
            "m1",
            "这周六下午三点开班会，地点另行通知",
            "2026-10-24 09:00:00",
            {"group_id": "g1"},
        )
    )
    raw = {
        "answer": "下周六下午三点开班会",
        "events": [
            _event(
                time="2026-10-31 15:00:00",
                time_raw="下周六下午三点",
                sources=[_source("m1", "这周六下午三点开班会，地点另行通知")],
            )
        ],
    }

    result = validate_answer(raw, corpus, group_scope=["g1"])

    assert result["status"] == "uncertain"
    assert result["events"] == []
    conflict = next(c for c in result["conflicts"] if c.get("check") == "L2")
    assert conflict["field"] == "time"
    assert conflict["claimed"] == "2026-10-31 15:00:00"
    assert conflict["candidates"][0]["value"] == "2026-10-24"


def test_l3_cross_group_reference_rejected():
    """3. 引用 msg_id 不在本次授权群范围 → L3 失败，不进事件。"""
    corpus = _full_corpus(
        ("m1", "集合地点南门", "2026-10-20 10:00:00", {"group_id": "g2"})
    )
    raw = {
        "answer": "在南门集合",
        "events": [
            _event(
                location="南门",
                sources=[_source("m1", "集合地点南门")],
            )
        ],
    }

    result = validate_answer(raw, corpus, group_scope=["g1"])

    assert result["status"] == "uncertain"
    assert result["events"] == []
    conflicts = [c for c in result["conflicts"] if c.get("check") == "L3"]
    assert conflicts and conflicts[0]["msg_id"] == "m1"
    assert conflicts[0]["reason"] == "reference_not_authorized"


def test_l4_revoked_reference_rejected_and_field_null():
    """4. 引用 revoked 消息 → L4 失败，该引用支撑的时间字段置 null。"""
    corpus = _full_corpus(
        ("m1", "集合地点南门", "2026-10-20 10:00:00", {"group_id": "g1"}),
        (
            "m2",
            "10月20日下午六点开始",
            "2026-10-20 10:00:00",
            {"group_id": "g1", "msg_status": "revoked"},
        ),
    )
    raw = {
        "answer": "南门集合，六点开始",
        "events": [
            _event(
                location="南门",
                time="2026-10-20 18:00:00",
                time_raw="六点",
                sources=[
                    _source("m1", "集合地点南门"),
                    _source("m2", "10月20日下午六点开始"),
                ],
            )
        ],
    }

    result = validate_answer(raw, corpus, group_scope=["g1"])

    # revoked 引用被剔除，事件靠 location 存活，但时间字段必须为 null
    assert result["events"][0]["time"] is None
    assert "m2" not in result["events"][0]["msg_ids"]
    conflicts = [c for c in result["conflicts"] if c.get("check") == "L4"]
    assert conflicts and conflicts[0]["msg_id"] == "m2"
    assert conflicts[0]["reason"] == "revoked_message"


def test_filtered_event_removed_from_rebuilt_answer():
    """5. 两个 event 冲突删掉一个 → 重建 answer 不含被删 event 的任何字段。"""
    corpus = _full_corpus(
        (
            "m1",
            "周六下午六点在南门集合",
            "2026-10-20 10:00:00",
            {"group_id": "g1"},
        )
    )
    raw = {
        # 模型原文同时含两个 event 的值；最终 answer 必须只来自保留下来的 event
        "answer": "南门集合，六点开始；北门集合，十点开始",
        "events": [
            _event(
                location="南门",
                time="2026-10-24 18:00:00",
                time_raw="周六下午六点",
                sources=[_source("m1", "周六下午六点在南门集合")],
            ),
            _event(
                location="北门",
                time="2026-10-24 10:00:00",
                time_raw="十点",
                sources=[_source("m9", "北门集合，十点开始")],
            ),
        ],
    }

    result = validate_answer(raw, corpus, group_scope=["g1"])

    assert result["status"] == "ok"
    assert len(result["events"]) == 1
    assert "北门" not in result["answer"]
    assert "十点" not in result["answer"]
    assert "南门" in result["answer"]


def test_all_fields_verified_and_evidence_derived():
    """6. 全部通过 → status=ok；每个字段都能指到 quote 里的一段字，且 evidence 非空。"""
    corpus = _full_corpus(
        (
            "m1",
            "班长：周六晚上6点在南门集合去团建",
            "2026-10-20 10:00:00",
            {"group_id": "g1", "version": 2},
        )
    )
    raw = {
        "answer": "周六晚上6点在南门集合去团建",
        "events": [
            _event(
                time="2026-10-24 18:00:00",
                time_raw="周六晚上6点",
                location="南门",
                participants=["班长"],
                sources=[_source("m1", "班长：周六晚上6点在南门集合去团建")],
            )
        ],
    }

    result = validate_answer(raw, corpus, group_scope=["g1"])

    assert result["status"] == "ok"
    assert result["events"]
    for item in result["events"][0]["field_evidence"]:
        content = corpus[item["msg_id"]]["content"]
        assert item["quote"] in content

    # T3 前置修复：evidence 由 events[].sources 派生，不再恒为空
    assert result["evidence"]
    for item in result["evidence"]:
        assert item["quote"] in corpus[item["msg_id"]]["content"]
        assert item["field"] in {"time", "time_raw", "location", "participants", "file_name"}


def test_l4_edited_reference_allowed_and_annotated():
    """L4 补充：edited 允许引用，但最终 answer 必须标注"（该消息已编辑）"。"""
    corpus = _full_corpus(
        (
            "m1",
            "集合地点南门（已修改）",
            "2026-10-20 10:00:00",
            {"group_id": "g1", "msg_status": "edited", "version": 3},
        )
    )
    raw = {
        "answer": "在南门集合",
        "events": [
            _event(
                location="南门",
                sources=[_source("m1", "集合地点南门（已修改）")],
            )
        ],
    }

    result = validate_answer(raw, corpus, group_scope=["g1"])

    assert result["status"] == "ok"
    assert "（该消息已编辑）" in result["answer"]
    assert result["events"][0]["edited_msg_ids"] == ["m1"]
