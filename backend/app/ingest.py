"""群聊消息导入模块。

读取 QQ/微信群聊导出的 JSON 文件，幂等写入 messages 表（msg_id 冲突则跳过），
并在导入后批量生成向量写入 embedding 列。
JSON 格式：[{msg_id, group_id, sender, sender_id, send_time, content}, ...]
"""

import json
from typing import Optional

import jieba

from .db import get_conn


def _segment(content: Optional[str]) -> str:
    """对消息正文做 jieba 搜索引擎模式分词，返回空格分隔的 token 串，供 FTS5 建索引。

    使用 cut_for_search 而非默认 lcut：默认模式会把"吃火锅"切成一个词，
    导致查询"火锅"时无法命中；搜索引擎模式会额外产出"火锅"子词，保证子串可召回。
    """
    if not content:
        return ""
    tokens = [t.strip() for t in jieba.lcut_for_search(content) if t.strip()]
    return " ".join(tokens)


def _fill_null_embeddings(conn) -> int:
    """为库中 embedding 为空且有 content 的行批量生成并写入向量，返回处理行数。"""
    import numpy as np
    from .embed import EmbeddingClient

    rows = conn.execute(
        "SELECT msg_id, content FROM messages WHERE embedding IS NULL AND content IS NOT NULL"
    ).fetchall()
    if not rows:
        return 0

    client = EmbeddingClient()
    contents = [r[1] for r in rows]
    embeddings = client.embed_texts(contents)

    cur = conn.cursor()
    for (msg_id, _), emb in zip(rows, embeddings):
        cur.execute(
            "UPDATE messages SET embedding = ? WHERE msg_id = ?",
            (np.asarray(emb, dtype=np.float32).tobytes(), msg_id),
        )
    conn.commit()
    return len(rows)


def import_messages(
    json_path: str,
    db_path: Optional[str] = None,
    generate_embeddings: bool = True,
) -> dict:
    """导入群聊消息 JSON，返回 {inserted, skipped, total, embedded}。

    幂等：msg_id 已存在则跳过（INSERT OR IGNORE），FTS 触发器仅在真正插入时同步。
    generate_embeddings=True 时，导入后会为所有 embedding 为空的行批量生成向量。
    """
    with open(json_path, "r", encoding="utf-8") as f:
        messages = json.load(f)

    if not isinstance(messages, list):
        raise ValueError("JSON 顶层必须是消息列表")

    conn = get_conn(db_path)
    cur = conn.cursor()

    inserted = 0
    skipped = 0

    for msg in messages:
        content = msg.get("content")
        raw_json = json.dumps(msg, ensure_ascii=False)
        content_terms = _segment(content)
        try:
            cur.execute(
                """
                INSERT OR IGNORE INTO messages
                    (msg_id, group_id, sender, sender_id, send_time, content, content_terms, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msg.get("msg_id"),
                    msg.get("group_id"),
                    msg.get("sender"),
                    msg.get("sender_id"),
                    msg.get("send_time"),
                    content,
                    content_terms,
                    raw_json,
                ),
            )
            if cur.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
        except Exception:
            # 单条异常不影响整体导入，记为跳过
            skipped += 1

    conn.commit()

    embedded = 0
    if generate_embeddings:
        embedded = _fill_null_embeddings(conn)

    conn.close()

    return {"inserted": inserted, "skipped": skipped, "total": len(messages), "embedded": embedded}
