"""U4 抽取改造测试：send_time 基准、上下文、无法换算标记、变更抽取。

全部 mock 大模型，只验证输入组织、校验与入库结果。
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app.llm as llm_module
from app.db import get_conn, init_db
from app.extract import extract_leads


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "changes.db")
    init_db(path)
    return path


@pytest.fixture
def fake_llm(monkeypatch):
    """替换 _call_llm，记录每次请求的 user payload。"""

    class Fake:
        def __init__(self):
            self.calls = 0
            self.responses: list[str] = []
            self.payloads: list[dict] = []

        def __call__(self, messages, timeout=None):
            self.calls += 1
            self.payloads.append(json.loads(messages[1]["content"]))
            idx = min(self.calls - 1, len(self.responses) - 1)
            return self.responses[idx]

    fake = Fake()
    monkeypatch.setattr(llm_module, "_call_llm", fake)
    return fake


def _json(obj):
    return json.dumps(obj, ensure_ascii=False)


def _lead_row(db_path, msg_id):
    conn = get_conn(db_path)
    try:
        return conn.execute(
            "SELECT event_time, time_raw, base_time, time_uncertain "
            "FROM leads WHERE msg_id = ?",
            (msg_id,),
        ).fetchone()
    finally:
        conn.close()


def _change_rows(db_path):
    conn = get_conn(db_path)
    try:
        return conn.execute(
            "SELECT msg_id, type, field, old_value, new_value, quote, confidence "
            "FROM leads_changes ORDER BY msg_id"
        ).fetchall()
    finally:
        conn.close()


MSG_ROWS = [
    {"msg_id": "m0", "send_time": "2026-10-25 20:00:00", "sender": "班长",
     "content": "通知：周六上午9点在南门集合"},
    {"msg_id": "m1", "send_time": "2026-10-26 09:00:00", "sender": "班长",
     "content": "明天下午交表"},
    {"msg_id": "m2", "send_time": "2026-10-26 09:30:00", "sender": "小A",
     "content": "收到"},
    {"msg_id": "m3", "send_time": "2026-10-26 10:00:00", "sender": "小B",
     "content": "好的"},
    {"msg_id": "m4", "send_time": "2026-10-26 11:00:00", "sender": "小C",
     "content": "别忘了"},
]


def test_relative_time_uses_send_time_as_base(db_path, fake_llm):
    """a. send_time=2026-10-26 09:00 + "明天下午交表" → event_time=2026-10-27。"""
    fake_llm.responses = [
        _json(
            {
                "leads": [
                    {
                        "msg_id": "m1",
                        "category": "task",
                        "title": "交表",
                        "event_time": "2026-10-27 14:00",
                        "time_raw": "明天下午",
                        "base_time": "2026-10-26 09:00",
                        "time_uncertain": False,
                        "location": None,
                        "participants": [],
                        "file_hint": None,
                        "confidence": 0.9,
                        "evidence": "明天下午交表",
                    }
                ],
                "changes": [],
            }
        )
    ]

    result = extract_leads(MSG_ROWS, db_path=db_path)

    assert result["leads"] == 1
    row = _lead_row(db_path, "m1")
    assert row[0] == "2026-10-27 14:00"  # event_time
    assert row[1] == "明天下午"  # time_raw 原文保留
    assert row[2] == "2026-10-26 09:00"  # base_time 用的是该消息 send_time
    assert row[3] == 0  # time_uncertain=false

    # 输入侧：每条消息都带 send_time，且上下文前后各最多 2 条
    items = fake_llm.payloads[0]["messages"]
    by_id = {item["msg_id"]: item for item in items}
    assert by_id["m1"]["send_time"] == "2026-10-26 09:00:00"
    assert len(by_id["m1"]["context_before"]) == 1
    assert len(by_id["m1"]["context_after"]) == 2
    assert all(len(item["context_before"]) <= 2 for item in items)
    assert all(len(item["context_after"]) <= 2 for item in items)


def test_unconvertible_time_marked_uncertain(db_path, fake_llm):
    """b. "到时候再说" → event_time=null 且 time_uncertain=true（模型漏标也会兜底）。"""
    fake_llm.responses = [
        _json(
            {
                "leads": [
                    {
                        "msg_id": "m1",
                        "category": "event",
                        "title": "待定活动",
                        "event_time": None,
                        "time_raw": "到时候再说",
                        "location": None,
                        "participants": [],
                        "file_hint": None,
                        "confidence": 0.6,
                        "evidence": "到时候再说",
                    }
                ],
                "changes": [],
            }
        )
    ]
    msgs = [
        {"msg_id": "m1", "send_time": "2026-10-26 09:00:00",
         "sender": "班长", "content": "活动时间到时候再说"},
    ]

    extract_leads(msgs, db_path=db_path)

    row = _lead_row(db_path, "m1")
    assert row[0] is None
    assert row[1] == "到时候再说"
    assert row[3] == 1  # 有原文时间但换算不出来 → 必须标记不确定


def test_change_location_keeps_old_value(db_path, fake_llm):
    """c. "地点改到东门了" → change_location，old_value 从上下文取到"南门"。"""
    msgs = [
        {"msg_id": "m0", "send_time": "2026-10-25 20:00:00", "sender": "班长",
         "content": "通知：周六上午9点在南门集合"},
        {"msg_id": "m1", "send_time": "2026-10-26 09:00:00", "sender": "班长",
         "content": "地点改到东门了"},
    ]
    fake_llm.responses = [
        _json(
            {
                "leads": [],
                "changes": [
                    {
                        "msg_id": "m1",
                        "type": "change_location",
                        "field": "location",
                        "old_value": "南门",
                        "new_value": "东门",
                        "evidence": {"msg_id": "m1", "quote": "地点改到东门了"},
                        "confidence": 0.9,
                    }
                ],
            }
        )
    ]

    result = extract_leads(msgs, db_path=db_path)

    assert result["changes"] == 1
    rows = _change_rows(db_path)
    assert len(rows) == 1
    msg_id, ctype, field, old_value, new_value, quote, confidence = rows[0]
    assert (msg_id, ctype, field) == ("m1", "change_location", "location")
    assert old_value == "南门" and new_value == "东门"
    assert quote == "地点改到东门了"
    assert confidence == pytest.approx(0.9)


def test_cancel_then_restore(db_path, fake_llm):
    """d. "活动取消" → cancel；随后"照常进行" → restore。"""
    msgs = [
        {"msg_id": "m1", "send_time": "2026-10-26 09:00:00", "sender": "班长",
         "content": "活动取消"},
        {"msg_id": "m2", "send_time": "2026-10-26 10:00:00", "sender": "班长",
         "content": "活动照常进行"},
    ]
    fake_llm.responses = [
        _json(
            {
                "leads": [],
                "changes": [
                    {
                        "msg_id": "m1",
                        "type": "cancel",
                        "field": None,
                        "old_value": None,
                        "new_value": None,
                        "evidence": {"msg_id": "m1", "quote": "活动取消"},
                        "confidence": 0.95,
                    },
                    {
                        "msg_id": "m2",
                        "type": "restore",
                        "field": None,
                        "old_value": None,
                        "new_value": None,
                        "evidence": {"msg_id": "m2", "quote": "活动照常进行"},
                        "confidence": 0.95,
                    },
                ],
            }
        )
    ]

    result = extract_leads(msgs, db_path=db_path)

    assert result["changes"] == 2
    rows = _change_rows(db_path)
    assert [(r[0], r[1]) for r in rows] == [("m1", "cancel"), ("m2", "restore")]


def test_change_quote_not_substring_dropped(db_path, fake_llm):
    """变更 quote 不是原文子串 → 丢弃，不写库。"""
    fake_llm.responses = [
        _json(
            {
                "leads": [],
                "changes": [
                    {
                        "msg_id": "m1",
                        "type": "cancel",
                        "field": None,
                        "old_value": None,
                        "new_value": None,
                        "evidence": {"msg_id": "m1", "quote": "活动取消了"},  # 原文无"了"
                        "confidence": 0.9,
                    }
                ],
            }
        )
    ]

    result = extract_leads(MSG_ROWS, db_path=db_path)

    assert result["changes"] == 0
    assert _change_rows(db_path) == []
