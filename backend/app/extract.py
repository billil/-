"""导入时批量线索抽取。

按 EXTRACT_BATCH_SIZE（默认 20）分批调用大模型，抽取 event/task/file 三类线索，
校验 evidence 是否为对应消息原文的逐字子串，不通过则丢弃该条 lead 并记 warning，
最终写入 leads 表。

三条铁律落地：
- 不编造：evidence 必须是原文子串；event_time 不合规一律置 null。
- 可复现：lead_id 由 msg_id/category/title/evidence 哈希生成，重复抽取结果一致。
- 可解释：每条 lead 都挂在 msg_id 上，可回溯到原文。
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

# 允许 "YYYY-MM-DD HH:MM"，也容忍模型多给秒
_EVENT_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?$")


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


def _make_lead_id(msg_id: str, category: str, title: Optional[str], evidence: str) -> str:
    """确定性 lead_id：同一消息同一线索重复抽取得到相同 id。"""
    raw = f"{msg_id}|{category}|{title or ''}|{evidence}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


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
    return {
        "lead_id": _make_lead_id(msg_id, category, title, evidence),
        "msg_id": msg_id,
        "category": category,
        "title": title,
        "event_time": _clean_event_time(lead.get("event_time")),
        "time_raw": _clean_str(lead.get("time_raw")),
        "location": _clean_str(lead.get("location")),
        "participants": _clean_participants(lead.get("participants")),
        "file_hint": _clean_str(lead.get("file_hint")),
        "confidence": _clean_confidence(lead.get("confidence")),
        "evidence": evidence,
    }


def _extract_batch(batch: list[dict]) -> tuple[list[dict], int]:
    """对一批消息调用大模型，返回 (通过校验的 lead 列表, 被丢弃条数)。"""
    batch_items = [
        {
            "msg_id": m.get("msg_id"),
            "sender": m.get("sender"),
            "content": m.get("content") or "",
        }
        for m in batch
        if m.get("msg_id")
    ]
    if not batch_items:
        return [], 0

    batch_by_id = {m["msg_id"]: m["content"] for m in batch_items}

    # 送入大模型前统一做注入防护；evidence 校验仍使用未清洗的原文（batch_by_id）
    prompt_items = [
        {
            "msg_id": m["msg_id"],
            "sender": m["sender"],
            "content": sanitize(m["content"]),
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
    if not isinstance(raw_leads, list):
        logger.warning("大模型返回结构不含 leads 数组，本批按 0 条处理")
        return [], 0

    validated: list[dict] = []
    dropped = 0
    for raw in raw_leads:
        lead = _validate_lead(raw, batch_by_id, batch_items)
        if lead is None:
            dropped += 1
            logger.warning(
                "丢弃 1 条线索：category/msg_id 非法或 evidence 非原文子串；原始内容=%s",
                json.dumps(raw, ensure_ascii=False)[:200],
            )
            continue
        validated.append(lead)

    return validated, dropped


def extract_leads(
    msgs: list[dict],
    db_path: Optional[str] = None,
    batch_size: int = EXTRACT_BATCH_SIZE,
) -> dict:
    """批量抽取线索并写入 leads 表，返回 {leads, dropped, batches}。

    msgs: [{msg_id, sender, content}, ...]，空列表直接返回空结果且不调用大模型。
    """
    if not msgs:
        return {"leads": 0, "dropped": 0, "batches": 0}

    init_db(db_path).close()  # 确保 leads 表存在（幂等）
    conn = get_conn(db_path)
    cur = conn.cursor()

    saved = 0
    dropped = 0
    batches = 0

    try:
        for start in range(0, len(msgs), batch_size):
            batch = msgs[start : start + batch_size]
            batches += 1
            leads, batch_dropped = _extract_batch(batch)
            dropped += batch_dropped

            for lead in leads:
                cur.execute(
                    """
                    INSERT OR REPLACE INTO leads
                        (lead_id, msg_id, category, title, event_time, time_raw,
                         location, participants, file_hint, confidence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """,
                    (
                        lead["lead_id"],
                        lead["msg_id"],
                        lead["category"],
                        lead["title"],
                        lead["event_time"],
                        lead["time_raw"],
                        lead["location"],
                        lead["participants"],
                        lead["file_hint"],
                        lead["confidence"],
                    ),
                )
                saved += 1

        conn.commit()
    finally:
        conn.close()

    return {"leads": saved, "dropped": dropped, "batches": batches}
