"""向量语义检索。

流程：query 向量化 -> 全库向量矩阵乘法（余弦相似度）-> 取 top_k。
向量在入库时已 L2 归一化，故点积即余弦相似度，分数 ∈ [-1, 1]。

U5：向量不可用时降级
- 单次向量化超时 3 秒；
- 失败只影响本次检索（返回空列表），连续失败 MAX_CONSECUTIVE_FAILURES 次后
  进程内标记降级，后续请求直接跳过向量化，避免每个请求都卡在超时上；
- 启动探针 probe_vector() 失败只记日志，不阻断启动。
"""

import logging
from typing import Optional

import numpy as np

from .config import VEC_TOP_K_DEFAULT
from .db import get_conn
from .embed import EmbeddingClient
from .search import MAX_CONSECUTIVE_FAILURES, VECTOR_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

# 向量状态：连续失败次数、是否已降级、最近一次调用是否失败
_STATE = {"failures": 0, "degraded": False, "last_failed": False}


def reset_vector_state() -> None:
    """重置向量状态（测试隔离 / 服务恢复后手动重置）。"""
    _STATE["failures"] = 0
    _STATE["degraded"] = False
    _STATE["last_failed"] = False


def vector_status() -> dict:
    """返回当前向量可用性状态，供检索侧决定是否标记 retrieval_degraded。"""
    return {
        "degraded": _STATE["degraded"],
        "last_failed": _STATE["last_failed"],
        "consecutive_failures": _STATE["failures"],
    }


def _record_failure(exc: Exception) -> None:
    """记录一次向量化失败；连续失败达到阈值后标记降级。"""
    _STATE["last_failed"] = True
    _STATE["failures"] += 1
    if _STATE["failures"] >= MAX_CONSECUTIVE_FAILURES and not _STATE["degraded"]:
        _STATE["degraded"] = True
        logger.error(
            "向量检索连续失败 %d 次，已降级为仅关键词检索：%s",
            _STATE["failures"],
            exc,
        )
    else:
        logger.warning("向量化失败（本次仅用关键词检索）：%s", exc)


def _record_success() -> None:
    _STATE["failures"] = 0
    _STATE["last_failed"] = False


def _embed_query(query: str) -> np.ndarray:
    """带超时的 query 向量化；超时或异常向上抛出，由调用方降级。

    向量化统一走 embed 模块的单线程池（模型加载与推理固定同一线程，
    避免 Python 3.15 + torch 跨线程 forward 崩溃），这里只负责等待超时。
    """
    client = EmbeddingClient()
    future = client.submit_embed_texts([query])
    return future.result(timeout=VECTOR_TIMEOUT_SECONDS)[0]


def probe_vector() -> bool:
    """启动探针：尝试一次向量化，失败只记日志不阻断启动。"""
    try:
        _embed_query("群忆向量探针")
        _record_success()
        logger.info("向量模型探针通过")
        return True
    except Exception as exc:  # noqa: BLE001 - 探针失败不应影响启动
        logger.warning("向量模型探针失败（不阻断启动，检索将降级）：%s", exc)
        return False


def vec_search(query: str, top_k: int = VEC_TOP_K_DEFAULT, db_path: Optional[str] = None) -> list[dict]:
    """语义检索，返回 [{msg_id, content, sender, send_time, score}]，按 score 降序。

    - score 为余弦相似度，范围 [-1, 1]。
    - 所有内容直接来自 messages 表，可溯源到 msg_id + 原文。
    - 库中无向量时返回空列表。
    - 向量模型不可用（超时/异常/已降级）时返回空列表，并通过 vector_status() 暴露状态。
    """
    _STATE["last_failed"] = False

    # 已降级：直接跳过向量化，避免每个请求都卡在超时上
    if _STATE["degraded"]:
        _STATE["last_failed"] = True
        logger.warning("向量检索已降级，本次跳过向量化")
        return []

    try:
        q_emb = _embed_query(query)
    except Exception as exc:  # noqa: BLE001 - 检索降级，不向接口抛错
        _record_failure(exc)
        return []
    _record_success()

    dim = q_emb.shape[0]

    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT msg_id, content, sender, send_time, embedding "
        "FROM messages WHERE embedding IS NOT NULL"
    ).fetchall()
    conn.close()

    if not rows:
        return []

    n = len(rows)
    ids = [r[0] for r in rows]
    contents = [r[1] for r in rows]
    senders = [r[2] for r in rows]
    send_times = [r[3] for r in rows]

    # 从 BLOB 恢复向量矩阵
    matrix = np.zeros((n, dim), dtype=np.float32)
    for i, r in enumerate(rows):
        vec = np.frombuffer(r[4], dtype=np.float32)
        if vec.shape[0] != dim:
            # 维度不一致（模型变更），跳过该行
            continue
        matrix[i] = vec

    # 余弦相似度（已归一化，点积即余弦）
    scores = matrix @ q_emb  # (n,)

    # 取 top_k 并按分数降序
    k = min(top_k, n)
    if k < n:
        # argpartition 取 top_k 下标，再按分数降序排序
        top_idx = np.argpartition(scores, -k)[-k:]
        top_idx = top_idx[np.argsort(scores[top_idx])[::-1]]
    else:
        top_idx = np.argsort(scores)[::-1]

    results = []
    for i in top_idx:
        results.append(
            {
                "msg_id": ids[i],
                "content": contents[i],
                "sender": senders[i],
                "send_time": send_times[i],
                "score": float(scores[i]),
            }
        )
    return results
