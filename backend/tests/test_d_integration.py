"""P0-1 联调测试：D 位 SQLite 真实表结构 → B 位 facade。

覆盖三组场景：
A. 正常查询：D 授权的群消息进入候选，答案引用 D 的真实 msg_id；
B. 缺少权限参数：直接 permission_denied，不调用大模型；
C. provider 返回越权候选：facade 第二道防线丢弃，答案只引用授权消息。

说明：本文件的模型调用为固定 mock（monkeypatch `answer.chat_json`），
只验证 D 数据、字段映射、权限与引用链路，不代表真实模型效果。
"""

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app.agent as agent_mod
import app.answer as answer_mod
import app.facade as facade
from app.d_source import DStoreCandidateProvider, authorized_group_keys
from app.session import InMemorySessionStore

WORKSPACE = "ws-demo"
DB_A_MSG = "msg_a1"
DB_A_FILE = "msg_a2"
DB_B_MSG = "msg_b1"

# D 侧 messages 表没有 sender / msg_status / edited_at，只有 revision 与 metadata。
D_SCHEMA = """
CREATE TABLE groups(
  workspace TEXT NOT NULL, group_key TEXT NOT NULL, label TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'manual', authorized INTEGER NOT NULL DEFAULT 0,
  last_received TEXT, last_event_id TEXT, created TEXT, updated TEXT,
  PRIMARY KEY(workspace, group_key)
);
CREATE TABLE messages(
  id TEXT PRIMARY KEY, workspace TEXT NOT NULL, batch TEXT,
  group_label TEXT NOT NULL, content TEXT NOT NULL, sent_at TEXT,
  imported_at TEXT, metadata TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1
);
"""

PARSE_D = {
    "keywords": ["志愿服务"],
    "time": {"type": "none", "start": None, "end": None, "raw": None},
    "time_field": "send_time",
    "category": None,
    "file_hint": None,
    "filters": {"explicit": [], "vague": []},
    "ambiguous": None,
}


def _metadata(event_key="service", title="志愿服务"):
    return json.dumps(
        {
            "event_key": event_key,
            "title": title,
            "fields": {"地点": "东门"},
            "source_confirmed": True,
            "revision_kind": "initial",
            "supersedes": [],
        },
        ensure_ascii=False,
    )


