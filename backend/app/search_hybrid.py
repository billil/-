"""混合检索：关键词 + 语义，RRF（Reciprocal Rank Fusion）融合。

RRF 得分 = Σ 1 / (k + rank)，rank 从 1 开始，k 默认 60。
每个候选都带 sources 标注（kw / vec），满足"可解释"要求。

U3：解析条件真正参与检索
- 硬过滤：category（leads.category）/ file_hint（title、file_hint 模糊匹配）/
  time_range（按 time_field 选择 messages.send_time 或 leads.event_time）/ group_id
- 软加权：soft_boost 命中的候选 RRF 分数 ×1.15，并标记 soft_matched=true；
  软加权只改分数与标记，不改变硬过滤后的结果集
- 返回 {"results": [...], "debug": {...}}，debug 供排查"条件是否真的生效"
"""

import logging
import sqlite3
from typing import Optional

from .config import RRF_K, TOP_K_DEFAULT
from .db import get_conn
from .search import (
    LOW_RELEVANCE_TOP,
    MIN_SCORE_KW,
    MIN_SCORE_VEC,
    normalize_bm25_scores,
    split_by_threshold,
)
from .search_kw import kw_search
from .search_vec import vec_search, vector_status

logger = logging.getLogger(__name__)

# 软加权系数：命中模糊条件的候选在 RRF 分数上乘该系数
SOFT_BOOST_FACTOR = 1.15

# 允许的时间字段：消息发送时间 / 事件发生时间
VALID_TIME_FIELDS = ("event_time", "send_time")
# 允许的线索类别
VALID_CATEGORIES = ("event", "task", "file")


def _normalize_time(value: Optional[str], is_end: bool) -> Optional[str]:
    """把 YYYY-MM-DD 或 YYYY-MM-DD HH:MM 规整为可比较的完整时间串。"""
    if not value:
        return None
    text = str(value).strip().replace("T", " ")
    if len(text) == 10:  # YYYY-MM-DD
        return text + (" 23:59:59" if is_end else " 00:00:00")
    if len(text) == 16:  # YYYY-MM-DD HH:MM
        return text + (":59" if is_end else ":00")
    return text


def _in_range(value: Optional[str], start: Optional[str], end: Optional[str]) -> bool:
    """判断时间点是否落在 [start, end] 闭区间内；无时间过滤时一律保留。"""
    if not start and not end:
        return True
    if not value:
        return False
    t = _normalize_time(value, is_end=False)
    if t is None:
        return False
    if start and t < start:
        return False
    if end and t > end:
        return False
    return True


def _load_meta(msg_ids: list[str], db_path: Optional[str]) -> dict[str, dict]:
    """批量读取候选消息及其线索（category / title / file_hint / event_time）。

    数据库异常只影响过滤判定，不向调用方抛错。
    """
    meta: dict[str, dict] = {}
    if not msg_ids:
        return meta

    placeholders = ",".join("?" for _ in msg_ids)
    try:
        conn = get_conn(db_path)
        try:
            for row in conn.execute(
                f"SELECT msg_id, group_id, send_time FROM messages "
                f"WHERE msg_id IN ({placeholders})",
                msg_ids,
            ):
                meta[row[0]] = {"group_id": row[1], "send_time": row[2], "leads": []}
            for row in conn.execute(
                f"SELECT msg_id, category, title, file_hint, event_time FROM leads "
                f"WHERE msg_id IN ({placeholders})",
                msg_ids,
            ):
                item = meta.setdefault(
                    row[0], {"group_id": None, "send_time": None, "leads": []}
                )
                item["leads"].append(
                    {
                        "category": row[1],
                        "title": row[2],
                        "file_hint": row[3],
                        "event_time": row[4],
                    }
                )
        finally:
            conn.close()
    except sqlite3.Error:
        # 表不存在或查询失败时按"无元信息"处理，避免检索直接报错
        return meta
    return meta


def _match_category(entry: dict, category: str) -> bool:
    return any(lead.get("category") == category for lead in entry.get("leads") or [])


def _match_file_hint(entry: dict, hint: str) -> bool:
    """file_hint 在 title / file_hint 字段做不区分大小写的模糊匹配。"""
    needle = str(hint).lower()
    for lead in entry.get("leads") or []:
        haystacks = (lead.get("title") or "", lead.get("file_hint") or "")
        if any(needle in str(text).lower() for text in haystacks):
            return True
    return False


