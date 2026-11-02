"""答案校验（不信任大模型输出）。

五级校验：
- L1 引用真实性：每条 source 的 quote 必须是对应 msg_id 原文的逐字子串，否则丢弃该 source；
  event 的 sources 全部被丢弃时丢弃该 event。
- L2 字段可推导性：time / deadline 按该消息 send_time 换算后比较日期；
  location 做别名归一（南门口 = 南门）；file_name 去扩展名后模糊相似度 >= 0.8；
  participants 逐名在原文中出现。推导不出的字段一律置 null，并记 conflicted_evidence。
- L3 引用归属：msg_id 必须落在本次授权集合内（group_scope / allowed_msg_ids，
  未提供时退化为"本次 corpus 候选集合"）；跨群引用直接判失败。
- L4 版本有效：msg_status=revoked 的 source 禁止引用，其所支撑字段置 null；
  edited 允许引用，但最终 answer 必须标注"（该消息已编辑）"，并要求 corpus 使用最新版本。
- L5 全文一致：先剔除 event → 用剩下的 event 重新渲染 answer → 再校验渲染文本
  与 fields / quote 是否一致；不一致则字段置 null、整体降为 uncertain。

过滤后禁止沿用模型生成的 answer 文本：一律用模板拼装"已核验字段"；
若事件全部被剔除，则降级 uncertain（不返回任何结论句）。
validate_answer 返回的 evidence 统一由 events[].sources 派生，供 facade.process 直接透出。
"""

import logging
from dataclasses import asdict
from typing import Optional

