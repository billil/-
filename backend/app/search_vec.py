"""向量语义检索。

流程：query 向量化 -> 全库向量矩阵乘法（余弦相似度）-> 取 top_k。
向量在入库时已 L2 归一化，故点积即余弦相似度，分数 ∈ [-1, 1]。
"""

from typing import Optional

import numpy as np

from .config import VEC_TOP_K_DEFAULT
from .db import get_conn
from .embed import EmbeddingClient


def vec_search(query: str, top_k: int = VEC_TOP_K_DEFAULT, db_path: Optional[str] = None) -> list[dict]:
    """语义检索，返回 [{msg_id, content, sender, send_time, score}]，按 score 降序。

    - score 为余弦相似度，范围 [-1, 1]。
    - 所有内容直接来自 messages 表，可溯源到 msg_id + 原文。
    - 库中无向量时返回空列表。
    """
    client = EmbeddingClient()
    q_emb = client.embed_texts([query])[0]  # (dim,)
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
