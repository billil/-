"""facade：交付给 D 的唯一对外入口，纯函数式、无全局可变状态。

约定：
- 所有会话读写走调用方注入的 SessionStore；模块内不 new 全局 store。
- 所有候选消息走调用方注入的 candidate_provider；本模块不直接查业务表。
- 大模型客户端通过 app.llm 单例获取，本模块不额外读环境变量。
- 返回结构统一为 {"status": ..., "parse": {...}, "answer": {...},
  "evidence": [...], "debug": {...}}；debug 默认只在 settings.DEBUG=true 时填充。

依赖的内部模块（B 自己维护）：
- agent.parse_query / correct_parse / build_search_filters / followup_or_new /
  looks_like_correction / load_clusters / NO_RESULT_SUGGESTIONS / Session / 状态常量
- agent.generate_answer
"""

from __future__ import annotations

from typing import Any, Callable, Optional

# 通过模块引用（而非 from import），便于测试中整体 monkeypatch。
from . import agent
from .config import TOP_K_DEFAULT
from .session import SessionStore


# ---------------------------------------------------------------------------
# 数据契约
# ---------------------------------------------------------------------------

# ParseResult：与 agent.parse_query 的返回结构一致，facade 透传不做二次归一化。
ParseResult = dict[str, Any]

# AnswerResult：与 answer.generate_answer 的返回结构一致，至少包含 status / answer /
# events / evidence 四个字段，其他字段视大模型输出而定。
AnswerResult = dict[str, Any]

# CandidateResult：candidate_provider 返回的候选项结构，字段尽量与 hybrid_search.results 对齐。
# 最少要有 msg_id / content / sender / send_time；可选 score / sources / group_id / category。
CandidateResult = dict[str, Any]

# candidate_provider 契约：接收 build_search_filters 生成的 filters dict，
# 返回候选列表。facade 不关心其内部实现（SQLite / MySQL / 向量库 / 远端服务皆可）。
CandidateProvider = Callable[[dict[str, Any]], list[CandidateResult]]


# ---------------------------------------------------------------------------
# facade settings：debug 开关由 D 控制，B 不预设
# ---------------------------------------------------------------------------

class _Settings:
    DEBUG = False


settings = _Settings()


def _maybe_debug(payload: dict, items: dict[str, Any]) -> dict:
    """settings.DEBUG 为真时，把 items 合入 payload.debug；否则保持原样。"""
    if not items:
        return payload
    if settings.DEBUG:
        debug = payload.setdefault("debug", {})
        debug.update(items)
    return payload


# ---------------------------------------------------------------------------
# 5 个对外接口
# ---------------------------------------------------------------------------


def analyze_query(
    query: str,
    today: str,
    context: Optional[dict] = None,
) -> ParseResult:
    """把自然语言查询解析为结构化检索条件。

    Args:
        query: 用户原始问题（如 "这周末的志愿活动"）。
        today: 当天日期（YYYY-MM-DD），用于相对时间换算。
        context: 可选上下文；保留参数，当前版本未使用。

    Returns:
        解析结果 dict，结构与 app.agent.parse_query 一致，关键字段包括：
        - keywords: list[str]  检索关键词
        - time: {"type": "relative"|"absolute"|"none", "start"| "end"|"raw": ...}
        - time_field: "event_time" | "send_time"
        - category: "event" | "task" | "file" | None
        - file_hint: str | None
        - filters: {"explicit": [...], "vague": [...]}
        - ambiguous:  存在字段歧义时才返回，内含 field / question / options

    Raises:
        app.errors.LLMFormatError: 大模型返回无法解析的 JSON（已重试一次）。
        app.errors.AppError: 大模型不可用（2003：未配置 / 网关拒绝 / 流式中断）或超时（2001）。
    """
    return agent.parse_query(query, today=today)


def revise(prev_parse: dict, correction: str, today: str) -> ParseResult:
    """按用户更正语句修改原解析结果。

    改条件 = 替换，不是叠加。例如原查询 "周六的活动"，用户说 "不是周六是周日"，
    返回时间字段替换为周日后的 parse；keywords 不变。

    Args:
        prev_parse: analyze_query 上一次返回的 dict。
        correction: 更正语句（如 "不是周六，改成周日"）。
        today: 当天日期 YYYY-MM-DD，用于相对时间换算。

    Returns:
        修改后的 ParseResult；结构与 analyze_query 一致。

    Raises:
        同 analyze_query。
    """
    return agent.correct_parse(prev_parse, correction, today=today)


