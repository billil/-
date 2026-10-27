"""检索相关性与降级配置。

集中管理相关性阈值与向量可用性参数，避免阈值散落在业务代码里。

- MIN_SCORE      : 相关性阈值初值 0.35（关键词路用归一化 bm25，向量路用余弦相似度）
- MIN_SCORE_KW   : 关键词路阈值，可单独覆盖
- MIN_SCORE_VEC  : 向量路阈值，可单独覆盖
- 低于阈值且处于候选尾部的结果剔除；若整条路全部低于阈值，由 hybrid 侧保留最接近的
  若干条并标记 low_confidence，避免结果全空。
"""

import os

# 相关性阈值（初值 0.35，可按校准脚本结果调整）
MIN_SCORE = float(os.environ.get("MIN_SCORE", "0.35"))
MIN_SCORE_KW = float(os.environ.get("MIN_SCORE_KW", str(MIN_SCORE)))
MIN_SCORE_VEC = float(os.environ.get("MIN_SCORE_VEC", str(MIN_SCORE)))

# low_relevance 状态下最多返回的候选条数
LOW_RELEVANCE_TOP = int(os.environ.get("LOW_RELEVANCE_TOP", "3"))

# 向量模型单次向量化超时（秒）与连续失败降级阈值
VECTOR_TIMEOUT_SECONDS = float(os.environ.get("VECTOR_TIMEOUT_SECONDS", "3"))
MAX_CONSECUTIVE_FAILURES = int(os.environ.get("MAX_CONSECUTIVE_FAILURES", "3"))


def normalize_bm25_scores(hits: list[dict]) -> list[dict]:
    """把 bm25 rank 归一化到 [0, 1] 并写入 relevance 字段。

    FTS5 的 bm25 越小越相关。这里按本次结果集做 min-max 归一化：
    最相关 = 1.0，最不相关 = 0.0；只有一个候选或分数全相同时统一给 1.0。
    """
    if not hits:
        return []
    scores = []
    for hit in hits:
        try:
            scores.append(float(hit.get("rank") or 0.0))
        except (TypeError, ValueError):
            scores.append(0.0)
    lowest, highest = min(scores), max(scores)

    normalized = []
    for hit, score in zip(hits, scores):
        if highest == lowest:
            relevance = 1.0
        else:
            relevance = (highest - score) / (highest - lowest)
        item = dict(hit)
        item["relevance"] = max(0.0, min(1.0, relevance))
        normalized.append(item)
    return normalized


def split_by_threshold(
    hits: list[dict], score_key: str, threshold: float
) -> tuple[list[dict], int]:
    """按阈值拆分候选，返回 (达到阈值的候选, 低于阈值的条数)。

    边界规则：分数正好等于阈值 → 保留（>=）。
    """
    if not hits:
        return [], 0
    passed: list[dict] = []
    below = 0
    for hit in hits:
        try:
            score = float(hit.get(score_key) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        if score >= threshold:
            passed.append(hit)
        else:
            below += 1
    return passed, below
