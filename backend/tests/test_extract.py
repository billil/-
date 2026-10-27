"""线索抽取测试（全部 mock 大模型，不发真实网络请求）。

覆盖：正常入库、非法 JSON 重试 1 次后成功、evidence 非原文子串丢弃、
空消息列表直接返回、两次非法 JSON 抛 LLMFormatError、20 条分批、lead_id 确定性。
"""

import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app.llm as llm_module
from app.db import get_conn, init_db
from app.extract import extract_leads
from app.llm import LLMFormatError

MSGS = [
    {
        "msg_id": "m1",
        "sender": "班长",
        "content": "通知：本周六晚上6点在学校南门集合去团建，大家记得带身份证",
    },
    {
        "msg_id": "m2",
        "sender": "学习委员",
        "content": "实验报告下周三前发我邮箱，文件命名学号+姓名",
    },
]

VALID_RESULT = {
    "leads": [
        {
            "msg_id": "m1",
            "category": "event",
            "title": "班级团建",
            "event_time": None,
            "time_raw": "本周六晚上6点",
            "location": "学校南门",
            "participants": ["全体同学"],
            "file_hint": None,
            "confidence": 0.8,
            "evidence": "本周六晚上6点在学校南门集合去团建",
        },
        {
            "msg_id": "m2",
            "category": "task",
            "title": "提交实验报告",
            "event_time": "2026-10-28 23:59",
            "time_raw": "下周三前",
            "location": None,
            "participants": [],
            "file_hint": "实验报告",
            "confidence": 0.9,
            "evidence": "实验报告下周三前发我邮箱",
        },
    ]
}


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "memory.db")
    init_db(path)
    return path


@pytest.fixture
def fake_llm(monkeypatch):
    """把 _call_llm 替换为可控假实现，记录调用次数。"""

    class Fake:
        def __init__(self):
            self.calls = 0
            self.responses: list[str] = []
            self.raise_on_call = False

        def __call__(self, messages, timeout=None):
            self.calls += 1
            idx = min(self.calls - 1, len(self.responses) - 1)
            return self.responses[idx]

    fake = Fake()
    monkeypatch.setattr(llm_module, "_call_llm", fake)
    return fake


def _rows(db_path):
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT lead_id, msg_id, category, title, event_time, time_raw, location,"
        " participants, file_hint, confidence FROM leads ORDER BY msg_id, category"
    ).fetchall()
    conn.close()
    return rows


def test_extract_normal_leads(db_path, fake_llm):
    """正常 JSON 正确入库。"""
    fake_llm.responses = [json.dumps(VALID_RESULT, ensure_ascii=False)]

    result = extract_leads(MSGS, db_path=db_path)

    assert result == {"leads": 2, "changes": 0, "dropped": 0, "batches": 1}
    rows = _rows(db_path)
    assert len(rows) == 2

    first, second = rows
    assert first[1] == "m1" and first[2] == "event"
    assert first[3] == "班级团建"
    assert first[4] is None  # 时间不明确 -> null
    assert first[5] == "本周六晚上6点"  # time_raw 保留原文表述
    assert first[6] == "学校南门"
    assert json.loads(first[7]) == ["全体同学"]
    assert first[9] == 0.8

    assert second[1] == "m2" and second[2] == "task"
    assert second[4] == "2026-10-28 23:59"
    assert second[7] is None  # 空参与人数组 -> null


def test_extract_retry_invalid_json(db_path, fake_llm):
    """返回非法 JSON 时重试 1 次后成功。"""
    fake_llm.responses = [
        "这不是 JSON，抱歉",
        json.dumps(VALID_RESULT, ensure_ascii=False),
    ]

    result = extract_leads(MSGS, db_path=db_path)

    assert fake_llm.calls == 2  # 首次 + 重试 1 次
    assert result["leads"] == 2


def test_retry_twice_raises(db_path, fake_llm):
    """两次都返回非法 JSON 抛 LLMFormatError。"""
    fake_llm.responses = ["坏输出一", "坏输出二"]

    with pytest.raises(LLMFormatError):
        extract_leads(MSGS, db_path=db_path)

    assert fake_llm.calls == 2


def test_evidence_not_substring_dropped(db_path, fake_llm, caplog):
    """evidence 不是原文子串的 lead 被丢弃并记 warning。"""
    bad = {
        "leads": [
            {
                "msg_id": "m1",
                "category": "event",
                "title": "周末爬山",
                "event_time": None,
                "time_raw": None,
                "location": None,
                "participants": [],
                "file_hint": None,
                "confidence": 0.7,
                "evidence": "周末一起去爬香山",  # 原文中不存在
            }
        ]
    }
    fake_llm.responses = [json.dumps(bad, ensure_ascii=False)]

    with caplog.at_level(logging.WARNING, logger="app.extract"):
        result = extract_leads(MSGS, db_path=db_path)

    assert result == {"leads": 0, "changes": 0, "dropped": 1, "batches": 1}
    assert _rows(db_path) == []
    assert any("丢弃" in rec.message for rec in caplog.records)


def test_empty_messages(db_path, fake_llm):
    """空消息列表直接返回空且不调用大模型。"""
    fake_llm.responses = [json.dumps({"leads": []})]

    result = extract_leads([], db_path=db_path)

    assert result == {"leads": 0, "changes": 0, "dropped": 0, "batches": 0}
    assert fake_llm.calls == 0


def test_batch_size_20(db_path, fake_llm):
    """25 条消息按 20 条一批，共 2 次调用。"""
    msgs = [
        {"msg_id": f"x{i}", "sender": "同学", "content": f"第{i}条普通消息"}
        for i in range(25)
    ]
    fake_llm.responses = [json.dumps({"leads": []})]

    result = extract_leads(msgs, db_path=db_path)

    assert result["batches"] == 2
    assert fake_llm.calls == 2


def test_lead_id_deterministic(db_path, fake_llm):
    """同一批数据重复抽取，lead_id 与行数保持一致（可复现）。"""
    fake_llm.responses = [json.dumps(VALID_RESULT, ensure_ascii=False)]
    extract_leads(MSGS, db_path=db_path)
    first_ids = [r[0] for r in _rows(db_path)]

    fake_llm.calls = 0
    extract_leads(MSGS, db_path=db_path)
    second_ids = [r[0] for r in _rows(db_path)]

    assert first_ids == second_ids
    assert len(first_ids) == 2