from .evidence import (
    bind_evidence,
    location_candidates,
    normalize_file_stem,
    normalize_location,
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


def _no_result(conflicts: Optional[list[dict]] = None) -> dict:
    return {
        "status": "no_result",
        "answer": "没有找到能支撑回答的原始消息，无法给出确定答案。",
        "events": [],
        "evidence": [],
        "conflicts": list(conflicts or []),
    }


def _uncertain(reason: str, conflicts: list[dict]) -> dict:
    """有原文、但字段无法核验/互相冲突/越权：不给出任何结论句。"""
    return {
        "status": "uncertain",
        "answer": "",
        "events": [],
        "evidence": [],
        "reason": reason,
        "conflicts": conflicts,
    }


def _source_conflict(check: str, reason: str, msg_id: str, quote: str) -> dict:
    """记录一条被丢弃引用的原因，保证校验失败不会被静默吞掉。"""
    return {
        "check": check,
        "reason": reason,
        "msg_id": msg_id,
        "quote": quote,
    }


def _authorized(msg_id: str, entry, group_scope: Optional[list[str]],
                allowed_msg_ids: Optional[list[str]]) -> bool:
    """L3：判断引用是否落在本次授权范围内。

    两个权限参数都未提供时，退化为"corpus（本次候选集合）内的 msg_id 即视为授权"，
    以兼容只做纯字段校验的调用方；提供了任何一个就按白名单/群范围严格判断。
    """
    if allowed_msg_ids is not None and msg_id not in set(allowed_msg_ids):
        return False
    if group_scope is not None:
        group_id = entry.get("group_id") if isinstance(entry, dict) else None
        if group_id not in set(group_scope):
            return False
    return True


def _entry_version(entry) -> int:
    if isinstance(entry, dict):
        try:
            return int(entry.get("version") or 1)
        except (TypeError, ValueError):
            return 1
    return 1


def _build_evidence(events: list[dict], corpus: dict) -> list[dict]:
    """从通过校验的 events[].sources / field_evidence 派生统一引用结构。

    field_evidence 按字段各出一条，保证每个字段都能指回同一段 quote；
    未被字段绑定的 source 也保留一条 field=None 的引用，便于前端展示溯源。
    """
    evidence: list[dict] = []
    seen = set()
    covered: set[tuple[str, str]] = set()

    def add(msg_id, quote, field):
        key = (msg_id, quote, field)
        if key in seen:
            return
        seen.add(key)
        entry = corpus.get(msg_id)
        evidence.append(
            {
                "msg_id": msg_id,
                "quote": quote,
                "field": field,
                "version": _entry_version(entry),
                "edited": bool(
                    isinstance(entry, dict)
                    and entry.get("msg_status") == "edited"
                ),
            }
        )

    for event in events:
        for item in event.get("field_evidence") or []:
            add(item["msg_id"], item["quote"], item["field"])
            covered.add((item["msg_id"], item["quote"]))
        for source in event.get("sources") or []:
            msg_id = source.get("msg_id")
            quote = source.get("quote")
            if (msg_id, quote) in covered:
                continue
            add(msg_id, quote, None)
    return evidence


def _l5_check(answer_text: str, events: list[dict]) -> list[dict]:
    """L5：渲染后的 answer 文本必须与 fields 一致。

    渲染只使用已核验字段，因此正常情况下不会触发；一旦触发说明渲染/校验链路
    出现矛盾，按规格把对应字段置 null 并让整体降级 uncertain。
    """
    conflicts: list[dict] = []
    text = _plain(answer_text)
    for event in events:
        when = event.get("time_raw") or event.get("time")
        if when and _plain(when) not in text:
            conflicts.append(
                {
                    "check": "L5",
                    "reason": "answer_time_conflict",
                    "field": "time",
                    "claimed": when,
                    "evidence": event.get("sources") or [],
                }
            )
            event["time"] = None
            event["time_raw"] = None
        if event.get("deadline") and _plain(event["deadline"]) not in text:
            conflicts.append(
                {
                    "check": "L5",
                    "reason": "answer_deadline_conflict",
                    "field": "deadline",
                    "claimed": event["deadline"],
                    "evidence": event.get("sources") or [],
                }
            )
            event["deadline"] = None
        location = event.get("location")
        if location and normalize_location(location) not in normalize_location(answer_text):
            conflicts.append(
                {
                    "check": "L5",
                    "reason": "answer_location_conflict",
                    "field": "location",
                    "claimed": location,
                    "evidence": event.get("sources") or [],
                }
            )
            event["location"] = None
    return conflicts


def _recheck_evidence(evidence: list[dict], corpus: dict) -> tuple[list[dict], list[dict]]:
    """渲染后重跑 L1：确认最终 evidence 仍是授权候选原文的逐字子串。"""
    kept: list[dict] = []
    conflicts: list[dict] = []
    for item in evidence:
        entry = corpus.get(item.get("msg_id"))
        content = entry.get("content") if isinstance(entry, dict) else entry
        if not isinstance(content, str) or item.get("quote") not in content:
            conflicts.append(
                _source_conflict(
                    "L1", "quote_not_in_source", item.get("msg_id"), item.get("quote")
                )
            )
            continue
        kept.append(item)
    return kept, conflicts


def _apply_field_checks(event: dict, corpus: dict) -> dict:
    """L2：逐字段校验可推导性/一致性，返回 conflicts；不合格字段就地置 null。"""
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
                    "check": "L2",
                    "field": "location",
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
                "check": "L2",
                "field": field,
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

    # 文件名：去扩展名后必须逐字出现在某条 quote 中；不采用"相似度>0.8 就算过"
    file_name = event.get("file_name")
    if file_name:
        stem = normalize_file_stem(file_name)
        exact = bool(stem) and any(
            stem in _plain(source.get("quote"))
            for source in event["sources"]
        )
        if "file_name" not in supported or not exact:
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
        edited = set(event.get("edited_msg_ids") or [])
        source_text = "、".join(
            msg_id + ("（该消息已编辑）" if msg_id in edited else "")
            for msg_id in event["msg_ids"]
        )
        segments.append("来源：" + source_text)
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


def validate_answer(
    result,
    corpus: dict,
    group_scope: Optional[list[str]] = None,
    allowed_msg_ids: Optional[list[str]] = None,
) -> dict:
    """校验并规整答案，返回 status / answer / events / evidence / conflicts。

    Args:
        result: 大模型输出的 {answer, events}；非 dict 或 events 非 list 时返回 no_result。
        corpus: {msg_id: 原文 content} 或
            {msg_id: {"content", "send_time", "group_id", "msg_status", "version"}}。
        group_scope: 本次授权群列表（L3）；不传则退化为"corpus 内候选均视为授权"。
        allowed_msg_ids: 本次授权消息白名单（L3）；与 group_scope 至少一个即可收紧范围。

    Returns:
        {"status": "ok"|"no_result"|"uncertain", "answer": str, "events": [...],
         "evidence": [...], "conflicts": [...], "reason"?: str}
        - ok：至少一个 event 通过 L1-L5，answer 由已核验字段重新渲染。
        - no_result：没有任何一条引用通过校验（没有任何原文证据）。
        - uncertain：有原文/引用，但字段不可推导、互相冲突、越权或版本失效，
          不返回任何结论句；conflicts 记录每条被拒引用的 check/reason。
    """
    if not isinstance(result, dict):
        return _no_result()

    raw_events = result.get("events")
    if not isinstance(raw_events, list):
        return _no_result()

    events = []
    any_source_kept = False
    dropped_conflicts: list[dict] = []
    source_conflicts: list[dict] = []
    unauthorized_conflicts: list[dict] = []
    revoked_conflicts: list[dict] = []

    for raw_event in raw_events:
        if not isinstance(raw_event, dict):
            continue

        kept_sources = []
        edited_msg_ids: list[str] = []
        seen = set()
        raw_sources = raw_event.get("sources")
        for source in raw_sources if isinstance(raw_sources, list) else []:
            if not isinstance(source, dict):
                continue
            msg_id = _clean_str(source.get("msg_id"))
            quote = source.get("quote")
            if not msg_id or not isinstance(quote, str) or not quote.strip():
                logger.warning("丢弃引用：msg_id 或 quote 缺失")
                source_conflicts.append(
                    _source_conflict(
                        "L1", "missing_msg_id_or_quote", msg_id or "", str(quote or "")
                    )
                )
                continue

            entry = corpus.get(msg_id)
            if entry is None:
                logger.warning("丢弃引用：msg_id=%s 不在检索候选中", msg_id)
                source_conflicts.append(
                    _source_conflict("L1", "msg_id_not_in_candidates", msg_id, quote)
                )
                continue
            content = entry.get("content") if isinstance(entry, dict) else entry
            if not isinstance(content, str) or quote not in content:
                logger.warning("丢弃引用：quote 不是 msg_id=%s 的原文字串", msg_id)
                source_conflicts.append(
                    _source_conflict("L1", "quote_not_in_source", msg_id, quote)
                )
                continue

            # L3 归属：跨群/白名单外引用直接判失败
            if not _authorized(msg_id, entry, group_scope, allowed_msg_ids):
                logger.warning("丢弃引用：msg_id=%s 不在授权范围内", msg_id)
                conflict = _source_conflict(
                    "L3", "reference_not_authorized", msg_id, quote
                )
                unauthorized_conflicts.append(conflict)
                source_conflicts.append(conflict)
                continue

            # L4 版本：revoked 消息禁止引用
            status = entry.get("msg_status") if isinstance(entry, dict) else None
            if status == "revoked":
                logger.warning("丢弃引用：msg_id=%s 已被撤回", msg_id)
                conflict = _source_conflict("L4", "revoked_message", msg_id, quote)
                revoked_conflicts.append(conflict)
                source_conflicts.append(conflict)
                continue

            key = (msg_id, quote)
            if key in seen:
                continue
            seen.add(key)
            kept_sources.append({"msg_id": msg_id, "quote": quote})
            if status == "edited" and msg_id not in edited_msg_ids:
                edited_msg_ids.append(msg_id)

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
            "edited_msg_ids": edited_msg_ids,
        }
        event["conflicts"] = _apply_field_checks(event, corpus)

        # 关键字段全空 → 无法定位该事件，剔除
        if not any(event.get(field) for field in ANCHOR_FIELDS):
            logger.warning("丢弃事件：关键字段全部无法核验（msg_ids=%s）", event["msg_ids"])
            dropped_conflicts.extend(event["conflicts"].values())
            continue

        events.append(event)

    all_conflicts = source_conflicts + dropped_conflicts

    if not events:
        if unauthorized_conflicts:
            return _uncertain("reference_not_authorized", all_conflicts)
        if revoked_conflicts:
            return _uncertain("revoked_reference", all_conflicts)
        if not any_source_kept:
            return _no_result(all_conflicts)
        reason = (
            "conflicting_evidence" if dropped_conflicts else "no_verifiable_fields"
        )
        return _uncertain(reason, all_conflicts)

    # T3 关键修复：先过滤 event → 用剩下的 event 重新渲染 → 再跑 L1/L5
    answer_text = _render_answer(events)
    l5_conflicts = _l5_check(answer_text, events)
    if l5_conflicts:
        return _uncertain("answer_conflict", all_conflicts + l5_conflicts)

    evidence = _build_evidence(events, corpus)
    evidence, recheck_conflicts = _recheck_evidence(evidence, corpus)
    if recheck_conflicts:
        return _uncertain("quote_not_in_source", all_conflicts + recheck_conflicts)

    return {
        "status": "ok",
        "answer": answer_text,
        "events": events,
        "evidence": evidence,
        "conflicts": all_conflicts,
    }
