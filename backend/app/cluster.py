"""事件关联：保守合并 + 变更挂载。

合并条件（同群，三者全部满足才自动合并）：
1) 同 group_id
2) 标题相似度 difflib.SequenceMatcher ratio > 0.6
3) 时间接近：event_time 同一天，或两者均为 null

跨群转发 / 同名活动（U4）：
- 标题 + 时间满足但 group_id 不同时，必须再有第二个独立信号
  （同一 sender_id / 同一地点 / 同一报名链接）才自动关联，并标 cross_group=true；
- 缺少第二信号时绝不合并，输出 status="pending_review" 与 candidates 供人工确认。

变更（U4）：
- changes 由抽取阶段写入 leads_changes，load_clusters 读取后挂到各 lead 上；
- 合并后按发送时间顺序汇总到 cluster["changes"]，old_value / new_value 同时保留，
  禁止覆盖原始值。
"""

import hashlib
import re
from difflib import SequenceMatcher
from typing import Optional

# 改期/取消识别模式（用于判断"时间变化但仍属同一事件"）
CORRECTION_PATTERN = re.compile(r"改期|改到|改成|推迟|延期|顺延|换到|取消|恢复|照常")

TITLE_SIM_THRESHOLD = 0.6

# 报名链接识别
_LINK_PATTERN = re.compile(r"https?://[^\s，。；;、]+")

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


def _links(content: Optional[str]) -> set[str]:
    """从正文中提取报名链接。"""
    return set(_LINK_PATTERN.findall(_text(content)))


def _pair_extra_signal(a: dict, b: dict) -> Optional[str]:
    """判断两条线索之间是否存在"第二个独立信号"，返回信号名或 None。"""
    sid_a, sid_b = _text(a.get("sender_id")), _text(b.get("sender_id"))
    if sid_a and sid_a == sid_b:
        return "same_sender"

    loc_a, loc_b = _text(a.get("location")).strip(), _text(b.get("location")).strip()
    if loc_a and loc_a == loc_b:
        return "same_location"

    if _links(a.get("content")) & _links(b.get("content")):
        return "same_link"
    return None


def _group_members(members: list[dict]) -> set[str]:
    return {_text(m.get("group_id")) for m in members}


def _has_extra_signal(members: list[dict], lead: dict) -> Optional[str]:
    """新线索与 cluster 中任一成员是否有第二信号。"""
    for member in members:
        signal = _pair_extra_signal(member, lead)
        if signal:
            return signal
    return None


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


def _candidate_summary(lead: dict) -> dict:
    """待人工确认时输出的候选摘要。"""
    return {
        "lead_id": lead.get("lead_id"),
        "msg_id": lead.get("msg_id"),
        "group_id": lead.get("group_id"),
        "sender_id": lead.get("sender_id"),
        "title": lead.get("title"),
        "event_time": lead.get("event_time"),
        "location": lead.get("location"),
    }


def _collect_changes(members: list[dict]) -> list[dict]:
    """把成员上的变更按时间顺序汇总；old_value / new_value 原样保留。"""
    rows: list[dict] = []
    seen: set[str] = set()

    for lead in sorted(members, key=_sort_key):
        for change in lead.get("changes") or []:
            if not isinstance(change, dict):
                continue
            row = dict(change)
            row.setdefault("msg_id", lead.get("msg_id"))
            row.setdefault("send_time", lead.get("send_time"))
            dedupe = _text(row.get("change_id")) or (
                _text(row.get("msg_id"))
                + "|"
                + _text(row.get("type"))
                + "|"
                + _text(row.get("quote"))
            )
            if dedupe in seen:
                continue
            seen.add(dedupe)
            rows.append(row)

    rows.sort(
        key=lambda c: (
            _text(c.get("send_time")),
            _text(c.get("msg_id")),
            _text(c.get("change_id")),
        )
    )
    return rows


def _make_cluster(
    members: list[dict],
    need_review: bool,
    reason: Optional[str] = None,
    candidates: Optional[list[dict]] = None,
) -> dict:
    """组装 cluster 结构。event_time 取最早成员的时间作为原始时间。"""
    ordered = sorted(members, key=_sort_key)
    original = ordered[0]
    groups = [g for g in sorted(_group_members(ordered)) if g]
    cluster = {
        "cluster_id": _cluster_id(members),
        "group_id": original.get("group_id"),
        "groups": groups,
        "cross_group": len(groups) > 1,
        "category": original.get("category"),
        "title": original.get("title"),
        "event_time": original.get("event_time"),
        "time_raw": original.get("time_raw"),
        "members": ordered,
        "need_review": need_review,
        "status": "pending_review" if need_review else "merged",
        "changes": [] if need_review else _collect_changes(ordered),
    }
    if reason:
        cluster["review_reason"] = reason
    if candidates:
        cluster["candidates"] = candidates
    return cluster


def cluster_leads(leads: list[dict]) -> list[dict]:
    """把线索聚成事件 cluster，返回 cluster 列表。

    - status="merged"：条件全部满足（跨群时还需第二信号），含 changes。
    - status="pending_review"：不满足自动合并条件，输出 candidates 供人工确认。
    """
    if not leads:
        return []

    merged_clusters: list[dict] = []
    review_entries: list[dict] = []
    review_seen: set[str] = set()

    clusters: list[dict] = []  # 内部维护 {"members": [...]}

    def _add_review(rep: dict, lead: dict, reason: str) -> None:
        key = _cluster_id([rep, lead])
        if key in review_seen:
            return
        review_seen.add(key)
        review_entries.append(
            _make_cluster(
                [rep, lead],
                need_review=True,
                reason=reason,
                candidates=[_candidate_summary(rep), _candidate_summary(lead)],
            )
        )

    for lead in sorted(leads, key=_sort_key):
        target = None

        for cl in clusters:
            rep = cl["members"][0]
            title_ok = _title_similar(rep.get("title"), lead.get("title"))
            time_ok = _same_day(rep.get("event_time"), lead.get("event_time"))
            same_group = _group_members(cl["members"]) == {_text(lead.get("group_id"))}

            # 同群改期/取消消息的时间必然变化，允许跨日期归属原事件
            if same_group and not time_ok and title_ok and _is_correction_message(lead):
                time_ok = True

            if title_ok and time_ok:
                if same_group:
                    target = cl
                    break
                # 跨群：必须有第二独立信号，否则只输出待确认候选
                signal = _has_extra_signal(cl["members"], lead)
                if signal:
                    cl["signal"] = signal
                    target = cl
                    break
                _add_review(
                    rep,
                    lead,
                    "跨群同名活动缺少第二信号（同一 sender_id / 同一地点 / 同一报名链接），未合并",
                )
                continue

            # 同群但标题/时间只满足其一 → 待确认
            if same_group and title_ok != time_ok:
                _add_review(rep, lead, "标题相似或时间接近只满足其一，未合并")

        if target is not None:
            target["members"].append(lead)
        else:
            clusters.append({"members": [lead]})

    for cl in clusters:
        merged_clusters.append(_make_cluster(cl["members"], need_review=False))

    # 输出顺序固定：先已合并（按 cluster_id），后待确认（按 cluster_id）
    merged_clusters.sort(key=lambda c: c["cluster_id"])
    review_entries.sort(key=lambda c: c["cluster_id"])
    return merged_clusters + review_entries
