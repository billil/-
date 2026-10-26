"""事件关联：保守合并 + 改期/取消标记。

合并条件（三者全部满足才合并）：
1) 同 group_id
2) 标题相似度 difflib.SequenceMatcher ratio > 0.6
3) 时间接近：event_time 同一天，或两者均为 null

只满足其中两条时绝不强行合并，输出 need_review=True 的候选条目供人工确认。

改期/取消：同一 cluster 内，后发消息命中"改期/改到/推迟/取消/换到"等模式时，
为该 cluster 挂 correction 标记；原 event_time 必须保留，禁止覆盖。

输入 lead 字段要求（来自 leads JOIN messages）：
lead_id, msg_id, group_id, category, title, event_time, time_raw, location,
participants, file_hint, confidence, send_time, content
"""

import hashlib
import re
from difflib import SequenceMatcher
from typing import Optional

# 改期/取消识别模式（"等"含义：可继续补充同义表达）
CORRECTION_PATTERN = re.compile(r"改期|改到|改成|推迟|延期|顺延|换到|取消")

TITLE_SIM_THRESHOLD = 0.6

_MISSING = object()


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _title_similar(a: Optional[str], b: Optional[str]) -> bool:
    """标题相似度是否超过阈值；任一侧缺失视为无法比较（保守返回 False）。"""
    t1, t2 = _text(a).strip(), _text(b).strip()
    if not t1 or not t2:
        return False
    return SequenceMatcher(None, t1, t2).ratio() > TITLE_SIM_THRESHOLD


def _same_day(a: Optional[str], b: Optional[str]) -> bool:
    """event_time 是否同一天；两者都为 null 视为时间接近。"""
    if not a and not b:
        return True
    if not a or not b:
        return False  # 一个有一个没有，无法确认，保守视为不接近
    return str(a)[:10] == str(b)[:10]


def _is_correction_message(lead: dict) -> bool:
    """消息正文是否包含改期/取消表述。"""
    return bool(CORRECTION_PATTERN.search(_text(lead.get("content"))))


def _sort_key(lead: dict):
    """确定性排序：先按发送时间，再按 msg_id/lead_id。"""
    return (
        _text(lead.get("send_time")),
        _text(lead.get("msg_id")) or _text(lead.get("lead_id")),
    )


def _lead_key(lead: dict) -> str:
    return _text(lead.get("lead_id")) or (
        _text(lead.get("msg_id")) + ":" + _text(lead.get("title"))
    )


def _cluster_id(members: list[dict]) -> str:
    """由成员 key 排序后哈希，保证同输入得到同 cluster_id（可复现）。"""
    raw = "|".join(sorted(_lead_key(m) for m in members))
    return "cl_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _extract_corrections(members: list[dict]) -> list[dict]:
    """从后发消息中提取改期/取消标记；原时间保留，不覆盖。"""
    corrections: list[dict] = []
    seen: set[tuple[str, str]] = set()

    ordered = sorted(members, key=_sort_key)
    for lead in ordered[1:]:  # 第一条视为原始通知
        content = _text(lead.get("content"))
        match = CORRECTION_PATTERN.search(content)
        if not match:
            continue

        corr_type = "cancel" if "取消" in content else "reschedule"
        # new_time 只取该消息自身 lead 的 event_time，取不到就为 null，禁止推测
        new_time = lead.get("event_time") or None
        from_msg_id = _text(lead.get("msg_id")) or None

        dedupe_key = (corr_type, from_msg_id or "")
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        corrections.append(
            {"type": corr_type, "new_time": new_time, "from_msg_id": from_msg_id}
        )

    return corrections


def _make_cluster(members: list[dict], need_review: bool, reason: Optional[str] = None) -> dict:
    """组装 cluster 结构。event_time 取最早成员的时间作为原始时间。"""
    ordered = sorted(members, key=_sort_key)
    original = ordered[0]
    cluster = {
        "cluster_id": _cluster_id(members),
        "group_id": original.get("group_id"),
        "category": original.get("category"),
        "title": original.get("title"),
        "event_time": original.get("event_time"),
        "time_raw": original.get("time_raw"),
        "members": ordered,
        "need_review": need_review,
        "corrections": [] if need_review else _extract_corrections(ordered),
    }
    if reason:
        cluster["review_reason"] = reason
    return cluster


def cluster_leads(leads: list[dict]) -> list[dict]:
    """把线索聚成事件 cluster，返回 cluster 列表。

    - need_review=False：条件全部满足、已合并的 cluster（含 corrections）。
    - need_review=True：仅满足 2/3 条件的候选，只做标记、不合并。
    """
    if not leads:
        return []

    # 按 group_id 分组，组内保持确定性顺序
    by_group: dict[str, list[dict]] = {}
    for lead in leads:
        by_group.setdefault(_text(lead.get("group_id")), []).append(lead)

    merged_clusters: list[dict] = []
    review_entries: list[dict] = []

    for group_id in sorted(by_group):
        members = sorted(by_group[group_id], key=_sort_key)
        clusters: list[dict] = []  # 内部维护 {"rep": lead, "members": [...]}

        for lead in members:
            target = None
            for cl in clusters:
                rep = cl["rep"]
                title_ok = _title_similar(rep.get("title"), lead.get("title"))
                time_ok = _same_day(rep.get("event_time"), lead.get("event_time"))
                # 例外：改期/取消消息的时间必然变化，允许跨日期关联，
                # 否则"改到周日上午"这类消息永远无法归属原事件。
                if not time_ok and title_ok and _is_correction_message(lead):
                    time_ok = True
                if title_ok and time_ok:
                    target = cl
                    break
                if title_ok != time_ok:
                    # 恰好只满足两条（同 group + 其中一条）→ 不合并，标记待确认
                    review_entries.append(
                        _make_cluster(
                            [rep, lead],
                            need_review=True,
                            reason="标题相似或时间接近只满足其一，未合并",
                        )
                    )

            if target is not None:
                target["members"].append(lead)
            else:
                clusters.append({"rep": lead, "members": [lead]})

        for cl in clusters:
            merged_clusters.append(_make_cluster(cl["members"], need_review=False))

    # 输出顺序固定：先已合并（按 cluster_id），后待确认（按 cluster_id）
    merged_clusters.sort(key=lambda c: c["cluster_id"])
    review_entries.sort(key=lambda c: c["cluster_id"])
    return merged_clusters + review_entries
