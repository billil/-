"""导入时批量线索抽取。

按 EXTRACT_BATCH_SIZE（默认 20）分批调用大模型，抽取 event/task/file 三类线索，
以及改期 / 改地点 / 取消 / 恢复等变更信息。校验 evidence 是否为对应消息原文的
逐字子串，不通过则丢弃并记 warning，最终写入 leads / leads_changes 表。

三条铁律落地：
- 不编造：evidence 必须是原文子串；event_time 不合规一律置 null，
  无法按 send_time 换算时标记 time_uncertain。
- 可复现：lead_id / change_id 由内容哈希生成，重复抽取结果一致。
- 可解释：每条 lead / change 都挂在 msg_id 上，可回溯到原文。

U4：每条消息输入带 send_time 与前后各最多 2 条上下文，
提示词要求以该消息 send_time 为基准换算相对时间。
"""

import hashlib
import json
import logging
import re
from typing import Any, Optional

from .config import EXTRACT_BATCH_SIZE
from .db import get_conn, init_db
from .guard import sanitize
from .llm import chat_json
from .prompts.extract import EXTRACT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {"event", "task", "file"}

# 变更类型枚举（U4）
VALID_CHANGE_TYPES = {
    "reschedule_time",
    "change_location",
    "cancel",
    "restore",
    "other",
}

# 允许 "YYYY-MM-DD HH:MM"，也容忍模型多给秒
_EVENT_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?$")

# 上下文条数：前后各最多 2 条
CONTEXT_SIZE = 2


def _clean_str(value: Any) -> Optional[str]:
    """把字段规整为去空白的字符串；非法或空值返回 None。"""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _clean_participants(value: Any) -> Optional[str]:
    """参与人列表序列化为 JSON 文本；空则返回 None。"""
    if not isinstance(value, list):
        return None
    names = [_clean_str(v) for v in value]
    names = [n for n in names if n]
    if not names:
        return None
    return json.dumps(names, ensure_ascii=False)


def _clean_confidence(value: Any) -> Optional[float]:
    """置信度裁剪到 [0, 1]。"""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, num))


def _clean_event_time(value: Any) -> Optional[str]:
    """只接受 YYYY-MM-DD HH:MM 形式，其他一律视为不可用返回 None。"""
    text = _clean_str(value)
    if text and _EVENT_TIME_RE.match(text):
        return text
    return None


def _clean_uncertain(value: Any, event_time: Optional[str], time_raw: Optional[str]) -> bool:
    """time_uncertain：显式 true 或"有原文时间但没换算出来"都算不确定。"""
    if isinstance(value, bool):
        uncertain = value
    else:
        uncertain = False
    if event_time is None and time_raw:
        # 原文有相对时间却给不出绝对日期，必须标记不确定，禁止静默当"无时间"
        return True
    return uncertain


def _make_lead_id(msg_id: str, category: str, title: Optional[str], evidence: str) -> str:
    """确定性 lead_id：同一消息同一线索重复抽取得到相同 id。"""
    raw = f"{msg_id}|{category}|{title or ''}|{evidence}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _make_change_id(
    msg_id: str,
    ctype: str,
    field: Optional[str],
    old_value: Optional[str],
    new_value: Optional[str],
    quote: str,
) -> str:
    """确定性 change_id：同一变更重复抽取得到相同 id。"""
    raw = f"{msg_id}|{ctype}|{field or ''}|{old_value or ''}|{new_value or ''}|{quote}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _context_item(msg: dict) -> dict:
    """上下文消息的精简视图（供大模型理解，不参与 evidence 校验）。"""
    return {
        "msg_id": msg.get("msg_id"),
        "send_time": msg.get("send_time"),
        "sender": msg.get("sender"),
        "content": msg.get("content") or "",
    }


def _prepare_items(msgs: list[dict]) -> list[dict]:
    """为每条消息补充前后各最多 2 条上下文；按发送时间排序保证上下文正确。"""
    ordered = sorted(
        [m for m in msgs if m.get("msg_id")],
        key=lambda m: (str(m.get("send_time") or ""), str(m.get("msg_id"))),
    )
    items: list[dict] = []
    for idx, msg in enumerate(ordered):
        before = ordered[max(0, idx - CONTEXT_SIZE) : idx]
        after = ordered[idx + 1 : idx + 1 + CONTEXT_SIZE]
        items.append(
            {
                "msg_id": msg.get("msg_id"),
                "send_time": msg.get("send_time"),
                "sender": msg.get("sender"),
                "content": msg.get("content") or "",
                "context_before": [_context_item(m) for m in before],
                "context_after": [_context_item(m) for m in after],
            }
        )
    return items


