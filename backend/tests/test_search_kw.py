"""关键词检索与导入的测试用例。

覆盖：单关键词命中、多词并集、无结果空列表、幂等导入、1万条性能、
rank 字段与排序、FTS 触发器同步。
"""

import json
import os
import timeit

import pytest

# 确保导入 backend 包
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db import init_db, get_conn
from app.ingest import import_messages
from app.search_kw import kw_search


@pytest.fixture
def db_path(tmp_path):
    """每个用例独立的临时数据库。"""
    path = str(tmp_path / "memory.db")
    init_db(path)
    return path


def _write_json(tmp_path, name, messages):
    p = tmp_path / name
    p.write_text(json.dumps(messages, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_single_keyword_match(db_path, tmp_path):
    """单关键词"火锅"能命中含"火锅"的消息。"""
    msgs = [
        {"msg_id": "1", "group_id": "g1", "sender": "小明", "sender_id": "u1",
         "send_time": "2026-10-01 12:00:00", "content": "今晚去吃火锅吧"},
        {"msg_id": "2", "group_id": "g1", "sender": "小红", "sender_id": "u2",
         "send_time": "2026-10-01 12:01:00", "content": "好啊，我知道一家不错的店"},
    ]
    import_messages(_write_json(tmp_path, "m.json", msgs), db_path)

    results = kw_search("火锅", db_path=db_path)
    assert len(results) == 1
    assert results[0]["msg_id"] == "1"
    assert "火锅" in results[0]["content"]
    assert results[0]["sender"] == "小明"


def test_multi_keyword_union(db_path, tmp_path):
    """多词"上周 火锅"命中任一词的并集。"""
    msgs = [
        {"msg_id": "1", "sender": "a", "send_time": "2026-10-01 12:00:00",
         "content": "上周我们去了公园"},
        {"msg_id": "2", "sender": "b", "send_time": "2026-10-01 12:01:00",
         "content": "火锅真好吃"},
        {"msg_id": "3", "sender": "c", "send_time": "2026-10-01 12:02:00",
         "content": "明天要交作业"},
    ]
    import_messages(_write_json(tmp_path, "m.json", msgs), db_path)

    results = kw_search("上周 火锅", db_path=db_path)
    ids = {r["msg_id"] for r in results}
    assert "1" in ids
    assert "2" in ids
    assert "3" not in ids


def test_no_result_returns_empty(db_path, tmp_path):
    """无结果查询返回空列表不报错。"""
    msgs = [
        {"msg_id": "1", "sender": "a", "send_time": "2026-10-01 12:00:00",
         "content": "今天天气不错"},
    ]
    import_messages(_write_json(tmp_path, "m.json", msgs), db_path)

    results = kw_search("不存在的词xyz", db_path=db_path)
    assert results == []


def test_idempotent_import(db_path, tmp_path):
    """重复导入同文件，messages 行数不变（幂等）。"""
    msgs = [
        {"msg_id": "1", "sender": "a", "send_time": "2026-10-01 12:00:00",
         "content": "测试消息一"},
        {"msg_id": "2", "sender": "b", "send_time": "2026-10-01 12:01:00",
         "content": "测试消息二"},
    ]
    f = _write_json(tmp_path, "m.json", msgs)

    r1 = import_messages(f, db_path)
    r2 = import_messages(f, db_path)

    conn = get_conn(db_path)
    cnt = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    fts_cnt = conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
    conn.close()

    assert cnt == 2
    assert fts_cnt == 2  # FTS 同步正确
    assert r1["inserted"] == 2
    assert r2["inserted"] == 0
    assert r2["skipped"] == 2


def test_rank_field_and_order(db_path, tmp_path):
    """结果含 rank 字段且按 bm25 升序（rank 递增）。"""
    msgs = [
        {"msg_id": "1", "sender": "a", "send_time": "2026-10-01 12:00:00",
         "content": "火锅火锅火锅"},
        {"msg_id": "2", "sender": "b", "send_time": "2026-10-01 12:01:00",
         "content": "火锅"},
    ]
    import_messages(_write_json(tmp_path, "m.json", msgs), db_path)

    results = kw_search("火锅", db_path=db_path)
    assert len(results) == 2
    for r in results:
        assert "rank" in r
        assert isinstance(r["rank"], float)
    # bm25 升序：rank 依次递增
    ranks = [r["rank"] for r in results]
    assert ranks == sorted(ranks)


def test_performance_10k_messages(db_path, tmp_path):
    """1 万条模拟数据下单次查询 < 500ms。"""
    msgs = []
    for i in range(10000):
        msgs.append({
            "msg_id": f"m{i}",
            "sender": f"用户{i}",
            "send_time": f"2026-10-{(i % 28) + 1:02d} 12:00:00",
            "content": f"这是第{i}条消息，今天天气不错，我们去吃火锅吧" if i % 7 == 0
                       else f"这是第{i}条普通消息，讨论学习和作业",
        })
    f = _write_json(tmp_path, "big.json", msgs)
    # 性能测试只测关键词检索，跳过向量化以避免 1 万条 embedding 拖慢用例
    import_messages(f, db_path, generate_embeddings=False)

    # 预热一次（jieba 词典加载等）
    kw_search("火锅", db_path=db_path)

    elapsed = timeit.timeit(
        lambda: kw_search("火锅", db_path=db_path),
        number=5,
    ) / 5
    assert elapsed < 0.5, f"单次查询耗时 {elapsed:.3f}s 超过 500ms"


def test_fts_trigger_sync_on_delete(db_path, tmp_path):
    """删除消息时 FTS 索引同步删除（触发器验证）。"""
    msgs = [
        {"msg_id": "1", "sender": "a", "send_time": "2026-10-01 12:00:00",
         "content": "火锅真好吃"},
        {"msg_id": "2", "sender": "b", "send_time": "2026-10-01 12:01:00",
         "content": "另一条消息"},
    ]
    import_messages(_write_json(tmp_path, "m.json", msgs), db_path)

    conn = get_conn(db_path)
    conn.execute("DELETE FROM messages WHERE msg_id='1'")
    conn.commit()
    conn.close()

    results = kw_search("火锅", db_path=db_path)
    assert len(results) == 0  # 删除后 FTS 不再命中
