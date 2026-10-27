"""事件关联测试（纯规则，不调用大模型）。

覆盖：同名不同天不合并、同名同天合并、改期标记且原时间保留、取消标记、
单条自成 cluster、结果可复现。
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.cluster import cluster_leads


def _lead(
    lead_id,
    msg_id,
    title,
    event_time,
    content,
    send_time,
    group_id="g1",
    category="event",
    time_raw=None,
):
    return {
        "lead_id": lead_id,
        "msg_id": msg_id,
        "group_id": group_id,
        "category": category,
        "title": title,
        "event_time": event_time,
        "time_raw": time_raw,
        "location": None,
        "participants": None,
        "file_hint": None,
        "confidence": 0.8,
        "send_time": send_time,
        "content": content,
    }


def _split(result):
    merged = [c for c in result if not c["need_review"]]
    review = [c for c in result if c["need_review"]]
    return merged, review


def test_same_title_different_day_not_merged():
    """同名活动不同两天 → 不合并，输出 need_review 候选。"""
    l1 = _lead("l1", "m1", "班级团建", "2026-10-25 18:00:00",
               "周六团建，下午6点南门集合", "2026-10-20 10:00:00")
    l2 = _lead("l2", "m2", "班级团建", "2026-11-01 18:00:00",
               "下周再聚一次", "2026-10-21 10:00:00")

    merged, review = _split(cluster_leads([l1, l2]))

    assert len(merged) == 2
    assert all(len(c["members"]) == 1 for c in merged)
    assert len(review) == 1
    assert review[0]["need_review"] is True
    assert len(review[0]["members"]) == 2


def test_same_title_same_day_merged():
    """同名同天 + 时间接近 → 合并为一个 cluster。"""
    l1 = _lead("l1", "m1", "班级团建", "2026-10-25 18:00:00",
               "周六团建，下午6点南门集合", "2026-10-20 10:00:00")
    l2 = _lead("l2", "m2", "班级团建活动", "2026-10-25 19:00:00",
               "团建晚上7点开始", "2026-10-20 11:00:00")

    merged, review = _split(cluster_leads([l1, l2]))

    assert len(merged) == 1
    assert len(merged[0]["members"]) == 2
    assert merged[0]["need_review"] is False
    assert review == []


def test_reschedule_marker_keeps_original_time():
    """"改到周日上午" → cluster 带 changes，原时间仍在，旧值与新值同时保留。"""
    l1 = _lead("l1", "m1", "班级团建", "2026-10-25 10:00:00",
               "周六团建，上午10点南门集合", "2026-10-20 10:00:00")
    l2 = _lead("l2", "m2", "班级团建", "2026-10-26 11:00:00",
               "团建改到周日上午11点了", "2026-10-21 09:00:00")
    l2["changes"] = [
        {
            "change_id": "c1",
            "msg_id": "m2",
            "type": "reschedule_time",
            "field": "event_time",
            "old_value": "2026-10-25 10:00:00",
            "new_value": "2026-10-26 11:00:00",
            "quote": "改到周日上午11点",
            "confidence": 0.9,
        }
    ]

    merged, _ = _split(cluster_leads([l1, l2]))

    assert len(merged) == 1
    cluster = merged[0]
    # 原时间保留，不被覆盖
    assert cluster["event_time"] == "2026-10-25 10:00:00"
    assert cluster["status"] == "merged"
    assert len(cluster["changes"]) == 1
    change = cluster["changes"][0]
    assert change["type"] == "reschedule_time"
    assert change["old_value"] == "2026-10-25 10:00:00"
    assert change["new_value"] == "2026-10-26 11:00:00"
    assert change["msg_id"] == "m2"
    # 两个时间同时存在，可都展示
    assert cluster["event_time"] != change["new_value"]


def test_cancel_marker():
    """"取消今晚的班会" → cancel 变更，原时间保留。"""
    l1 = _lead("l1", "m1", "班会", "2026-10-25 19:00:00",
               "今晚7点开班会", "2026-10-25 09:00:00")
    l2 = _lead("l2", "m2", "班会", None,
               "取消今晚的班会", "2026-10-25 15:00:00")
    l2["changes"] = [
        {
            "change_id": "c2",
            "msg_id": "m2",
            "type": "cancel",
            "field": None,
            "old_value": None,
            "new_value": None,
            "quote": "取消今晚的班会",
            "confidence": 0.95,
        }
    ]

    merged, _ = _split(cluster_leads([l1, l2]))

    assert len(merged) == 1
    cluster = merged[0]
    assert cluster["event_time"] == "2026-10-25 19:00:00"
    assert cluster["changes"][0]["type"] == "cancel"
    assert cluster["changes"][0]["quote"] == "取消今晚的班会"


def test_single_lead_cluster():
    """单条 lead 自成 cluster，无 changes。"""
    l1 = _lead("l1", "m1", "班级团建", "2026-10-25 18:00:00",
               "周六团建", "2026-10-20 10:00:00")

    result = cluster_leads([l1])

    assert len(result) == 1
    assert result[0]["need_review"] is False
    assert len(result[0]["members"]) == 1
    assert result[0]["changes"] == []


def test_cluster_result_deterministic():
    """同输入重复调用结果完全一致（cluster_id 稳定）。"""
    l1 = _lead("l1", "m1", "班级团建", "2026-10-25 10:00:00",
               "周六团建，上午10点南门集合", "2026-10-20 10:00:00")
    l2 = _lead("l2", "m2", "班级团建", "2026-10-26 11:00:00",
               "团建改到周日上午11点了", "2026-10-21 09:00:00")
    l3 = _lead("l3", "m3", "班级团建", "2026-11-08 18:00:00",
               "月底再聚一次", "2026-10-22 09:00:00")

    first = json.dumps(cluster_leads([l1, l2, l3]), ensure_ascii=False, sort_keys=True)
    second = json.dumps(cluster_leads([l1, l2, l3]), ensure_ascii=False, sort_keys=True)

    assert first == second


def test_different_group_not_merged():
    """同名同天但不同群且无第二信号 → 不合并，输出 pending_review。"""
    l1 = _lead("l1", "m1", "班级团建", "2026-10-25 18:00:00",
               "周六团建", "2026-10-20 10:00:00", group_id="g1")
    l2 = _lead("l2", "m2", "班级团建", "2026-10-25 18:00:00",
               "周六团建", "2026-10-20 10:00:00", group_id="g2")

    merged, review = _split(cluster_leads([l1, l2]))

    assert len(merged) == 2
    assert len(review) == 1
    assert review[0]["status"] == "pending_review"