def _resolve_msg_id(
    lead: dict,
    batch_by_id: dict[str, str],
    batch_items: list[dict],
) -> Optional[str]:
    """确定 lead 对应的 msg_id。

    优先使用模型回填的 msg_id（必须存在于本批消息中）；
    缺失或非法时，尝试用 evidence 在本批消息中做唯一子串匹配，唯一才采纳。
    """
    candidate = _clean_str(lead.get("msg_id"))
    if candidate and candidate in batch_by_id:
        return candidate

    evidence = _clean_str(lead.get("evidence"))
    if not evidence:
        return None
    matched = [m["msg_id"] for m in batch_items if evidence in (m.get("content") or "")]
    return matched[0] if len(matched) == 1 else None


def _validate_lead(
    lead: dict,
    batch_by_id: dict[str, str],
    batch_items: list[dict],
    send_time_by_id: dict[str, Optional[str]],
) -> Optional[dict]:
    """校验并规整单条 lead；不合法返回 None（调用方负责记日志丢弃）。"""
    if not isinstance(lead, dict):
        return None

    category = _clean_str(lead.get("category"))
    if category not in VALID_CATEGORIES:
        return None

    evidence = _clean_str(lead.get("evidence"))
    if not evidence:
        return None

    msg_id = _resolve_msg_id(lead, batch_by_id, batch_items)
    if not msg_id:
        return None

    # 核心校验：evidence 必须是该消息原文的逐字子串
    if evidence not in (batch_by_id.get(msg_id) or ""):
        return None

    title = _clean_str(lead.get("title"))
    event_time = _clean_event_time(lead.get("event_time"))
    time_raw = _clean_str(lead.get("time_raw"))
    base_time = _clean_event_time(lead.get("base_time"))
    if base_time is None and event_time is not None:
        # 模型没回填基准时，用该消息 send_time 兜底（提示词要求同一基准）
        base_time = send_time_by_id.get(msg_id)
    return {
        "lead_id": _make_lead_id(msg_id, category, title, evidence),
        "msg_id": msg_id,
        "category": category,
        "title": title,
        "event_time": event_time,
        "time_raw": time_raw,
        "base_time": base_time,
        "time_uncertain": int(
            _clean_uncertain(lead.get("time_uncertain"), event_time, time_raw)
        ),
        "location": _clean_str(lead.get("location")),
        "participants": _clean_participants(lead.get("participants")),
        "file_hint": _clean_str(lead.get("file_hint")),
        "confidence": _clean_confidence(lead.get("confidence")),
        "evidence": evidence,
    }


def _validate_change(
    raw: dict,
    batch_by_id: dict[str, str],
) -> Optional[dict]:
    """校验并规整单条变更；不合法返回 None。"""
    if not isinstance(raw, dict):
        return None

    ctype = _clean_str(raw.get("type"))
    if ctype not in VALID_CHANGE_TYPES:
        return None

    evidence = raw.get("evidence")
    if not isinstance(evidence, dict):
        return None
    quote = _clean_str(evidence.get("quote"))
    msg_id = _clean_str(evidence.get("msg_id")) or _clean_str(raw.get("msg_id"))
    if not quote or not msg_id or msg_id not in batch_by_id:
        return None
    # 核心校验：quote 必须是该消息原文的逐字子串
    if quote not in (batch_by_id.get(msg_id) or ""):
        return None

    field = _clean_str(raw.get("field"))
    old_value = _clean_str(raw.get("old_value"))
    new_value = _clean_str(raw.get("new_value"))
    return {
        "change_id": _make_change_id(msg_id, ctype, field, old_value, new_value, quote),
        "msg_id": msg_id,
        "type": ctype,
        "field": field,
        "old_value": old_value,
        "new_value": new_value,
        "quote": quote,
        "confidence": _clean_confidence(raw.get("confidence")),
    }