def retrieve(
    parse: dict,
    candidate_provider: CandidateProvider,
    limit: int = TOP_K_DEFAULT,
    group_scope: Optional[list[str]] = None,
    allowed_msg_ids: Optional[list[str]] = None,
) -> dict:
    """按 parse 构造检索 filters，调用 candidate_provider 拉候选。

    facade 不直接查业务表：filters 由 build_search_filters(parse) 生成，
    candidate_provider 内部决定怎么用这些 filters（SQLite FTS / MySQL / 向量库均可）。
    limit 作为"希望返回的候选数"传给 provider；provider 可以自行决定截断策略
    （关键词 / 向量融合、阈值过滤等），facade 不做跨群扩召回。

    T2 权限契约：group_scope（用户可见群列表）与 allowed_msg_ids（消息白名单）
    至少必须提供一个；两者都缺时视为无权限，直接返回 permission_denied，
    不会把"没有权限"当成"可以查全量"。provider 返回的越权候选一律丢弃并计数。

    Args:
        parse: analyze_query / revise 的返回。
        candidate_provider: 接收 filters dict、返回 list[CandidateResult] 的 callable。
        limit: 希望返回的最大候选数（默认 TOP_K_DEFAULT）。
        group_scope: D 授权的群列表；候选 group_id 不在其中即丢弃。
        allowed_msg_ids: D 授权的消息白名单；候选 msg_id 不在其中即丢弃。

    Returns:
        {"status": "ok" | "permission_denied", "results": list[CandidateResult],
         "message": str, "debug": dict}；debug 仅在 settings.DEBUG 时填充。

    Raises:
        TypeError: candidate_provider 不是可调用对象。
        由 candidate_provider 抛出的异常原样向上传递（facade 不吞）。
    """
    if not callable(candidate_provider):
        raise TypeError("candidate_provider 必须是可调用对象：receive_filters(dict) -> list")

    if group_scope is None and allowed_msg_ids is None:
        payload: dict[str, Any] = {
            "status": agent.STATUS_PERMISSION_DENIED,
            "results": [],
            "message": agent.MESSAGE_PERMISSION_DENIED,
        }
        _maybe_debug(payload, {"permission": "missing_group_scope_and_allowed_msg_ids"})
        return payload

    filters = agent.build_search_filters(parse)
    filters["_limit"] = limit  # 附加希望返回的条数，provider 可参考
    filters["group_scope"] = list(group_scope) if group_scope is not None else None
    filters["allowed_msg_ids"] = (
        list(allowed_msg_ids) if allowed_msg_ids is not None else None
    )
    raw_results = list(candidate_provider(filters) or [])

    scope = set(group_scope) if group_scope is not None else None
    allowed = set(allowed_msg_ids) if allowed_msg_ids is not None else None
    results: list[CandidateResult] = []
    dropped_out_of_scope = 0
    dropped_not_allowed = 0
    for candidate in raw_results:
        # 缺 group_id 视为无法确认授权范围，按越权丢弃
        if scope is not None and candidate.get("group_id") not in scope:
            dropped_out_of_scope += 1
            continue
        if allowed is not None and candidate.get("msg_id") not in allowed:
            dropped_not_allowed += 1
            continue
        results.append(candidate)

    payload = {"status": "ok", "results": results, "message": ""}
    _maybe_debug(
        payload,
        {
            "filters": filters,
            "count": len(results),
            "dropped_out_of_scope": dropped_out_of_scope,
            "dropped_not_allowed": dropped_not_allowed,
        },
    )
    return payload


def build_answer(
    parse: dict,
    candidates: list[dict],
    db_path: Optional[str] = None,
    group_scope: Optional[list[str]] = None,
    allowed_msg_ids: Optional[list[str]] = None,
) -> AnswerResult:
    """把 parse 与候选消息交给大模型生成答案。

    Args:
        parse: analyze_query / revise 的返回。
        candidates: retrieve 的 results，至少每条要有 msg_id / content。
        db_path: 业务库路径（仅供 load_clusters 抽 leads 用，可传 None 则跳过事件关联）。
        group_scope: 本次授权群列表，继续透传给 validate_answer 做 L3 归属校验。
        allowed_msg_ids: 本次授权消息白名单，继续透传给 validate_answer 做 L3 校验。

    Returns:
        dict，关键字段：
        - status: "ok" | "uncertain" | "no_result"
        - answer: str  最终答案文本
        - events: list[dict]  结构化事件列表（可空）
        - evidence: list[dict]  引用来源（{msg_id, quote}）
        - fields: dict  事件字段（event_time / location 等）

    Raises:
        同 analyze_query。
    """
    # 答案生成内部还要从 candidates 里抽 leads 做 cluster；cluster 自身不依赖 LLM，
    # 但需要读 leads / leads_changes 表。这部分是 B 内部的实现细节，D 不需要关心。
    assumptions = _build_assumptions(parse)
    clusters = agent.load_clusters(candidates, db_path=db_path) if candidates and db_path else []
    result = agent.generate_answer(
        " ".join(parse.get("keywords") or []) or "",
        candidates,
        assumptions,
        clusters,
        group_scope=group_scope,
        allowed_msg_ids=allowed_msg_ids,
    )
    # 把 clusters 一并附带，D 如需结构化事件列表可直接读
    if isinstance(result, dict):
        result.setdefault("clusters", clusters)
    return result


