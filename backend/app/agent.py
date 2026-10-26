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

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {"event", "task", "file"}
VALID_AMBIGUOUS_FIELDS = {"time", "category", "entity"}

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DT_MINUTE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
_DT_SECOND = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

# 无结果时给用户的可操作建议（固定文案，不调用大模型）
NO_RESULT_SUGGESTIONS = [
    "把时间范围放宽到最近一个月",
    "只按类别搜索（活动/办事/文件）",
    "换一个更具体的关键词",
]

# 会话缓存：session_id -> {"query": 原始查询, "parse": 解析结果, "pending": 是否在等待回答}
# 进程级内存 dict：进程重启即丢失（追问上下文重建），当前阶段可接受。
_SESSIONS: dict[str, dict] = {}


def _no_result_payload(session_id: str, result: Optional[dict] = None) -> dict:
    """无结果统一返回结构；不调用大模型编造"假装有结果"的回答。"""
    payload = {
        "status": "no_result",
        "session_id": session_id,
        "suggestions": list(NO_RESULT_SUGGESTIONS),
    }
    if result is not None:
        payload["result"] = result
    return payload


def get_session(session_id: str) -> Optional[dict]:
    """读取会话缓存。"""
    return _SESSIONS.get(session_id)


def reset_session(session_id: str) -> None:
    """清除会话缓存（前端开始新会话或测试隔离时使用）。"""
    _SESSIONS.pop(session_id, None)


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
)


