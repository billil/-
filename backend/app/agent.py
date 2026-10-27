"""Agent 决策链路（T2-2 部分）：查询解析 → 一次必要追问 → 条件更正 → 混合检索。

追问预算：整个会话（按 session_id）最多追问 1 次。回答后若仍 ambiguous，
不再追问，直接按当前解析结果检索，并在 message 中说明所做假设。
"""

import json
import logging
import re
import sqlite3
from datetime import date
from typing import Optional

from .answer import generate_answer
from .cluster import cluster_leads
from .config import TOP_K_DEFAULT
from .db import get_conn, init_db
from .errors import AppError, EMPTY_QUERY
from .llm import chat_json
from .prompts.correct import CORRECT_SYSTEM_PROMPT
from .prompts.parse import PARSE_SYSTEM_PROMPT
from .search_hybrid import hybrid_search
from .session import (
    STATUS_ASKED,
    STATUS_ANSWERED,
    STATUS_PARSED,
    InMemorySessionStore,
    Session,
)

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {"event", "task", "file"}
VALID_AMBIGUOUS_FIELDS = {"time", "category", "entity"}
# U3：时间字段分离，查询"活动什么时候"走 event_time，"群里什么时候说的"走 send_time
VALID_TIME_FIELDS = {"event_time", "send_time"}
# 解析侧 filters 允许的条件键（格式 "key:value"）
VALID_FILTER_KEYS = {"category", "file_hint", "time", "time_field", "group_id"}

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DT_MINUTE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
_DT_SECOND = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

# 无结果时给用户的可操作建议（固定文案，不调用大模型）
NO_RESULT_SUGGESTIONS = [
    "把时间范围放宽到最近一个月",
    "只按类别搜索（活动/办事/文件）",
    "换一个更具体的关键词",
]

# 会话状态：默认进程内实现（TTL 30 分钟）；持久化实现由 D 提供，实现 SessionStore 协议即可替换。
# 全局 SESSION_STORE 仅作向后兼容入口——新接入方请用 app.facade.process，
# 它支持传入自定义 store 与 candidate_provider，不依赖任何模块级可变状态。
SESSION_STORE = InMemorySessionStore()

# 更正信号词：命中即视为"改条件"，按 correction 处理
CORRECTION_SIGNALS = ("不是", "改成", "换成", "记错了", "说错了", "应该是", "其实是", "纠正")


def _no_result_payload(
    session_id: str,
    result: Optional[dict] = None,
    search_debug: Optional[dict] = None,
) -> dict:
    """无结果统一返回结构；不调用大模型编造"假装有结果"的回答。"""
    payload = {
        "status": "no_result",
        "session_id": session_id,
        "suggestions": list(NO_RESULT_SUGGESTIONS),
    }
    if result is not None:
        payload["result"] = result
    if search_debug is not None:
        payload["search_debug"] = search_debug
    return payload


def get_session(session_id: str) -> Optional[dict]:
    """读取会话（兼容旧调用方：返回 dict 视图，pending 映射 asked 状态）。"""
    session = SESSION_STORE.get(session_id)
    if session is None:
        return None
    return {
        "session_id": session.session_id,
        "status": session.status,
        "query": session.query,
        "parse": session.parse,
        "result_summary": session.result_summary,
        "ask_count": session.ask_count,
        "updated_at": session.updated_at,
        "pending": session.status == STATUS_ASKED,
    }


def reset_session(session_id: str) -> None:
    """清除会话（前端开始新会话或测试隔离时使用）。"""
    SESSION_STORE.delete(session_id)


def looks_like_correction(query: str) -> bool:
    """是否包含更正信号词（如"不是""改成""换成"）。"""
    text = query or ""
    return any(signal in text for signal in CORRECTION_SIGNALS)


