"""答案校验（不信任大模型输出）。

三级校验：
- L1 引用存在性：每条 source 的 quote 必须是对应 msg_id 原文的逐字子串，否则丢弃该 source；
  event 的 sources 全部被丢弃时丢弃该 event。
- L2 字段可推导性：time / deadline 按该消息 send_time 换算后比较日期；
  location 做别名归一（南门口 = 南门）；file_name 去扩展名后模糊相似度 >= 0.8；
  participants 逐名在原文中出现。推导不出的字段一律置 null。
- L3 冲突检测：同一字段出现 >=2 个互斥候选值（如原文分别写南门、北门）→ 字段置 null，
  标记 reasons="conflicting_evidence"，并保留候选 quote 供前端展示。

过滤后禁止沿用模型生成的 answer 文本：一律用模板拼装"已核验字段"；
若事件全部被剔除，则降级 uncertain（不返回任何结论句）。
"""

import logging
from dataclasses import asdict
from typing import Optional

from .evidence import (
    bind_evidence,
    file_supports,
    location_candidates,
    participants_supported,
    time_candidates,
)

logger = logging.getLogger(__name__)

FIELD_LABELS = {"time": "时间", "deadline": "截止时间", "location": "地点"}

# 能定位事件的关键字段：全空则视为"无法定位"，剔除该 event
ANCHOR_FIELDS = ("time", "time_raw", "deadline", "location")


def _clean_str(value) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _clean_participants(value) -> Optional[list[str]]:
    if not isinstance(value, list):
        return None
    names = [_clean_str(v) for v in value]
    names = [n for n in names if n]
    return names or None


def _plain(text) -> str:
    return "".join(str(text or "").split())


def _raw_in_quotes(raw: str, sources: list[dict]) -> bool:
    """time_raw 必须能逐字出现在某条通过 L1 的 quote 中，否则属于编造。"""
    target = _plain(raw)
    return bool(target) and any(target in _plain(s.get("quote")) for s in sources)


def _name_in_any(name: str, sources: list[dict]) -> bool:
    return any(
        participants_supported([name], s.get("quote") or "") for s in sources
    )


def _no_result() -> dict:
    return {
        "status": "no_result",
        "answer": "没有找到能支撑回答的原始消息，无法给出确定答案。",
        "events": [],
    }


def _uncertain(reason: str, conflicts: list[dict]) -> dict:
    """有原文、但字段无法核验/互相冲突：不给出任何结论句。"""
    return {
        "status": "uncertain",
        "answer": "",
        "events": [],
        "reason": reason,
        "conflicts": conflicts,
    }


def _apply_field_checks(event: dict, corpus: dict) -> dict:
    """L2 + L3：逐字段校验，返回 conflicts；不合格字段就地置 null。"""
    conflicts: dict[str, dict] = {}
    evidences = bind_evidence(event, corpus)
    supported = {ev.field for ev in evidences}

    # 地点：可推导 + 候选唯一 → 保留；否则剔除，并在有候选时记冲突
    location = event.get("location")
    if location:
        candidates = location_candidates(event["sources"], corpus)
        distinct = {c["value"] for c in candidates}
        if not ("location" in supported and len(distinct) < 2):
            if candidates:
                conflicts["location"] = {
                    "reason": "conflicting_evidence",
                    "claimed": location,
                    "candidates": candidates,
                }
            event["location"] = None

    # 时间 / 截止时间：换算日期必须命中
    for field in ("time", "deadline"):
        value = event.get(field)
        if not value or field in supported:
            continue
        candidates = time_candidates(event["sources"], corpus)
        if candidates:
            conflicts[field] = {
                "reason": "conflicting_evidence",
                "claimed": value,
                "candidates": candidates,
            }
        event[field] = None

    # time_raw 是展示用原文表述，也必须能在原文中找到
    if event.get("time_raw") and not _raw_in_quotes(
        event["time_raw"], event["sources"]
    ):
        event["time_raw"] = None

    # 参与人：只保留能在原文中找到的名字
    participants = event.get("participants")
    if participants:
        kept = [name for name in participants if _name_in_any(name, event["sources"])]
        event["participants"] = kept or None

    # 文件名：去扩展名模糊匹配
    file_name = event.get("file_name")
    if file_name and "file_name" not in supported:
        event["file_name"] = None

    # 只保留仍然有值的字段证据
    event["field_evidence"] = [
        asdict(ev) for ev in evidences if event.get(ev.field)
    ]
    return conflicts