def process(
    query: str,
    session: Optional[dict],
    candidate_provider: CandidateProvider,
    store: SessionStore,
    db_path: Optional[str] = None,
    today: Optional[str] = None,
    limit: int = TOP_K_DEFAULT,
    group_scope: Optional[list[str]] = None,
    allowed_msg_ids: Optional[list[str]] = None,
) -> dict:
    """端到端流程：解析 → 必要时追问一次 → 更正判定 → 检索 → 答案生成 → 会话写回。

    与 app.agent.answer_flow 等价的无副作用版本：会话读写走传入的 store、
    检索走 candidate_provider、大模型走 llm.py 单例。

    Args:
        query: 本轮用户问题（追问回答时是用户补全的那句话）。
        session: 上游 store.get(session_id) 的返回视图（dict 结构，字段同 Session dataclass）。
                 第一次调用可以传 None 或空 dict。
        candidate_provider: 见 retrieve 文档。
        store: 注入的 SessionStore（Protocol），facade 用它做 get / save / touch。
        db_path: 业务库路径（仅供 load_clusters 抽 leads 用，可传 None 则跳过事件关联）。
        today: 当天日期 YYYY-MM-DD，默认当天。
        limit: 希望返回的候选数。
        group_scope: D 授权的群列表（与 allowed_msg_ids 至少提供一个）。
        allowed_msg_ids: D 授权的消息白名单；两者都缺时直接返回 permission_denied，
            且在解析前返回，不调用大模型。

    Returns:
        统一响应结构：
        {
            "status": "answer" | "ask" | "no_data" | "low_relevance" |
                      "uncertain" | "retrieval_degraded" | "permission_denied",
            "parse": {...},
            "answer": {...} | None,
            "evidence": [...],
            "debug": {...},        # 仅 settings.DEBUG=true 时填充
            "suggestions": [...], # 仅 no_data 时
            "assumptions": [...], # 有模糊字段时
            "context_lost": bool, # 会话过期但用户明显在更正时为 True
        }

    Raises:
        app.errors.AppError / LLMFormatError / 由 store / candidate_provider 抛出的异常原样向上传递。
    """
    from datetime import date

    today = today or date.today().isoformat()

    # T2 权限前置：没有 D 授权范围时立即拒绝，不解析、不检索、不调用大模型
    if group_scope is None and allowed_msg_ids is None:
        payload: dict[str, Any] = {
            "status": agent.STATUS_PERMISSION_DENIED,
            "parse": {},
            "answer": None,
            "evidence": [],
            "message": agent.MESSAGE_PERMISSION_DENIED,
        }
        _maybe_debug(payload, {"permission": "missing_group_scope_and_allowed_msg_ids"})
        return payload

    session_obj = _dict_to_session(session)
    ask_count = session_obj.ask_count if session_obj else 0
    context_lost = (
        session_obj is None and bool(query) and agent.looks_like_correction(query)
    )

    # 1) 上一轮在等追问回答：用"原查询 + 本轮回答"重新解析，预算已用完，不再追问
    if session_obj is not None and session_obj.status == agent.STATUS_ASKED:
        parse = agent.parse_query(f"{session_obj.query} {query}".strip(), today=today)
        assumptions = [_ambiguous_assumption(parse.get("ambiguous"))] if parse.get("ambiguous") else []
        _save(store, session_obj, status=agent.STATUS_ANSWERED, parse=parse, ask_count=ask_count)
        return _retrieve_and_answer(
            parse, query, assumptions, candidate_provider, store, session_obj,
            db_path, limit, context_lost, group_scope, allowed_msg_ids,
        )

    # 2) 更正：会话里有最新 parse，按更正语句替换字段
    if session_obj is not None and agent.followup_or_new(query, session_obj) == "correction":
        parse = agent.correct_parse(session_obj.parse, query, today=today)
        _save(store, session_obj, status=agent.STATUS_ANSWERED, parse=parse, ask_count=ask_count)
        return _retrieve_and_answer(
            parse, " ".join(parse.get("keywords") or []) or query,
            [], candidate_provider, store, session_obj,
            db_path, limit, context_lost, group_scope, allowed_msg_ids,
        )

    # 3) 新查询
    parse = agent.parse_query(query, today=today)
    ambiguous = parse.get("ambiguous")

    if ambiguous and ask_count < 1:
        new_session = agent.Session(
            session_id=session_obj.session_id if session_obj else _infer_session_id(session),
            status=agent.STATUS_ASKED,
            query=query,
            parse=parse,
            ask_count=ask_count + 1,
        )
        store.save(new_session)
        payload: dict[str, Any] = {
            "status": "ask",
            "parse": parse,
            "assumptions": [],
            "question": ambiguous.get("question"),
            "options": ambiguous.get("options") or [],
        }
        if context_lost:
            payload["context_lost"] = True
        return payload

    assumptions = [_ambiguous_assumption(ambiguous)] if ambiguous else []
    _save(
        store,
        session_obj or _new_session_from_query(session, query, parse, ask_count + 1 if ambiguous else ask_count),
        status=agent.STATUS_ANSWERED,
        parse=parse,
        ask_count=ask_count,
    )
    return _retrieve_and_answer(
        parse, query, assumptions, candidate_provider, store, session_obj,
        db_path, limit, context_lost, group_scope, allowed_msg_ids,
    )


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _retrieve_and_answer(
    parse: dict,
    query: str,
    assumptions: list[str],
    candidate_provider: CandidateProvider,
    store: SessionStore,
    session_obj: Optional[agent.Session],
    db_path: Optional[str],
    limit: int,
    context_lost: bool,
    group_scope: Optional[list[str]] = None,
    allowed_msg_ids: Optional[list[str]] = None,
) -> dict:
    """retrieve + build_answer 的内部组合；process() 中多处复用。"""
    retrieval = retrieve(
        parse,
        candidate_provider,
        limit=limit,
        group_scope=group_scope,
        allowed_msg_ids=allowed_msg_ids,
    )
    # 理论上 process 已在入口拦截缺权限；这里兜底，确保越权路径不会调用大模型
    if retrieval.get("status") == agent.STATUS_PERMISSION_DENIED:
        payload: dict[str, Any] = {
            "status": agent.STATUS_PERMISSION_DENIED,
            "parse": parse,
            "assumptions": assumptions,
            "answer": None,
            "evidence": [],
            "message": agent.MESSAGE_PERMISSION_DENIED,
        }
        _maybe_debug(payload, retrieval.get("debug") or {})
        if context_lost:
            payload["context_lost"] = True
        return payload

    candidates = retrieval.get("results") or []

    # candidate_provider 可以自己实现阈值过滤与 low_relevance 标记；
    # facade 只从候选项上读 low_confidence / degraded 标记并透传到响应。
    all_low = bool(candidates) and all(bool(c.get("low_confidence")) for c in candidates)
    degraded = bool(any(c.get("degraded") for c in candidates)) or bool(retrieval.get("debug", {}).get("degraded"))

    if not candidates:
        payload = {
            "status": agent.STATUS_NO_DATA,
            "parse": parse,
            "assumptions": assumptions,
            "suggestions": list(agent.NO_RESULT_SUGGESTIONS),
            "answer": None,
            "evidence": [],
        }
        _maybe_debug(payload, retrieval.get("debug") or {})
        if degraded:
            # 降级时不能冒充 no_data：状态与文案都必须是 retrieval_degraded
            payload["status"] = agent.STATUS_RETRIEVAL_DEGRADED
            payload["degraded"] = True
            payload["degraded_reason"] = retrieval.get("debug", {}).get("degraded_reason")
            payload["message"] = agent.MESSAGE_RETRIEVAL_DEGRADED
        else:
            payload["message"] = agent.MESSAGE_NO_DATA
        if context_lost:
            payload["context_lost"] = True
        return payload

    if all_low:
        payload = {
            "status": agent.STATUS_LOW_RELEVANCE,
            "parse": parse,
            "assumptions": assumptions,
            "low_confidence": True,
            "message": agent.MESSAGE_LOW_RELEVANCE,
            "answer": None,
            "evidence": [
                {"msg_id": c.get("msg_id"), "quote": (c.get("content") or "")[:80]}
                for c in candidates
            ],
        }
        if degraded:
            payload["degraded"] = True
            payload["degraded_reason"] = retrieval.get("debug", {}).get("degraded_reason")
            payload["message"] += "；" + agent.MESSAGE_RETRIEVAL_DEGRADED
        _maybe_debug(payload, retrieval.get("debug") or {})
        if context_lost:
            payload["context_lost"] = True
        return payload

    result = build_answer(
        parse,
        candidates,
        db_path=db_path,
        group_scope=group_scope,
        allowed_msg_ids=allowed_msg_ids,
    )
    answer_status = (result or {}).get("status", "ok")

    if answer_status == "uncertain":
        payload = {
            "status": "uncertain",
            "parse": parse,
            "assumptions": assumptions,
            "message": "现有原文能对上，但关键字段无法核验或存在冲突，暂不给出结论。",
            "answer": result,
            "evidence": (result or {}).get("evidence") or [],
        }
    elif answer_status == "no_result":
        payload = {
            "status": agent.STATUS_RETRIEVAL_DEGRADED if degraded else agent.STATUS_NO_DATA,
            "parse": parse,
            "assumptions": assumptions,
            "message": (
                agent.MESSAGE_RETRIEVAL_DEGRADED if degraded else agent.MESSAGE_NO_DATA
            ),
            "suggestions": list(agent.NO_RESULT_SUGGESTIONS),
            "answer": result,
            "evidence": [],
        }
    else:
        payload = {
            "status": agent.STATUS_RETRIEVAL_DEGRADED if degraded else "answer",
            "parse": parse,
            "assumptions": assumptions,
            "message": ("说明：" + "；".join(assumptions)) if assumptions else "",
            "answer": result,
            "evidence": (result or {}).get("evidence") or [],
        }
        if degraded:
            payload["degraded"] = True
            payload["degraded_reason"] = retrieval.get("debug", {}).get("degraded_reason")
            payload["message"] = (
                payload["message"] + "；" + agent.MESSAGE_RETRIEVAL_DEGRADED
                if payload["message"]
                else agent.MESSAGE_RETRIEVAL_DEGRADED
            )

    _maybe_debug(payload, retrieval.get("debug") or {})
    if context_lost:
        payload["context_lost"] = True
    return payload