def followup_or_new(query: str, session: Optional[Session]) -> str:
    """判定本轮是"更正上一轮条件"还是"新问题"。

    - 命中更正信号词 → correction
    - 有会话、query 很短（<=15 字）且与原 keywords 有交集 → correction
    - 其余 → new
    """
    if looks_like_correction(query):
        return "correction"
    text = (query or "").strip()
    if session is not None and len(text) <= 15:
        keywords = (session.parse or {}).get("keywords") or []
        if any(kw and kw in text for kw in keywords):
            return "correction"
    return "new"


_LEAD_COLUMNS = (
    "lead_id",
    "msg_id",
    "category",
    "title",
    "event_time",
    "time_raw",
    "location",
    "participants",
    "file_hint",
    "confidence",
    "group_id",
    "send_time",
    "content",
    "sender_id",
)


def load_clusters(candidates: list[dict], db_path: Optional[str] = None) -> list[dict]:
    """把候选消息对应的 leads 聚成事件 cluster（含变更信息）。

    - 还没抽取线索（leads 为空）时返回 []，答案链路降级为只用消息原文。
    - leads_changes 中的变更按 msg_id 挂到对应 lead，再交给 cluster 按时间汇总。
    - 数据库异常不阻断回答，只记 warning。
    """
    msg_ids = [c.get("msg_id") for c in candidates if c.get("msg_id")]
    if not msg_ids:
        return []

    changes_by_msg: dict[str, list[dict]] = {}
    try:
        init_db(db_path).close()
        conn = get_conn(db_path)
        try:
            placeholders = ",".join("?" for _ in msg_ids)
            rows = conn.execute(
                f"""
                SELECT l.lead_id, l.msg_id, l.category, l.title, l.event_time,
                       l.time_raw, l.location, l.participants, l.file_hint,
                       l.confidence, m.group_id, m.send_time, m.content, m.sender_id
                FROM leads AS l
                JOIN messages AS m ON m.msg_id = l.msg_id
                WHERE l.msg_id IN ({placeholders})
                ORDER BY m.send_time, m.msg_id
                """,
                msg_ids,
            ).fetchall()

            change_rows = conn.execute(
                f"""
                SELECT change_id, lead_id, msg_id, type, field, old_value,
                       new_value, quote, confidence
                FROM leads_changes
                WHERE msg_id IN ({placeholders})
                ORDER BY msg_id, change_id
                """,
                msg_ids,
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("读取线索失败，跳过事件关联：%s", exc)
        return []

    for row in change_rows:
        changes_by_msg.setdefault(row[2], []).append(
            {
                "change_id": row[0],
                "lead_id": row[1],
                "msg_id": row[2],
                "type": row[3],
                "field": row[4],
                "old_value": row[5],
                "new_value": row[6],
                "quote": row[7],
                "confidence": row[8],
            }
        )

    leads = []
    for row in rows:
        lead = dict(zip(_LEAD_COLUMNS, row))
        lead["changes"] = changes_by_msg.get(lead.get("msg_id"), [])
        leads.append(lead)
    return cluster_leads(leads)


def save_session(session_id: str, query: str, parse: dict, pending: bool = False) -> None:
    """写入会话（兼容旧调用方；pending=True 表示正在等待追问回答）。"""
    status = STATUS_ASKED if pending else STATUS_PARSED
    SESSION_STORE.save(
        Session(session_id=session_id, status=status, query=query, parse=parse)
    )


def _clean_str(value) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _normalize_dt(value, is_end: bool) -> Optional[str]:
    """时间点统一为 YYYY-MM-DD HH:MM:SS；只有日期/分钟时补齐。"""
    text = _clean_str(value)
    if not text:
        return None
    text = text.replace("T", " ")
    if _DATE_ONLY.match(text):
        return text + (" 23:59:59" if is_end else " 00:00:00")
    if _DT_MINUTE.match(text):
        return text + (":59" if is_end else ":00")
    if _DT_SECOND.match(text):
        return text
    return None


def _normalize_time_block(value) -> dict:
    """规整 time 字段；type=none 时 start/end/raw 一律为 null。"""
    if not isinstance(value, dict):
        return {"type": "none", "start": None, "end": None, "raw": None}

    time_type = _clean_str(value.get("type")) or "none"
    if time_type not in {"relative", "absolute", "none"}:
        time_type = "none"

    if time_type == "none":
        return {"type": "none", "start": None, "end": None, "raw": None}

    return {
        "type": time_type,
        "start": _normalize_dt(value.get("start"), is_end=False),
        "end": _normalize_dt(value.get("end"), is_end=True),
        "raw": _clean_str(value.get("raw")),
    }


def _normalize_keywords(value) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        word = _clean_str(item)
        if word and word not in result:
            result.append(word)
    return result[:8]


def _normalize_ambiguous(value) -> Optional[dict]:
    """ambiguous 必须字段合法、问题非空、至少 2 个具体选项，否则视为不需要追问。"""
    if not isinstance(value, dict):
        return None
    field = _clean_str(value.get("field"))
    question = _clean_str(value.get("question"))
    options_raw = value.get("options")
    if field not in VALID_AMBIGUOUS_FIELDS or not question or not isinstance(options_raw, list):
        return None
    options = [_clean_str(o) for o in options_raw]
    options = [o for o in options if o]
    if len(options) < 2:
        return None
    return {"field": field, "question": question, "options": options[:4]}


def _normalize_filter_list(value) -> list[str]:
    """规整 filters.explicit / vague：只保留 "已知键:非空值" 格式，去重限量。"""
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _clean_str(item)
        if not text or ":" not in text:
            continue
        key, val = text.split(":", 1)
        key = key.strip().lower()
        val = val.strip()
        if key not in VALID_FILTER_KEYS or not val:
            continue
        normalized = f"{key}:{val}"
        if normalized not in result:
            result.append(normalized)
    return result[:8]


def _normalize_filters(value) -> dict:
    """filters 固定为 {explicit, vague} 两组字符串列表。"""
    data = value if isinstance(value, dict) else {}
    return {
        "explicit": _normalize_filter_list(data.get("explicit")),
        "vague": _normalize_filter_list(data.get("vague")),
    }


def _normalize_time_field(value) -> str:
    """time_field 只允许 event_time / send_time，非法或缺失时默认 event_time。"""
    text = _clean_str(value)
    return text if text in VALID_TIME_FIELDS else "event_time"


def _normalize_parse(raw) -> dict:
    """把大模型输出规整为固定结构，非法字段一律降级为"未指定"。"""
    data = raw if isinstance(raw, dict) else {}
    category = _clean_str(data.get("category")) or "any"
    if category not in VALID_CATEGORIES:
        category = "any"
    return {
        "keywords": _normalize_keywords(data.get("keywords")),
        "time": _normalize_time_block(data.get("time")),
        "time_field": _normalize_time_field(data.get("time_field")),
        "category": category,
        "file_hint": _clean_str(data.get("file_hint")),
        "filters": _normalize_filters(data.get("filters")),
        "ambiguous": _normalize_ambiguous(data.get("ambiguous")),
    }


def _split_filter_entries(entries: list[str]) -> dict[str, str]:
    """把 ["category:file", ...] 转成 {category: file}；同键后出现的值覆盖前面的。"""
    mapping: dict[str, str] = {}
    for entry in entries:
        key, val = entry.split(":", 1)
        mapping[key] = val.strip()
    return mapping


def build_search_filters(parse: dict) -> dict:
    """把解析结果转成检索 filters：explicit 做硬过滤，vague 只做软加权。

    - category / file_hint：用户明确说出（或在无模糊表述时解析出）→ 硬过滤；
      只有模糊表述时 → 放进 soft_boost
    - time_range：有明确时间区间且未被标为模糊 → 硬过滤
    - time_field：显式 > 解析结果 > 默认 event_time
    """
    parse = parse or {}
    filters_block = parse.get("filters") or {}
    explicit = filters_block.get("explicit") or []
    vague = filters_block.get("vague") or []
    exp = _split_filter_entries(explicit)
    vg = _split_filter_entries(vague)
    vague_keys = set(vg)
    # 只有模糊表述、没有任何明确条件时，解析出的类别/文件线索一并降级为软加权
    only_vague = bool(vague_keys) and not exp

    time_field = exp.get("time_field") or parse.get("time_field") or "event_time"
    if time_field not in VALID_TIME_FIELDS:
        time_field = "event_time"

    filters: dict = {
        "category": None,
        "file_hint": None,
        "time_range": None,
        "time_field": time_field,
        "group_id": exp.get("group_id") or None,
        "soft_boost": {"category": None, "file_hint": None},
    }

    parsed_category = parse.get("category")
    category = exp.get("category") or (
        parsed_category if parsed_category in VALID_CATEGORIES else None
    )
    if category in VALID_CATEGORIES:
        if "category" in exp or (not only_vague and "category" not in vague_keys):
            filters["category"] = category
        else:
            filters["soft_boost"]["category"] = category

    file_hint = exp.get("file_hint") or parse.get("file_hint")
    if file_hint:
        if "file_hint" in exp or (not only_vague and "file_hint" not in vague_keys):
            filters["file_hint"] = file_hint
        else:
            filters["soft_boost"]["file_hint"] = file_hint

    time_block = parse.get("time") or {}
    if (
        time_block.get("type") not in (None, "none")
        and "time" not in vague_keys
        and time_block.get("start")
        and time_block.get("end")
    ):
        filters["time_range"] = {
            "start": time_block["start"],
            "end": time_block["end"],
        }

    # 模糊条件里的类别/文件线索进入软加权（覆盖同名解析值）
    if vg.get("category") in VALID_CATEGORIES:
        filters["soft_boost"]["category"] = vg["category"]
    if vg.get("file_hint"):
        filters["soft_boost"]["file_hint"] = vg["file_hint"]

    return filters


def parse_query(query: str, today: Optional[str] = None) -> dict:
    """把自然语言查询解析为结构化检索条件。"""
    today = today or date.today().isoformat()
    user_payload = json.dumps({"today": today, "query": query}, ensure_ascii=False)
    raw = chat_json(
        [
            {"role": "system", "content": PARSE_SYSTEM_PROMPT},
            {"role": "user", "content": user_payload},
        ]
    )
    return _normalize_parse(raw)


def correct_parse(original: dict, correction: str, today: Optional[str] = None) -> dict:
    """按用户更正语句修改原解析结果（改条件 = 替换，不是叠加）。"""
    today = today or date.today().isoformat()
    user_payload = json.dumps(
        {"today": today, "original": original, "correction": correction},
        ensure_ascii=False,
    )
    raw = chat_json(
        [
            {"role": "system", "content": CORRECT_SYSTEM_PROMPT},
            {"role": "user", "content": user_payload},
        ]
    )
    return _normalize_parse(raw)


def _assumption_text(ambiguous: dict) -> str:
    """把仍不确定的字段转成一句人话假设说明（禁止推测补全，只说明取值范围）。"""
    field = ambiguous.get("field")
    if field == "time":
        return "未能确认时间范围，已按不限时间检索"
    if field == "category":
        return "未能确认要找的类别，已在活动、办事、文件三类中一起检索"
    return "未能确认具体对象，已按全部候选检索"


def _run_search_and_answer(
    session_id: str,
    search_query: str,
    parse: dict,
    assumptions: list[str],
    db_path: Optional[str],
    limit: int,
    ask_count: int,
    context_lost: bool = False,
) -> dict:
    """检索 + 生成答案，并把 status=answered 的会话写回（回答后不删除上下文）。"""
    filters = build_search_filters(parse)
    search_out = hybrid_search(
        query=search_query,
        limit=limit,
        filters=filters,
        db_path=db_path,
    )
    candidates = search_out.get("results") or []
    search_debug = search_out.get("debug") or {}
    outcome = search_debug.get("outcome") or "ok"
    degraded = bool(search_debug.get("degraded"))
    degraded_reason = search_debug.get("degraded_reason")
    degraded_note = "本次仅使用关键词检索，结果可能不完整" if degraded else ""

    def _tag(payload: dict) -> dict:
        """统一附加 outcome 与降级标记，保证最终响应可区分三分状态。"""
        payload["outcome"] = outcome
        if degraded:
            payload["degraded"] = True
            payload["degraded_reason"] = degraded_reason
        return payload

    if not candidates:
        SESSION_STORE.save(
            Session(
                session_id=session_id,
                status=STATUS_ANSWERED,
                query=search_query,
                parse=parse,
                ask_count=ask_count,
            )
        )
        payload = _no_result_payload(session_id, search_debug=search_debug)
        payload["message"] = "群里没有找到相关资料"
        if degraded_note:
            payload["message"] += "；" + degraded_note
        payload = _tag(payload)
        if context_lost:
            payload["context_lost"] = True
        return payload

    # 低相关：有召回但全部低于阈值，只返回最接近的几条，不生成结论
    if outcome == "low_relevance":
        SESSION_STORE.save(
            Session(
                session_id=session_id,
                status=STATUS_ANSWERED,
                query=search_query,
                parse=parse,
                ask_count=ask_count,
            )
        )
        payload = {
            "status": "low_relevance",
            "session_id": session_id,
            "message": "没有找到明确匹配，这几个可能相关",
            "low_confidence": True,
            "assumptions": assumptions,
            "candidates": candidates,
            "parse": parse,
            "search_debug": search_debug,
        }
        if degraded_note:
            payload["message"] += "；" + degraded_note
        payload = _tag(payload)
        if context_lost:
            payload["context_lost"] = True
        return payload

    clusters = load_clusters(candidates, db_path)
    result = generate_answer(search_query, candidates, assumptions, clusters)
    summary = result.get("answer") if result.get("status") == "ok" else None
    SESSION_STORE.save(
        Session(
            session_id=session_id,
            status=STATUS_ANSWERED,
            query=search_query,
            parse=parse,
            result_summary=summary,
            ask_count=ask_count,
        )
    )

    if result.get("status") == "uncertain":
        # 有原文，但关键字段无法核验/互相冲突：不返回任何结论句
        payload = {
            "status": "uncertain",
            "session_id": session_id,
            "message": "现有原文能对上，但关键字段无法核验或存在冲突，暂不给出结论。",
            "assumptions": assumptions,
            "candidates": candidates,
            "clusters": clusters,
            "result": result,
            "parse": parse,
            "search_debug": search_debug,
        }
        if degraded_note:
            payload["message"] += "；" + degraded_note
        payload = _tag(payload)
        if context_lost:
            payload["context_lost"] = True
        return payload

    if result.get("status") == "no_result":
        # 检索有候选但答案校验全部失败：同样按无结果返回，并保留本次检索状态
        payload = _no_result_payload(session_id, result)
        payload["message"] = "群里没有找到相关资料"
        if degraded_note:
            payload["message"] += "；" + degraded_note
        payload = _tag(payload)
        if context_lost:
            payload["context_lost"] = True
        return payload

    payload = {
        "status": "answer",
        "session_id": session_id,
        "message": ("说明：" + "；".join(assumptions)) if assumptions else "",
        "assumptions": assumptions,
        "candidates": candidates,
        "clusters": clusters,
        "result": result,
        "parse": parse,
        "search_debug": search_debug,
    }
    if degraded_note:
        payload["message"] = (
            payload["message"] + "；" + degraded_note
            if payload["message"]
            else degraded_note
        )
    payload = _tag(payload)
    if context_lost:
        payload["context_lost"] = True
    return payload


def answer_flow(
    query: str,
    session_id: str,
    db_path: Optional[str] = None,
    today: Optional[str] = None,
    limit: int = TOP_K_DEFAULT,
) -> dict:
    """主流程：解析 →（必要时）追问一次 → 更正判定 → 混合检索 → 带引用答案。

    - 追问预算：整个会话最多 1 次（ask_count >= 1 不再追问）
    - 回答后保留会话（status=answered），供后续"不是这周，是下周"这类更正使用
    - 会话过期/不存在、但语句明显是更正时，按新查询处理并标记 context_lost
    """
    today = today or date.today().isoformat()
    if not query or not query.strip():
        reset_session(session_id)
        raise AppError(EMPTY_QUERY)

    session = SESSION_STORE.get(session_id)
    intent = followup_or_new(query, session)
    context_lost = session is None and looks_like_correction(query)

    # 1) 上一轮在等追问回答：用"原查询 + 回答"重新解析，预算已用完，不再追问
    if session is not None and session.status == STATUS_ASKED:
        search_query = f"{session.query} {query}".strip()
        parse = parse_query(search_query, today=today)
        ambiguous = parse.get("ambiguous")
        assumptions = [_assumption_text(ambiguous)] if ambiguous else []
        return _run_search_and_answer(
            session_id,
            search_query,
            parse,
            assumptions,
            db_path,
            limit,
            session.ask_count,
        )

    # 2) 更正：以会话内最新 parse 为基线替换字段（不是叠加），追问预算不变
    if session is not None and intent == "correction":
        parse = correct_parse(session.parse, query, today=today)
        search_query = " ".join(parse["keywords"]) or query
        return _run_search_and_answer(
            session_id,
            search_query,
            parse,
            [],
            db_path,
            limit,
            session.ask_count,
        )

    # 3) 新查询
    parse = parse_query(query, today=today)
    ambiguous = parse.get("ambiguous")
    previous_ask_count = session.ask_count if session is not None else 0

    if ambiguous and previous_ask_count < 1:
        SESSION_STORE.save(
            Session(
                session_id=session_id,
                status=STATUS_ASKED,
                query=query,
                parse=parse,
                ask_count=previous_ask_count + 1,
            )
        )
        payload = {
            "status": "ask",
            "session_id": session_id,
            "question": ambiguous["question"],
            "options": ambiguous["options"],
            "parse": parse,
        }
        if context_lost:
            payload["context_lost"] = True
        return payload

    assumptions = [_assumption_text(ambiguous)] if ambiguous else []
    return _run_search_and_answer(
        session_id,
        query,
        parse,
        assumptions,
        db_path,
        limit,
        previous_ask_count,
        context_lost=context_lost,
    )


def revise_flow(
    session_id: str,
    correction: str,
    db_path: Optional[str] = None,
    today: Optional[str] = None,
    limit: int = TOP_K_DEFAULT,
) -> dict:
    """条件更正流程：读取会话中最新的 parse → 按更正语句替换字段 → 重新检索。

    - 改条件 = 替换，不是叠加（提示词层面约束）。
    - 会话不存在时降级为普通 answer_flow，并在返回里标记 context_lost。
    - 更正后 ask_count 不变（追问预算仍按会话计），status 回到 answered。
    """
    today = today or date.today().isoformat()
    if not correction or not correction.strip():
        raise AppError(EMPTY_QUERY)

    session = SESSION_STORE.get(session_id)
    if session is None or not session.parse:
        payload = answer_flow(
            correction, session_id, db_path=db_path, today=today, limit=limit
        )
        payload.setdefault("context_lost", True)
        return payload

    parse = correct_parse(session.parse, correction, today=today)
    search_query = " ".join(parse["keywords"]) or correction
    return _run_search_and_answer(
        session_id,
        search_query,
        parse,
        [],
        db_path,
        limit,
        session.ask_count,
    )