def _render_answer(events: list[dict]) -> str:
    """用模板拼装已核验字段，禁止沿用模型 answer 原文。"""
    lines = []
    for index, event in enumerate(events, start=1):
        segments = []
        when = event.get("time_raw") or event.get("time")
        if when:
            segments.append(f"时间：{when}")
        if event.get("deadline"):
            segments.append(f"截止：{event['deadline']}")
        if event.get("location"):
            segments.append(f"地点：{event['location']}")
        if event.get("participants"):
            segments.append("参与人：" + "、".join(event["participants"]))
        if event.get("file_name"):
            segments.append(f"文件：{event['file_name']}")
        segments.append("来源：" + "、".join(event["msg_ids"]))
        lines.append(f"{index}) " + "，".join(segments))

    text = "根据群聊原文：" + "；".join(lines) + "。"
    conflict_fields = sorted(
        {field for event in events for field in (event.get("conflicts") or {})}
    )
    if conflict_fields:
        text += "（存在不一致信息：" + "、".join(
            FIELD_LABELS.get(field, field) for field in conflict_fields
        ) + "）"
    return text


def validate_answer(result, corpus: dict) -> dict:
    """校验并规整答案。

    返回 {"status": "ok"|"no_result"|"uncertain", "answer": str, "events": [...]}。
    - no_result：连一条引用都没通过 L1（没有任何原文证据）。
    - uncertain：有原文通过 L1，但所有 event 的关键字段都无法核验或互相冲突。
    corpus: {msg_id: 原文 content}，或 {msg_id: {"content", "send_time"}}。
    """
    if not isinstance(result, dict):
        return _no_result()

    raw_events = result.get("events")
    if not isinstance(raw_events, list):
        return _no_result()

    events = []
    any_source_kept = False
    dropped_conflicts: list[dict] = []

    for raw_event in raw_events:
        if not isinstance(raw_event, dict):
            continue

        kept_sources = []
        seen = set()
        raw_sources = raw_event.get("sources")
        for source in raw_sources if isinstance(raw_sources, list) else []:
            if not isinstance(source, dict):
                continue
            msg_id = _clean_str(source.get("msg_id"))
            quote = source.get("quote")
            if not msg_id or not isinstance(quote, str) or not quote.strip():
                logger.warning("丢弃引用：msg_id 或 quote 缺失")
                continue

            entry = corpus.get(msg_id)
            if entry is None:
                logger.warning("丢弃引用：msg_id=%s 不在检索候选中", msg_id)
                continue
            content = entry.get("content") if isinstance(entry, dict) else entry
            if not isinstance(content, str) or quote not in content:
                logger.warning("丢弃引用：quote 不是 msg_id=%s 的原文字串", msg_id)
                continue

            key = (msg_id, quote)
            if key in seen:
                continue
            seen.add(key)
            kept_sources.append({"msg_id": msg_id, "quote": quote})

        if not kept_sources:
            logger.warning(
                "丢弃事件：%s 没有任何通过校验的引用",
                _clean_str(raw_event.get("summary")),
            )
            continue
        any_source_kept = True

        # summary 未经核验，一律不进入输出（模板只拼已核验字段）
        event = {
            "summary": None,
            "time": _clean_str(raw_event.get("time")),
            "time_raw": _clean_str(raw_event.get("time_raw")),
            "deadline": _clean_str(raw_event.get("deadline")),
            "location": _clean_str(raw_event.get("location")),
            "participants": _clean_participants(raw_event.get("participants")),
            "file_name": _clean_str(raw_event.get("file_name")),
            "sources": kept_sources,
            "msg_ids": [s["msg_id"] for s in kept_sources],
        }
        event["conflicts"] = _apply_field_checks(event, corpus)

        # 关键字段全空 → 无法定位该事件，剔除
        if not any(event.get(field) for field in ANCHOR_FIELDS):
            logger.warning("丢弃事件：关键字段全部无法核验（msg_ids=%s）", event["msg_ids"])
            dropped_conflicts.extend(event["conflicts"].values())
            continue

        events.append(event)

    if not events:
        if not any_source_kept:
            return _no_result()
        reason = (
            "conflicting_evidence" if dropped_conflicts else "no_verifiable_fields"
        )
        return _uncertain(reason, dropped_conflicts)

    return {"status": "ok", "answer": _render_answer(events), "events": events}