def load_clusters(candidates: list[dict], db_path: Optional[str] = None) -> list[dict]:
    """把候选消息对应的 leads 聚成事件 cluster（含改期/取消标记）。

    - 还没抽取线索（leads 为空）时返回 []，答案链路降级为只用消息原文。
    - 数据库异常不阻断回答，只记 warning。
    """
    msg_ids = [c.get("msg_id") for c in candidates if c.get("msg_id")]
    if not msg_ids:
        return []

    try:
        init_db(db_path).close()
        conn = get_conn(db_path)
        try:
            placeholders = ",".join("?" for _ in msg_ids)
            rows = conn.execute(
                f"""
                SELECT l.lead_id, l.msg_id, l.category, l.title, l.event_time,
                       l.time_raw, l.location, l.participants, l.file_hint,
                       l.confidence, m.group_id, m.send_time, m.content
                FROM leads AS l
                JOIN messages AS m ON m.msg_id = l.msg_id
                WHERE l.msg_id IN ({placeholders})
                ORDER BY m.send_time, m.msg_id
                """,
                msg_ids,
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("读取线索失败，跳过事件关联：%s", exc)
        return []

    leads = [dict(zip(_LEAD_COLUMNS, row)) for row in rows]
    return cluster_leads(leads)


def save_session(session_id: str, query: str, parse: dict, pending: bool = False) -> None:
    """写入会话缓存（供条件更正等需要"上一轮解析"的场景使用）。"""
    _SESSIONS[session_id] = {"query": query, "parse": parse, "pending": pending}


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


def _normalize_parse(raw) -> dict:
    """把大模型输出规整为固定结构，非法字段一律降级为"未指定"。"""
    data = raw if isinstance(raw, dict) else {}
    category = _clean_str(data.get("category")) or "any"
    if category not in VALID_CATEGORIES:
        category = "any"
    return {
        "keywords": _normalize_keywords(data.get("keywords")),
        "time": _normalize_time_block(data.get("time")),
        "category": category,
        "file_hint": _clean_str(data.get("file_hint")),
        "ambiguous": _normalize_ambiguous(data.get("ambiguous")),
    }


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


def answer_flow(
    query: str,
    session_id: str,
    db_path: Optional[str] = None,
    today: Optional[str] = None,
    limit: int = TOP_K_DEFAULT,
) -> dict:
    """主流程：解析 →（必要时）追问一次 → 条件更正 → 混合检索。

    返回两种状态：
    - {"status": "ask", ...}：需要用户回答，parse 已存入会话缓存
    - {"status": "answer", ...}：已检索出候选；assumptions 为空表示没有做任何假设
    """
    today = today or date.today().isoformat()
    if not query or not query.strip():
        _SESSIONS.pop(session_id, None)
        raise AppError(EMPTY_QUERY)

    state = _SESSIONS.get(session_id)
    waiting_answer = bool(state and state.get("pending"))

    if waiting_answer:
        # 条件更正：把原查询与用户补充回答一起重新解析
        search_query = f"{state.get('query', '')} {query}".strip()
    else:
        search_query = query

    parse = parse_query(search_query, today=today)
    ambiguous = parse.get("ambiguous")

    # 追问预算：整个会话最多 1 次（waiting_answer=True 时不再追问）
    if ambiguous and not waiting_answer:
        _SESSIONS[session_id] = {"query": query, "parse": parse, "pending": True}
        return {
            "status": "ask",
            "session_id": session_id,
            "question": ambiguous["question"],
            "options": ambiguous["options"],
            "parse": parse,
        }

    assumptions = [_assumption_text(ambiguous)] if ambiguous else []
    _SESSIONS.pop(session_id, None)

    time_block = parse["time"]
    candidates = hybrid_search(
        query=search_query,
        keywords=parse["keywords"] or None,
        time_start=time_block["start"],
        time_end=time_block["end"],
        limit=limit,
        db_path=db_path,
    )

    # 无候选：不调用大模型，直接返回建议
    if not candidates:
        return _no_result_payload(session_id)

    clusters = load_clusters(candidates, db_path)
    result = generate_answer(search_query, candidates, assumptions, clusters)
    if result.get("status") == "no_result":
        # 引用全部被剔除：同样不保留模型结论
        return _no_result_payload(session_id, result)

    return {
        "status": "answer",
        "session_id": session_id,
        "message": ("说明：" + "；".join(assumptions)) if assumptions else "",
        "assumptions": assumptions,
        "candidates": candidates,
        "clusters": clusters,
        "result": result,
        "parse": parse,
    }


def revise_flow(
    session_id: str,
    correction: str,
    db_path: Optional[str] = None,
    today: Optional[str] = None,
    limit: int = TOP_K_DEFAULT,
) -> dict:
    """条件更正流程：读取会话中原 parse → 按更正语句替换字段 → 用新条件检索。

    - 改条件 = 替换，不是叠加（提示词层面约束，返回的 parse 已是替换后的完整条件）。
    - 会话无原解析时降级为普通 answer_flow（把更正语句当作新查询）。
    - 更正后仍把新 parse 写回会话缓存，便于连续多次更正。
    """
    today = today or date.today().isoformat()
    if not correction or not correction.strip():
        raise AppError(EMPTY_QUERY)

    state = _SESSIONS.get(session_id)
    original = state.get("parse") if isinstance(state, dict) else None

    if not original:
        return answer_flow(
            correction, session_id, db_path=db_path, today=today, limit=limit
        )

    parse = correct_parse(original, correction, today=today)
    save_session(session_id, correction, parse, pending=False)

    time_block = parse["time"]
    search_query = " ".join(parse["keywords"]) or correction
    candidates = hybrid_search(
        query=search_query,
        keywords=parse["keywords"] or None,
        time_start=time_block["start"],
        time_end=time_block["end"],
        limit=limit,
        db_path=db_path,
    )

    if not candidates:
        return _no_result_payload(session_id)

    clusters = load_clusters(candidates, db_path)
    result = generate_answer(search_query, candidates, [], clusters)
    if result.get("status") == "no_result":
        return _no_result_payload(session_id, result)

    return {
        "status": "answer",
        "session_id": session_id,
        "message": "",
        "assumptions": [],
        "candidates": candidates,
        "clusters": clusters,
        "result": result,
        "parse": parse,
    }