def _match_time(
    entry: dict, start: Optional[str], end: Optional[str], time_field: str
) -> bool:
    """按 time_field 选择时间字段做区间过滤。"""
    if not start and not end:
        return True

    if time_field == "event_time":
        values = [
            lead.get("event_time")
            for lead in entry.get("leads") or []
            if lead.get("event_time")
        ]
        # 尚未抽取线索（无 event_time）时退回消息发送时间，避免空召回
        if not values:
            values = [entry.get("send_time")]
    else:
        values = [entry.get("send_time")]

    return any(_in_range(value, start, end) for value in values)


def _hard_match(
    entry: dict,
    filters: dict,
    start: Optional[str],
    end: Optional[str],
    time_field: str,
) -> bool:
    """硬过滤：任一条件不满足即剔除。"""
    category = filters.get("category")
    if category and not _match_category(entry, category):
        return False

    file_hint = filters.get("file_hint")
    if file_hint and not _match_file_hint(entry, file_hint):
        return False

    group_id = filters.get("group_id")
    if group_id and entry.get("group_id") != group_id:
        return False

    return _match_time(entry, start, end, time_field)


def _soft_matched(entry: dict, soft_boost: dict) -> bool:
    """软加权命中判定：category / file_hint 任一命中即算。"""
    category = soft_boost.get("category")
    file_hint = soft_boost.get("file_hint")
    if not category and not file_hint:
        return False
    if category and not _match_category(entry, category):
        return False
    if file_hint and not _match_file_hint(entry, file_hint):
        return False
    return True


def _applied_filters(
    filters: dict,
    start: Optional[str],
    end: Optional[str],
    time_field: str,
) -> list[str]:
    """把生效的硬过滤转成可读清单，放在 debug 里供排查。"""
    applied: list[str] = []
    if filters.get("category"):
        applied.append(f"category:{filters['category']}")
    if filters.get("file_hint"):
        applied.append(f"file_hint:{filters['file_hint']}")
    if start or end:
        applied.append(f"time_range:{start or ''}~{end or ''}")
        applied.append(f"time_field:{time_field}")
    if filters.get("group_id"):
        applied.append(f"group_id:{filters['group_id']}")
    return applied