def _extract_batch(batch_items: list[dict]) -> tuple[list[dict], list[dict], int]:
    """对一批消息调用大模型，返回 (通过校验的 lead, 通过校验的 change, 丢弃条数)。"""
    if not batch_items:
        return [], [], 0

    batch_by_id = {m["msg_id"]: m["content"] for m in batch_items}
    send_time_by_id = {m["msg_id"]: m.get("send_time") for m in batch_items}

    # 送入大模型前统一做注入防护；evidence 校验仍使用未清洗的原文（batch_by_id）
    prompt_items = [
        {
            "msg_id": m["msg_id"],
            "send_time": m.get("send_time"),
            "sender": m.get("sender"),
            "content": sanitize(m["content"]),
            "context_before": [
                {**item, "content": sanitize(item.get("content") or "")}
                for item in m.get("context_before") or []
            ],
            "context_after": [
                {**item, "content": sanitize(item.get("content") or "")}
                for item in m.get("context_after") or []
            ],
        }
        for m in batch_items
    ]

    messages = [
        {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps({"messages": prompt_items}, ensure_ascii=False),
        },
    ]
    result = chat_json(messages)

    raw_leads = result.get("leads") if isinstance(result, dict) else None
    raw_changes = result.get("changes") if isinstance(result, dict) else None
    if not isinstance(raw_leads, list):
        logger.warning("大模型返回结构不含 leads 数组，本批按 0 条处理")
        raw_leads = []
    if not isinstance(raw_changes, list):
        raw_changes = []

    validated: list[dict] = []
    dropped = 0
    for raw in raw_leads:
        lead = _validate_lead(raw, batch_by_id, batch_items, send_time_by_id)
        if lead is None:
            dropped += 1
            logger.warning(
                "丢弃 1 条线索：category/msg_id 非法或 evidence 非原文子串；原始内容=%s",
                json.dumps(raw, ensure_ascii=False)[:200],
            )
            continue
        validated.append(lead)

    changes: list[dict] = []
    for raw in raw_changes:
        change = _validate_change(raw, batch_by_id)
        if change is None:
            logger.warning(
                "丢弃 1 条变更：type/msg_id 非法或 quote 非原文子串；原始内容=%s",
                json.dumps(raw, ensure_ascii=False)[:200],
            )
            continue
        changes.append(change)

    return validated, changes, dropped


def extract_leads(
    msgs: list[dict],
    db_path: Optional[str] = None,
    batch_size: int = EXTRACT_BATCH_SIZE,
) -> dict:
    """批量抽取线索与变更并写库，返回 {leads, changes, dropped, batches}。

    msgs: [{msg_id, send_time, sender, content}, ...]，空列表直接返回空结果且不调用大模型。
    """
    if not msgs:
        return {"leads": 0, "changes": 0, "dropped": 0, "batches": 0}

    init_db(db_path).close()  # 确保 leads / leads_changes 表存在（幂等）
    conn = get_conn(db_path)
    cur = conn.cursor()

    saved = 0
    saved_changes = 0
    dropped = 0
    batches = 0

    items = _prepare_items(msgs)

    try:
        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            batches += 1
            leads, changes, batch_dropped = _extract_batch(batch)
            dropped += batch_dropped

            for lead in leads:
                cur.execute(
                    """
                    INSERT OR REPLACE INTO leads
                        (lead_id, msg_id, category, title, event_time, time_raw,
                         base_time, time_uncertain, location, participants,
                         file_hint, confidence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """,
                    (
                        lead["lead_id"],
                        lead["msg_id"],
                        lead["category"],
                        lead["title"],
                        lead["event_time"],
                        lead["time_raw"],
                        lead["base_time"],
                        lead["time_uncertain"],
                        lead["location"],
                        lead["participants"],
                        lead["file_hint"],
                        lead["confidence"],
                    ),
                )
                saved += 1

            # 变更挂到同消息的 lead 上（同消息可能有多条 lead，取第一条即可）
            lead_id_by_msg = {lead["msg_id"]: lead["lead_id"] for lead in leads}
            for change in changes:
                cur.execute(
                    """
                    INSERT OR REPLACE INTO leads_changes
                        (change_id, lead_id, msg_id, type, field, old_value,
                         new_value, quote, confidence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """,
                    (
                        change["change_id"],
                        lead_id_by_msg.get(change["msg_id"]),
                        change["msg_id"],
                        change["type"],
                        change["field"],
                        change["old_value"],
                        change["new_value"],
                        change["quote"],
                        change["confidence"],
                    ),
                )
                saved_changes += 1

        conn.commit()
    finally:
        conn.close()

    return {
        "leads": saved,
        "changes": saved_changes,
        "dropped": dropped,
        "batches": batches,
    }
