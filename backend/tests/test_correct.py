"""条件更正测试（mock 大模型与检索，不发真实请求）。

核心断言：改条件 = 替换，不是叠加。所有用例都检查"旧值已消失"，而不只是"新值存在"。
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app.llm as llm_module
import app.agent as agent_module
from app.agent import get_session, reset_session, revise_flow, save_session

TODAY = "2026-10-21"  # 周三：本周一 10-19，上周一 10-12

# 上一轮解析：上周的火锅局
ORIGINAL = {
    "keywords": ["火锅", "局"],
    "time": {
        "type": "relative",
        "start": "2026-10-12 00:00:00",
        "end": "2026-10-18 23:59:59",
        "raw": "上周",
    },
    "category": "event",
    "file_hint": None,
    "ambiguous": None,
}

ORIGINAL_FILE = {
    "keywords": ["报名表"],
    "time": {"type": "none", "start": None, "end": None, "raw": None},
    "category": "file",
    "file_hint": "报名表",
    "ambiguous": None,
}

ORIGINAL_CLASS = {
    "keywords": ["三班", "活动"],
    "time": {"type": "none", "start": None, "end": None, "raw": None},
    "category": "event",
    "file_hint": None,
    "ambiguous": None,
}


@pytest.fixture(autouse=True)
def _clean_sessions():
    for sid in ["c1", "c2", "c3", "c4", "c5"]:
        reset_session(sid)
    yield
    for sid in ["c1", "c2", "c3", "c4", "c5"]:
        reset_session(sid)


@pytest.fixture
def fake_llm(monkeypatch):
    class Fake:
        def __init__(self):
            self.calls = 0
            self.responses: list[str] = []

        def __call__(self, messages, timeout=None):
            idx = min(self.calls, len(self.responses) - 1)
            self.calls += 1
            return self.responses[idx]

    fake = Fake()
    monkeypatch.setattr(llm_module, "_call_llm", fake)
    return fake


@pytest.fixture
def fake_search(monkeypatch):
    class Fake:
        def __init__(self):
            self.calls: list[dict] = []

        def __call__(self, **kwargs):
            self.calls.append(kwargs)
            # 返回一条候选，保证流程走到 answer 分支（空候选会按 T3-3 规则降级 no_result）
            return {
                "results": [
                    {
                        "msg_id": "m1",
                        "content": "周六晚上6点在南门集合",
                        "sender": "班长",
                        "send_time": "2026-10-20 10:00:00",
                        "score": 0.03,
                        "sources": ["kw"],
                    }
                ],
                "debug": {
                    "applied_filters": [],
                    "soft_boost": {},
                    "result_count_before_filter": 1,
                    "result_count_after_filter": 1,
                },
            }

    fake = Fake()
    monkeypatch.setattr(agent_module, "hybrid_search", fake)
    # 答案生成在 T3-2 接入，这里用桩隔离，避免干扰大模型调用次数断言
    monkeypatch.setattr(
        agent_module,
        "generate_answer",
        lambda *a, **k: {"status": "ok", "answer": "", "events": []},
    )
    return fake


def _json(obj):
    return json.dumps(obj, ensure_ascii=False)


def test_replace_time(fake_llm, fake_search):
    """"不是上周，是上个月" → time 完全替换，不与上周做并集。"""
    corrected = dict(ORIGINAL)
    corrected["time"] = {
        "type": "relative",
        "start": "2026-09-01 00:00:00",
        "end": "2026-09-30 23:59:59",
        "raw": "上个月",
    }
    fake_llm.responses = [_json(corrected)]
    save_session("c1", "上周的火锅局", ORIGINAL)

    result = revise_flow("c1", "不是上周，是上个月", today=TODAY)

    time_block = result["parse"]["time"]
    assert time_block["start"] == "2026-09-01 00:00:00"
    assert time_block["end"] == "2026-09-30 23:59:59"
    assert time_block["raw"] == "上个月"
    # 旧区间必须消失（不能出现"上周 OR 上个月"）
    assert "2026-10-12" not in json.dumps(result["parse"], ensure_ascii=False)
    assert fake_search.calls[0]["filters"]["time_range"]["start"] == "2026-09-01 00:00:00"
    assert fake_search.calls[0]["filters"]["time_range"]["end"] == "2026-09-30 23:59:59"


def test_replace_category(fake_llm, fake_search):
    """"不是文件，是活动通知" → category 替换为 event，file_hint 清空。"""
    corrected = {
        "keywords": ["活动", "通知"],
        "time": {"type": "none", "start": None, "end": None, "raw": None},
        "category": "event",
        "file_hint": None,
        "ambiguous": None,
    }
    fake_llm.responses = [_json(corrected)]
    save_session("c2", "找一下报名表", ORIGINAL_FILE)

    result = revise_flow("c2", "不是文件，是活动通知", today=TODAY)

    assert result["parse"]["category"] == "event"
    assert result["parse"]["file_hint"] is None
    assert "报名表" not in result["parse"]["keywords"]


def test_replace_entity(fake_llm, fake_search):
    """"不是三班的，是四班的活动" → 关键词中的"三班"被"四班"替换。"""
    corrected = dict(ORIGINAL_CLASS)
    corrected["keywords"] = ["四班", "活动"]
    fake_llm.responses = [_json(corrected)]
    save_session("c3", "三班的活动", ORIGINAL_CLASS)

    result = revise_flow("c3", "不是三班的，是四班的活动", today=TODAY)

    keywords = result["parse"]["keywords"]
    assert "四班" in keywords
    assert "三班" not in keywords  # 旧实体必须消失
    assert fake_search.calls[0]["query"] == "四班 活动"


def test_multi_correction(fake_llm, fake_search):
    """一句话两处更正："不是上周是这周，而且是打球不是火锅" → 时间与关键词同时替换。"""
    corrected = {
        "keywords": ["打球"],
        "time": {
            "type": "relative",
            "start": "2026-10-19 00:00:00",
            "end": "2026-10-25 23:59:59",
            "raw": "这周",
        },
        "category": "event",
        "file_hint": None,
        "ambiguous": None,
    }
    fake_llm.responses = [_json(corrected)]
    save_session("c4", "上周的火锅局", ORIGINAL)

    result = revise_flow("c4", "改一下，不是上周是这周，而且是打球不是火锅", today=TODAY)

    parse = result["parse"]
    assert parse["time"]["start"] == "2026-10-19 00:00:00"
    assert parse["time"]["raw"] == "这周"
    assert parse["keywords"] == ["打球"]
    assert "火锅" not in parse["keywords"]
    assert "2026-10-12" not in json.dumps(parse, ensure_ascii=False)


def test_revise_updates_session_cache(fake_llm, fake_search):
    """更正后新 parse 写回会话缓存，支持连续多次更正。"""
    corrected = dict(ORIGINAL)
    corrected["time"] = {
        "type": "relative",
        "start": "2026-09-01 00:00:00",
        "end": "2026-09-30 23:59:59",
        "raw": "上个月",
    }
    fake_llm.responses = [_json(corrected)]
    save_session("c5", "上周的火锅局", ORIGINAL)

    revise_flow("c5", "不是上周，是上个月", today=TODAY)

    cached = get_session("c5")
    assert cached["parse"]["time"]["raw"] == "上个月"
    assert cached["pending"] is False


def test_revise_without_session_falls_back_to_new_query(fake_llm, fake_search):
    """没有上一轮解析时，更正语句按新查询处理（不报错）。"""
    fresh = {
        "keywords": ["打球"],
        "time": {"type": "none", "start": None, "end": None, "raw": None},
        "category": "event",
        "file_hint": None,
        "ambiguous": None,
    }
    fake_llm.responses = [_json(fresh)]

    result = revise_flow("c1", "找打球的活动", today=TODAY)

    assert result["status"] == "answer"
    assert "打球" in result["parse"]["keywords"]
    assert fake_llm.calls == 1
