"""向量语义检索测试。

用 20 条小样本验证语义召回能力，模型在 session 级别加载一次后复用。
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.db import init_db
from app.ingest import import_messages
from app.search_vec import vec_search

# 20 条小样本消息，包含目标语义对与若干干扰项
SAMPLE_MESSAGES = [
    {"msg_id": "m01", "sender": "小明", "send_time": "2026-10-01 12:00:00",
     "content": "周六一起吃饭啊，都来啊"},
    {"msg_id": "m02", "sender": "小红", "send_time": "2026-10-01 12:01:00",
     "content": "记得把作业私发我一下"},
    {"msg_id": "m03", "sender": "小刚", "send_time": "2026-10-01 12:02:00",
     "content": "明天天气好像要下雨"},
    {"msg_id": "m04", "sender": "小丽", "send_time": "2026-10-01 12:03:00",
     "content": "有没有人捡到我的校园卡"},
    {"msg_id": "m05", "sender": "小强", "send_time": "2026-10-01 12:04:00",
     "content": "晚上的选修课大家去不去"},
    {"msg_id": "m06", "sender": "小美", "send_time": "2026-10-01 12:05:00",
     "content": "图书馆三楼有位置吗"},
    {"msg_id": "m07", "sender": "小华", "send_time": "2026-10-01 12:06:00",
     "content": "谁有充电宝借我用一下"},
    {"msg_id": "m08", "sender": "小军", "send_time": "2026-10-01 12:07:00",
     "content": "这道数学题怎么做"},
    {"msg_id": "m09", "sender": "小芳", "send_time": "2026-10-01 12:08:00",
     "content": "周末一起去看电影吧"},
    {"msg_id": "m10", "sender": "小伟", "send_time": "2026-10-01 12:09:00",
     "content": "食堂今天有什么好吃的"},
    {"msg_id": "m11", "sender": "小燕", "send_time": "2026-10-01 12:10:00",
     "content": "实验报告周五要交"},
    {"msg_id": "m12", "sender": "小峰", "send_time": "2026-10-01 12:11:00",
     "content": "有人一起去打球吗"},
    {"msg_id": "m13", "sender": "小婷", "send_time": "2026-10-01 12:12:00",
     "content": "快递到了菜鸟驿站"},
    {"msg_id": "m14", "sender": "小磊", "send_time": "2026-10-01 12:13:00",
     "content": "明天早八别忘了"},
    {"msg_id": "m15", "sender": "小琳", "send_time": "2026-10-01 12:14:00",
     "content": "求推荐一款好用的耳机"},
    {"msg_id": "m16", "sender": "小航", "send_time": "2026-10-01 12:15:00",
     "content": "校医院晚上开门吗"},
    {"msg_id": "m17", "sender": "小雪", "send_time": "2026-10-01 12:16:00",
     "content": "谁有Python的学习资料"},
    {"msg_id": "m18", "sender": "小博", "send_time": "2026-10-01 12:17:00",
     "content": "聚餐改到周日晚上了"},
    {"msg_id": "m19", "sender": "小悦", "send_time": "2026-10-01 12:18:00",
     "content": "论文截止日期是下周一"},
    {"msg_id": "m20", "sender": "小凯", "send_time": "2026-10-01 12:19:00",
     "content": "有没有人拼单买奶茶"},
]


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    """session 级数据库：导入 20 条消息并生成向量。"""
    db_dir = tmp_path_factory.mktemp("vec_db")
    db = str(db_dir / "memory.db")
    init_db(db)

    json_path = str(db_dir / "messages.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(SAMPLE_MESSAGES, f, ensure_ascii=False)

    result = import_messages(json_path, db_path=db, generate_embeddings=True)
    assert result["embedded"] == 20, f"应生成 20 条向量，实际 {result['embedded']}"
    return db


def test_semantic_recall_dinner(db_path):
    """查询"聚餐"能召回"周六一起吃饭啊，都来啊"（字面不含"聚餐"）。"""
    results = vec_search("聚餐", db_path=db_path)
    contents = [r["content"] for r in results]
    assert "周六一起吃饭啊，都来啊" in contents


def test_semantic_recall_homework(db_path):
    """查询"交作业"能召回"记得把作业私发我一下"。"""
    results = vec_search("交作业", db_path=db_path)
    contents = [r["content"] for r in results]
    assert "记得把作业私发我一下" in contents


def test_score_range_0_1(db_path):
    """所有返回 score 都在 0~1 之间（归一化向量余弦相似度）。"""
    results = vec_search("聚餐", db_path=db_path)
    assert len(results) > 0
    for r in results:
        assert 0.0 <= r["score"] <= 1.0, f"score={r['score']} 超出 [0,1]"


def test_top_k_limit(db_path):
    """返回条数不超过 top_k。"""
    results = vec_search("聚餐", top_k=3, db_path=db_path)
    assert len(results) <= 3


def test_score_descending(db_path):
    """结果按 score 降序排列。"""
    results = vec_search("聚餐", db_path=db_path)
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)