def hybrid_search(
    query: str,
    limit: int = TOP_K_DEFAULT,
    filters: Optional[dict] = None,
    db_path: Optional[str] = None,
) -> dict:
    """混合检索：关键词 + 语义 RRF 融合，再做阈值过滤、硬过滤与软加权。

    返回 {"results": [...], "debug": {...}}
    - results 项：{msg_id, content, sender, send_time, score, sources,
                  soft_matched, low_confidence, group_id, category, title,
                  file_hint, event_time}
    - debug：{applied_filters, soft_boost, result_count_before_filter,
              result_count_after_filter, outcome, degraded, degraded_reason}
    - outcome：ok / no_data / low_relevance / retrieval_degraded
    """
    filters = filters or {}
    soft_boost = filters.get("soft_boost") or {}
    soft_boost = {
        "category": soft_boost.get("category"),
        "file_hint": soft_boost.get("file_hint"),
    }
    # 非法类别一律视为"未指定"，避免条件写错导致空结果
    if filters.get("category") not in VALID_CATEGORIES:
        filters = {**filters, "category": None}
    if soft_boost["category"] not in VALID_CATEGORIES:
        soft_boost["category"] = None

    time_field = filters.get("time_field") or "event_time"
    if time_field not in VALID_TIME_FIELDS:
        time_field = "event_time"

    time_range = filters.get("time_range") or {}
    start = _normalize_time(time_range.get("start"), is_end=False)
    end = _normalize_time(time_range.get("end"), is_end=True)

    pool = max(limit * 2, limit)

    # 关键词路：bm25 归一化到 [0,1] 后按 MIN_SCORE_KW 过滤
    kw_hits = []
    if query:
        kw_hits = normalize_bm25_scores(
            kw_search(query, limit=pool, db_path=db_path)
        )
    kw_pass, _ = split_by_threshold(kw_hits, "relevance", MIN_SCORE_KW)

    # 向量路：余弦相似度按 MIN_SCORE_VEC 过滤；异常/降级时只走关键词
    vec_hits: list[dict] = []
    vec_degraded = False
    if query:
        try:
            vec_hits = vec_search(query, top_k=pool, db_path=db_path)
        except Exception as exc:  # noqa: BLE001 - 检索降级，不影响接口
            logger.warning("向量检索异常，本次降级为仅关键词：%s", exc)
            vec_hits = []
            vec_degraded = True
        if vector_status().get("last_failed"):
            vec_degraded = True
    vec_pass, _ = split_by_threshold(vec_hits, "score", MIN_SCORE_VEC)

    # 两路都有召回但全部低于阈值 → 低相关，返回最接近的若干条
    low_relevance = bool(kw_hits or vec_hits) and not kw_pass and not vec_pass

    retrieved: list[dict] = []
    if low_relevance:
        best: dict[str, dict] = {}

        def _add_low(hit: dict, score_key: str, source: str) -> None:
            msg_id = hit.get("msg_id")
            if not msg_id:
                return
            try:
                score = float(hit.get(score_key) or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            item = best.get(msg_id)
            if item is None:
                best[msg_id] = {
                    "msg_id": msg_id,
                    "content": hit.get("content"),
                    "sender": hit.get("sender"),
                    "send_time": hit.get("send_time"),
                    "score": score,
                    "sources": [source],
                }
            else:
                item["score"] = max(item["score"], score)
                if source not in item["sources"]:
                    item["sources"].append(source)

        for hit in kw_hits:
            _add_low(hit, "relevance", "kw")
        for hit in vec_hits:
            _add_low(hit, "score", "vec")
        retrieved = sorted(
            best.values(), key=lambda x: (-x["score"], x["msg_id"])
        )[:LOW_RELEVANCE_TOP]
    else:
        # RRF 融合：只用达到阈值的候选，低于阈值的尾部不再参与
        fused: dict[str, dict] = {}

        def _add(hit: dict, rank: int, source: str) -> None:
            msg_id = hit.get("msg_id")
            if not msg_id:
                return
            item = fused.get(msg_id)
            if item is None:
                item = {
                    "msg_id": msg_id,
                    "content": hit.get("content"),
                    "sender": hit.get("sender"),
                    "send_time": hit.get("send_time"),
                    "score": 0.0,
                    "sources": [],
                }
                fused[msg_id] = item
            item["score"] += 1.0 / (RRF_K + rank)
            if source not in item["sources"]:
                item["sources"].append(source)

        for rank, hit in enumerate(kw_pass, start=1):
            _add(hit, rank, "kw")
        for rank, hit in enumerate(vec_pass, start=1):
            _add(hit, rank, "vec")
        retrieved = list(fused.values())

    result_count_before = len(retrieved)
    meta = _load_meta([item["msg_id"] for item in retrieved], db_path)

    candidates: list[dict] = []
    for item in retrieved:
        entry = meta.get(item["msg_id"]) or {}
        if not _hard_match(entry, filters, start, end, time_field):
            continue
        item["soft_matched"] = _soft_matched(entry, soft_boost)
        if item["soft_matched"]:
            item["score"] *= SOFT_BOOST_FACTOR
        # 低相关结果显式标记，前端/答案侧不得当作确定匹配
        item["low_confidence"] = low_relevance
        # 附带线索元信息，方便排查条件命中情况（内容仍以 messages 原文为准）
        leads = entry.get("leads") or []
        primary = leads[0] if leads else {}
        item["group_id"] = entry.get("group_id")
        item["category"] = primary.get("category")
        item["title"] = primary.get("title")
        item["file_hint"] = primary.get("file_hint")
        item["event_time"] = primary.get("event_time")
        candidates.append(item)

    result_count_after = len(candidates)

    # 确定性排序：分数降序，同分按 msg_id 升序
    ranked = sorted(candidates, key=lambda x: (-x["score"], x["msg_id"]))

    if not candidates:
        outcome = "no_data"
    elif low_relevance:
        outcome = "low_relevance"
    elif vec_degraded:
        outcome = "retrieval_degraded"
    else:
        outcome = "ok"

    return {
        "results": ranked[:limit],
        "debug": {
            "applied_filters": _applied_filters(filters, start, end, time_field),
            "soft_boost": soft_boost,
            "result_count_before_filter": result_count_before,
            "result_count_after_filter": result_count_after,
            "outcome": outcome,
            "degraded": vec_degraded,
            "degraded_reason": "vector_unavailable" if vec_degraded else None,
        },
    }
