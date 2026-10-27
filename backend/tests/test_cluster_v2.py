"""U4 关联逻辑 v2 测试：跨群第二信号、同名活动待确认、变更时序与旧值保留。"""

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
    sender_id="u1",
    location=None,
    changes=None,
):
    return {
        "lead_id": lead_id,
        "msg_id": msg_id,
        "group_id": group_id,
        "sender_id": sender_id,
        "category": "event",
        "title": title,
        "event_time": event_time,
        "time_raw": None,
        "location": location,
        "participants": None,
        "file_hint": None,
        "confidence": 0.9,
        "send_time": send_time,
        "content": content,
        "changes": changes or [],
    }


def _split(result):
    merged = [c for c in result if not c["need_review"]]
    review = [c for c in result if c["need_review"]]
    return merged, review


def test_same_name_different_class_pending_review():
    """e. 两个不同班同名"迎新晚会" → 不自动合并，输出 pending_review。"""
    l1 = _lead("l1", "m1", "迎新晚会", "2026-10-30 19:00:00",
               "我们班迎新晚会周五晚7点", "2026-10-25 10:00:00",
               group_id="g_class1", sender_id="u1")
    l2 = _lead("l2", "m2", "迎新晚会", "2026-10-30 19:00:00",
               "咱们班迎新晚会周五晚7点", "2026-10-25 11:00:00",
               group_id="g_class2", sender_id="u2")

    merged, review = _split(cluster_leads([l1, l2]))

    assert len(merged) == 2  # 未自动合并
    assert all(len(c["members"]) == 1 for c in merged)
    assert len(review) == 1
    pending = review[0]
    assert pending["status"] == "pending_review"
    assert pending["need_review"] is True
    assert len(pending["candidates"]) == 2
    assert {c["group_id"] for c in pending["candidates"]} == {"g_class1", "g_class2"}
    assert "缺少第二信号" in pending["review_reason"]


def test_cross_group_forward_with_same_sender_merged():
    """f. 同一活动转发到另一个群，同一发起人信号 → 自动关联并标 cross_group。"""
    l1 = _lead("l1", "m1", "迎新晚会", "2026-10-30 19:00:00",
               "我们班迎新晚会周五晚7点，报名链接 https://example.com/sign",
               "2026-10-25 10:00:00", group_id="g_class1", sender_id="u1")
    l2 = _lead("l2", "m2", "迎新晚会", "2026-10-30 19:00:00",
               "转发：迎新晚会周五晚7点，报名链接 https://example.com/sign",
               "2026-10-25 12:00:00", group_id="g_class2", sender_id="u1")

    merged, review = _split(cluster_leads([l1, l2]))

    assert len(merged) == 1
    assert review == []
    cluster = merged[0]
    assert cluster["status"] == "merged"
    assert cluster["cross_group"] is True
    assert cluster["groups"] == ["g_class1", "g_class2"]
    assert len(cluster["members"]) == 2


def test_cross_group_with_same_location_merged():
    """跨群但有同一地点信号 → 也可自动关联。"""
    l1 = _lead("l1", "m1", "迎新晚会", "2026-10-30 19:00:00",
               "晚会地点：大学生活动中心", "2026-10-25 10:00:00",
               group_id="g1", sender_id="u1", location="大学生活动中心")
    l2 = _lead("l2", "m2", "迎新晚会", "2026-10-30 19:00:00",
               "晚会地点：大学生活动中心", "2026-10-25 12:00:00",
               group_id="g2", sender_id="u2", location="大学生活动中心")

    merged, review = _split(cluster_leads([l1, l2]))

    assert len(merged) == 1
    assert review == []
    assert merged[0]["cross_group"] is True


def test_changes_sorted_and_old_value_kept():
    """合并后 changes 按时间顺序挂载，原时间不被覆盖，旧值/新值同时保留。"""
    l1 = _lead("l1", "m1", "班级团建", "2026-10-30 09:00:00",
               "周六上午9点在南门集合", "2026-10-25 10:00:00",
               location="南门")
    # 输入顺序故意打乱：恢复在前、改地点在后，验证输出按 send_time 排序
    l3 = _lead("l3", "m3", "班级团建", None,
               "活动照常进行", "2026-10-26 10:00:00",
               changes=[
                   {
                       "change_id": "c3",
                       "msg_id": "m3",
                       "type": "restore",
                       "field": None,
                       "old_value": None,
                       "new_value": None,
                       "quote": "活动照常进行",
                       "confidence": 0.9,
                   }
               ])
    l2 = _lead("l2", "m2", "班级团建", None,
               "地点改到东门了", "2026-10-26 09:00:00",
               changes=[
                   {
                       "change_id": "c2",
                       "msg_id": "m2",
                       "type": "change_location",
                       "field": "location",
                       "old_value": "南门",
                       "new_value": "东门",
                       "quote": "地点改到东门了",
                       "confidence": 0.9,
                   }
               ])

    merged, review = _split(cluster_leads([l3, l1, l2]))

    assert review == []
    assert len(merged) == 1
    cluster = merged[0]
    assert cluster["event_time"] == "2026-10-30 09:00:00"  # 原始时间保留
    assert [c["type"] for c in cluster["changes"]] == [
        "change_location",
        "restore",
    ]
    first = cluster["changes"][0]
    assert first["old_value"] == "南门"
    assert first["new_value"] == "东门"
    assert first["old_value"] != first["new_value"]
