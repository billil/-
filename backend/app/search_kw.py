"""关键词检索模块（FTS5 + jieba 预分词）。

流程：query -> jieba.lcut 分词 -> 过滤停用词/单字符虚词 -> 词间 OR 连接 ->
FTS5 MATCH -> 按 bm25 升序返回候选。
"""

import re
from typing import Optional

import jieba

from .db import get_conn

# 停用词表：常见中文虚词与标点，过滤后避免无意义词干扰
_STOPWORDS = {
    "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一",
    "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看",
    "好", "自己", "这", "那", "啊", "呢", "吧", "吗", "哦", "嗯", "哈", "呀",
    "把", "被", "让", "给", "对", "为", "以", "及", "或", "而", "但", "如果",
    "因为", "所以", "虽然", "但是", "然后", "还是", "或者", "的话", "一下",
    "这个", "那个", "什么", "怎么", "为什么", "哪里", "哪个", "谁", "几",
    "能", "可以", "已经", "正在", "将", "会", "要", "应该", "需要",
}

# FTS5 MATCH 表达式中的特殊字符
_FTS_SPECIAL = re.compile(r'[+\-*/(){}\[\]^"~:]')


def _quote_token(token: str) -> str:
    """将分词结果包装为 FTS5 引号字符串，内部双引号转义。"""
    escaped = token.replace('"', '""')
    return f'"{escaped}"'


def _tokenize(query: str) -> list[str]:
    """jieba 分词，过滤停用词与单字符虚词。"""
    tokens = jieba.lcut(query)
    result = []
    for t in tokens:
        t = t.strip()
        if not t:
            continue
        # 过滤停用词
        if t in _STOPWORDS:
            continue
        # 过滤纯标点/空白
        if _FTS_SPECIAL.fullmatch(t) or not re.search(r"[\w]", t, re.UNICODE):
            continue
        # 单字符且为常见虚词（已被停用词覆盖，此处兜底）
        if len(t) == 1 and t in _STOPWORDS:
            continue
        result.append(t)
    return result


def kw_search(query: str, limit: int = 50, db_path: Optional[str] = None) -> list[dict]:
    """关键词检索，返回 [{msg_id, content, sender, send_time, rank}]。

    - rank 为 bm25 得分，越小越相关，结果已按 rank 升序排列。
    - 分词为空时返回空列表，不抛异常。
    - 所有返回内容均直接来自 messages 表，可溯源到 msg_id + 原文。
    """
    tokens = _tokenize(query)
    if not tokens:
        return []

    # 词间用 OR 连接，避免整句匹配不到；每个词加引号防止特殊字符破坏语法
    match_expr = " OR ".join(_quote_token(t) for t in tokens)

    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT m.msg_id, m.content, m.sender, m.send_time, bm25(messages_fts) AS rank
            FROM messages_fts
            JOIN messages m ON messages_fts.rowid = m.rowid
            WHERE messages_fts MATCH ?
            ORDER BY rank ASC
            LIMIT ?
            """,
            (match_expr, limit),
        )
        rows = cur.fetchall()
    except Exception:
        # FTS 语法异常时返回空列表，保证接口稳定
        conn.close()
        return []

    conn.close()

    return [
        {
            "msg_id": row[0],
            "content": row[1],
            "sender": row[2],
            "send_time": row[3],
            "rank": row[4],
        }
        for row in rows
    ]
