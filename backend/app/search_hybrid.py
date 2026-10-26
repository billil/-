"""混合检索：关键词 + 语义，RRF（Reciprocal Rank Fusion）融合。

RRF 得分 = Σ 1 / (k + rank)，rank 从 1 开始，k 默认 60。
每个候选都带 sources 标注（kw / vec），满足"可解释"要求。
时间过滤作用在 messages.send_time 上（start / end 均为闭区间）。
"""

from typing import Optional

from .config import RRF_K, TOP_K_DEFAULT
from .search_kw import kw_search
from .search_vec import vec_search


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


def _in_time_range(send_time: Optional[str], start: Optional[str], end: Optional[str]) -> bool:
    """判断消息是否落在 [start, end] 区间内；无时间过滤时一律保留。"""
    if not start and not end:
        return True
    if not send_time:
        return False
    t = str(send_time).strip().replace("T", " ")
    if start and t < start:
        return False
    if end and t > end:
        return False
    return True


def hybrid_search(
    query: str,
    keywords: Optional[list[str]] = None,
    time_start: Optional[str] = None,
    time_end: Optional[str] = None,
    limit: int = TOP_K_DEFAULT,
    db_path: Optional[str] = None,
) -> list[dict]:
    """混合检索，返回 [{msg_id, content, sender, send_time, score, sources}]，按 score 降序。

    - keywords 非空时用于关键词检索，否则用 query 原文。
    - sources 取值："kw"（关键词命中）、"vec"（语义命中），两者都命中则为 ["kw","vec"]。
    """
    start = _normalize_time(time_start, is_end=False)
    end = _normalize_time(time_end, is_end=True)

    kw_query = " ".join(keywords) if keywords else query
    pool = max(limit * 2, limit)

    kw_hits = kw_search(kw_query, limit=pool, db_path=db_path) if kw_query else []
    vec_hits = vec_search(query, top_k=pool, db_path=db_path) if query else []

    fused: dict[str, dict] = {}

    def _add(hit: dict, rank: int, source: str) -> None:
        msg_id = hit.get("msg_id")
        if not msg_id:
            return
        if not _in_time_range(hit.get("send_time"), start, end):
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

    for rank, hit in enumerate(kw_hits, start=1):
        _add(hit, rank, "kw")
    for rank, hit in enumerate(vec_hits, start=1):
        _add(hit, rank, "vec")

    results = sorted(fused.values(), key=lambda x: x["score"], reverse=True)
    return results[:limit]
