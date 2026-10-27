"""REST 接口测试（直接调用路由函数，不依赖 httpx）。

说明：FastAPI TestClient 需要 httpx（本项目未安装），因此直接 await 路由函数，
校验统一响应结构与参数透传。完整 HTTP 链路由 scripts/run.bat + curl 验证。
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app.api as api_module
from app.api import AnswerAskRequest, ImportRequest, SearchRequest
from app.errors import AppError, GUARD_REJECTED

API_PATHS = {"/api/search", "/api/answer_ask", "/api/import", "/api/health"}


def test_routes_registered():
    """四个 /api 路由都已注册。"""
    paths = {getattr(route, "path", None) for route in api_module.app.routes}
    assert API_PATHS.issubset(paths)


def test_health():
    """/api/health 返回统一三段结构 {code, message, data}。"""
    response = asyncio.run(api_module.health())
    assert response == {"code": 0, "message": "ok", "data": None}


def test_search_generates_session_when_absent(monkeypatch):
    """session_id 为空时自动生成，并透传 query。"""
    captured = {}

    def fake_flow(query, session_id):
        captured["query"] = query
        captured["session_id"] = session_id
        return {"status": "no_result", "session_id": session_id, "suggestions": []}

    monkeypatch.setattr(api_module, "answer_flow", fake_flow)

    response = asyncio.run(api_module.search(SearchRequest(query="找团建")))

    assert response["code"] == 0
    data = response["data"]
    assert data["status"] == "no_result"
    assert len(data["session_id"]) == 32  # uuid4().hex
    assert captured["query"] == "找团建"


def test_search_keeps_provided_session(monkeypatch):
    """传入 session_id 时继续使用该会话。"""
    def fake_flow(query, session_id):
        return {"status": "ask", "session_id": session_id, "question": "哪段时间？", "options": ["这周", "上周"]}

    monkeypatch.setattr(api_module, "answer_flow", fake_flow)

    response = asyncio.run(
        api_module.search(SearchRequest(query="那个活动", session_id="sess-abc"))
    )

    assert response["data"]["session_id"] == "sess-abc"
    assert response["data"]["status"] == "ask"


def test_answer_ask_goes_through_revise(monkeypatch):
    """回答追问内部走 revise_flow。"""
    captured = {}

    def fake_revise(session_id, correction):
        captured["args"] = (session_id, correction)
        return {"status": "answer", "session_id": session_id, "parse": {}}

    monkeypatch.setattr(api_module, "revise_flow", fake_revise)

    response = asyncio.run(
        api_module.answer_ask(AnswerAskRequest(session_id="s1", reply="上个月"))
    )

    assert response["code"] == 0
    assert captured["args"] == ("s1", "上个月")


def test_answer_ask_requires_session():
    """session_id 为空 → GUARD_REJECTED。"""
    with pytest.raises(AppError) as excinfo:
        asyncio.run(api_module.answer_ask(AnswerAskRequest(session_id="  ", reply="上个月")))

    assert excinfo.value.code == GUARD_REJECTED


def test_import_rejects_missing_file():
    """文件不存在 → GUARD_REJECTED，而不是 500。"""
    with pytest.raises(AppError) as excinfo:
        asyncio.run(api_module.import_json(ImportRequest(json_path="no/such/file.json")))

    assert excinfo.value.code == GUARD_REJECTED


def test_import_returns_stats(monkeypatch, tmp_path):
    """导入成功时返回导入统计 + 抽取统计。"""
    sample = tmp_path / "msgs.json"
    sample.write_text(json.dumps([{"msg_id": "m1", "content": "周六团建"}], ensure_ascii=False), encoding="utf-8")

    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(
        api_module,
        "import_messages",
        lambda path: {"inserted": 1, "skipped": 0, "total": 1, "embedded": 1},
    )
    monkeypatch.setattr(
        api_module,
        "_pending_messages",
        lambda: [{"msg_id": "m1", "sender": "班长", "content": "周六团建"}],
    )
    monkeypatch.setattr(
        api_module, "extract_leads", lambda msgs: {"leads": 1, "dropped": 0, "batches": 1}
    )

    response = asyncio.run(api_module.import_json(ImportRequest(json_path=str(sample))))

    data = response["data"]
    assert response["code"] == 0
    assert data["inserted"] == 1
    assert data["embedded"] == 1
    assert data["extract"] == {"leads": 1, "dropped": 0, "batches": 1}