def _build_assumptions(parse: dict) -> list[str]:
    """build_answer 里需要把 ambiguous 转成人话假设说明；process 里已经做过一次，
    但 facade 单独暴露 build_answer 时 parse 里可能带 ambiguous 也可能不带。
    这里统一兜底。"""
    amb = parse.get("ambiguous") if isinstance(parse, dict) else None
    return [_ambiguous_assumption(amb)] if amb else []


def _ambiguous_assumption(amb: Optional[dict]) -> str:
    if not amb:
        return "未能确认具体对象，已按全部候选检索"
    field = amb.get("field")
    if field == "time":
        return "未能确认时间范围，已按不限时间检索"
    if field == "category":
        return "未能确认要找的类别，已在活动、办事、文件三类中一起检索"
    return "未能确认具体对象，已按全部候选检索"


def _dict_to_session(session: Optional[dict]) -> Optional[agent.Session]:
    if not session:
        return None
    return agent.Session(
        session_id=session.get("session_id") or "",
        status=session.get("status") or agent.STATUS_PARSED,
        query=session.get("query") or "",
        parse=session.get("parse") or {},
        result_summary=session.get("result_summary"),
        ask_count=int(session.get("ask_count") or 0),
    )


def _infer_session_id(session: Optional[dict]) -> str:
    if session and session.get("session_id"):
        return session["session_id"]
    import uuid
    return uuid.uuid4().hex


def _new_session_from_query(
    session: Optional[dict], query: str, parse: dict, ask_count: int
) -> agent.Session:
    return agent.Session(
        session_id=_infer_session_id(session),
        status=agent.STATUS_PARSED,
        query=query,
        parse=parse,
        ask_count=ask_count,
    )


def _save(
    store: SessionStore,
    session_obj: Optional[agent.Session],
    *,
    status: str,
    parse: dict,
    ask_count: int,
    result_summary: Optional[str] = None,
) -> None:
    """把 facade 内部变更写回 store；无 session_id 时跳过。"""
    if session_obj is None:
        return
    session_obj.status = status
    session_obj.parse = parse
    session_obj.ask_count = ask_count
    if result_summary is not None:
        session_obj.result_summary = result_summary
    store.save(session_obj)