@pytest.fixture
def d_db(tmp_path):
    """建一份与 D 真实建表语句一致的最小 SQLite 库并写入测试消息。"""
    path = tmp_path / "d_store.db"
    conn = sqlite3.connect(path)
    conn.executescript(D_SCHEMA)
    conn.execute(
        "INSERT INTO groups VALUES(?,?,?,?,?,?,?,?,?)",
        (WORKSPACE, "g-a", "模拟活动群", "import", 1, None, None, None, None),
    )
    conn.execute(
        "INSERT INTO groups VALUES(?,?,?,?,?,?,?,?,?)",
        (WORKSPACE, "g-b", "未授权群", "import", 0, None, None, None, None),
    )
    rows = [
        (DB_A_MSG, WORKSPACE, "batch-1", "g-a",
         "本周六志愿服务上午九点在东门集合，请提前十分钟到。",
         "2026-10-24 09:00:00", "2026-10-24 09:05:00", _metadata(), 1),
        (DB_A_FILE, WORKSPACE, "batch-1", "g-a",
         "志愿服务报名表已上传群文件。",
         "2026-10-25 18:00:00", "2026-10-25 18:02:00", _metadata(), 2),
        (DB_B_MSG, WORKSPACE, "batch-1", "g-b",
         "本周六志愿服务上午九点在东门集合（隔壁群转发）。",
         "2026-10-24 09:01:00", "2026-10-24 09:06:00", _metadata(), 1),
    ]
    conn.executemany("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return str(path)


@pytest.fixture(autouse=True)
def _patch_llm(monkeypatch):
    """固定 parse 与答案模型输出；load_clusters 跳过（D 库没有 leads 表）。"""
    monkeypatch.setattr(agent_mod, "parse_query", lambda q, today=None: dict(PARSE_D))
    monkeypatch.setattr(agent_mod, "load_clusters", lambda candidates, db_path=None: [])

    def _fake_chat_json(messages):
        return {
            "answer": "志愿服务在东门集合",
            "events": [
                {
                    "summary": "志愿服务",
                    "time": None,
                    "time_raw": None,
                    "location": "东门",
                    "participants": [],
                    "sources": [
                        {"msg_id": DB_A_MSG, "quote": "志愿服务上午九点在东门集合"}
                    ],
                    "msg_ids": [DB_A_MSG],
                }
            ],
        }

    monkeypatch.setattr(answer_mod, "chat_json", _fake_chat_json)


def test_keywords_flow_into_provider_filters():
    """7.1 修复：build_search_filters 必须把查询词传给 provider。"""
    filters = agent_mod.build_search_filters(dict(PARSE_D))
    assert filters["keywords"] == ["志愿服务"]


def test_d_provider_maps_d_columns_to_candidate(d_db):
    """D 真实列 → B Candidate 字段映射，且只返回授权群。"""
    provider = DStoreCandidateProvider(
        d_db, WORKSPACE, group_scope=["g-a"]
    )
    calls = []

    def _wrap(filters):
        calls.append(filters)
        return provider(filters)

    out = facade.retrieve(
        dict(PARSE_D), _wrap, limit=5, group_scope=["g-a"]
    )

    assert out["status"] == "ok"
    ids = {item["msg_id"] for item in out["results"]}
    assert ids == {DB_A_MSG, DB_A_FILE}
    assert DB_B_MSG not in ids

    item = next(x for x in out["results"] if x["msg_id"] == DB_A_MSG)
    assert item["content"] == "本周六志愿服务上午九点在东门集合，请提前十分钟到。"
    assert item["send_time"] == "2026-10-24 09:00:00"
    assert item["group_id"] == "g-a"
    assert item["version"] == 1
    assert item["msg_status"] == "normal"
    assert item["sender"] is None
    assert calls[0]["keywords"] == ["志愿服务"]
    assert authorized_group_keys(d_db, WORKSPACE) == ["g-a"]


def test_process_cites_real_d_message_id(d_db):
    """场景 A：同一后端入口返回答案，evidence 指向 D 的真实消息 ID。"""
    provider = DStoreCandidateProvider(d_db, WORKSPACE, group_scope=["g-a"])
    store = InMemorySessionStore()

    response = facade.process(
        "志愿服务在哪集合",
        None,
        provider,
        store,
        today="2026-10-26",
        limit=5,
        group_scope=["g-a"],
    )

    assert response["status"] == "answer"
    assert response["answer"]["status"] == "ok"
    assert "地点：东门" in response["answer"]["answer"]

    msg_ids = {item["msg_id"] for item in response["evidence"]}
    assert DB_A_MSG in msg_ids
    assert DB_B_MSG not in msg_ids

    for item in response["evidence"]:
        assert item["quote"] in (
            "本周六志愿服务上午九点在东门集合，请提前十分钟到。"
            if item["msg_id"] == DB_A_MSG
            else "志愿服务报名表已上传群文件。"
        )


def test_process_denies_without_permission_params(d_db):
    """场景 B：没有 group_scope / allowed_msg_ids → permission_denied，不调用模型。"""
    provider = DStoreCandidateProvider(d_db, WORKSPACE)
    store = InMemorySessionStore()

    response = facade.process(
        "志愿服务在哪集合", None, provider, store, today="2026-10-26"
    )

    assert response["status"] == "permission_denied"
    assert response["evidence"] == []
    assert provider.calls == 0


def test_process_drops_out_of_scope_provider_result(d_db, monkeypatch):
    """场景 C：provider 违规返回未授权群候选，facade 丢弃且不进入答案。"""
    monkeypatch.setattr(facade.settings, "DEBUG", True)
    provider = DStoreCandidateProvider(d_db, WORKSPACE, group_scope=["g-a"])

    def _leaky_provider(filters):
        # 模拟 D 侧漏过滤：把 g-b 也和授权候选一起返回
        rows = provider(filters)
        rows.append(
            {
                "msg_id": DB_B_MSG,
                "content": "本周六志愿服务上午九点在东门集合（隔壁群转发）。",
                "send_time": "2026-10-24 09:01:00",
                "group_id": "g-b",
                "version": 1,
                "msg_status": "normal",
            }
        )
        return rows

    store = InMemorySessionStore()
    response = facade.process(
        "志愿服务在哪集合",
        None,
        _leaky_provider,
        store,
        today="2026-10-26",
        limit=5,
        group_scope=["g-a"],
    )

    assert response["status"] == "answer"
    assert response["debug"]["dropped_out_of_scope"] == 1
    assert DB_B_MSG not in {item["msg_id"] for item in response["evidence"]}
