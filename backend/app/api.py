"""FastAPI 应用入口：REST 接口 + 全局异常处理器。

统一响应结构：{code, message, data}，成功时 code=0。
- 已知业务异常（AppError / LLMFormatError / sqlite3.Error）→ 对应错误码
- 未知异常 → 500 + INTERNAL_ERROR，并记录日志（不向用户泄露堆栈）

会话缓存：使用 app.agent 内的模块级 dict（session_id → 解析上下文 + 追问标记）。
进程重启即丢失，由前端重新发起 /api/search 即可，属于可接受的简化设计。
"""

import logging
import os
import sqlite3
import threading
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .agent import answer_flow, revise_flow
from .db import get_conn, init_db
from .errors import (
    AppError,
    DB_ERROR,
    GUARD_REJECTED,
    INTERNAL_ERROR,
    LLM_FORMAT_ERROR,
    MESSAGES,
)
from .extract import extract_leads
from .ingest import import_messages
from .llm import LLMFormatError
from .search_vec import probe_vector

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """启动时探测向量模型可用性。

    放进后台线程执行：探针最坏要等 3 秒超时，不能阻塞服务启动。
    探测失败只记日志（probe_vector 内部已处理），检索侧会自动降级为仅关键词。
    """
    threading.Thread(target=probe_vector, name="vec-probe", daemon=True).start()
    yield


app = FastAPI(title="群忆 API", version="1.0.0", lifespan=_lifespan)

# 本地联调：前端（Vite 等）与服务不同端口，放开 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------- 请求模型 ----------------


class SearchRequest(BaseModel):
    query: str
    session_id: Optional[str] = None


class AnswerAskRequest(BaseModel):
    session_id: str
    reply: str


class ImportRequest(BaseModel):
    json_path: str


def _ok(data=None) -> dict:
    return {"code": 0, "message": "ok", "data": data}


# ---------------- 接口 ----------------


@app.get("/api/health")
async def health() -> dict:
    """健康检查。"""
    return {"code": 0, "message": "ok", "data": None}


@app.post("/api/search")
async def search(body: SearchRequest) -> dict:
    """主检索接口：返回 answer / ask / no_result 三种 status。"""
    session_id = (body.session_id or "").strip() or uuid.uuid4().hex
    flow = answer_flow(body.query, session_id)
    return _ok(flow)


@app.post("/api/answer_ask")
async def answer_ask(body: AnswerAskRequest) -> dict:
    """回答追问后继续流程（内部走条件更正 revise_flow）。"""
    session_id = (body.session_id or "").strip()
    if not session_id:
        raise AppError(GUARD_REJECTED, "session_id 不能为空，请先调用 /api/search")
    flow = revise_flow(session_id, body.reply)
    return _ok(flow)


def _pending_messages() -> list[dict]:
    """取还没有抽取过线索的消息（增量抽取，避免重复导入时重复调用大模型）。"""
    init_db().close()
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT m.msg_id, m.sender, m.send_time, m.content
            FROM messages AS m
            WHERE m.content IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM leads AS l WHERE l.msg_id = m.msg_id)
            ORDER BY m.send_time, m.msg_id
            """
        ).fetchall()
    finally:
        conn.close()
    return [
        {"msg_id": r[0], "sender": r[1], "send_time": r[2], "content": r[3]}
        for r in rows
    ]


@app.post("/api/import")
async def import_json(body: ImportRequest) -> dict:
    """导入群聊 JSON 并触发线索抽取。

    - 导入幂等：msg_id 已存在则跳过。
    - 向量生成失败（模型未下载/离线）不影响导入，会在 data.embedding_error 中说明。
    - 线索抽取失败（大模型未配置/超时）不影响导入，会在 data.extract.error 中说明。
    """
    json_path = (body.json_path or "").strip()
    if not json_path or not os.path.isfile(json_path):
        raise AppError(GUARD_REJECTED, f"导入文件不存在：{json_path}")

    init_db().close()

    try:
        stats = import_messages(json_path)
    except Exception as exc:  # 向量模型不可用等环境问题
        logger.warning("向量生成失败，改为仅导入原文：%s", exc)
        stats = import_messages(json_path, generate_embeddings=False)
        stats["embedding_error"] = str(exc)

    pending = _pending_messages()
    try:
        extract_stats = (
            extract_leads(pending) if pending else {"leads": 0, "dropped": 0, "batches": 0}
        )
    except (AppError, LLMFormatError) as exc:
        logger.warning("线索抽取失败：%s", exc)
        extract_stats = {
            "leads": 0,
            "dropped": 0,
            "batches": 0,
            "error": getattr(exc, "message", str(exc)),
        }

    stats["extract"] = extract_stats
    return _ok(stats)


# ---------------- 全局异常处理器 ----------------


def _error_response(code: int, message: str, http_status: int) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content={"code": code, "message": message, "data": None},
    )


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """已知业务异常，按错误码表返回。"""
    logger.warning("业务异常 code=%s message=%s", exc.code, exc.message)
    return _error_response(exc.code, exc.message, exc.http_status)


@app.exception_handler(LLMFormatError)
async def llm_format_error_handler(request: Request, exc: LLMFormatError) -> JSONResponse:
    """大模型输出无法解析为 JSON（已重试 1 次）。"""
    logger.warning("大模型格式错误：%s", exc)
    return _error_response(LLM_FORMAT_ERROR, MESSAGES[LLM_FORMAT_ERROR], 502)


@app.exception_handler(sqlite3.Error)
async def db_error_handler(request: Request, exc: sqlite3.Error) -> JSONResponse:
    """数据库异常。"""
    logger.exception("数据库错误：%s", exc)
    return _error_response(DB_ERROR, MESSAGES[DB_ERROR], 500)


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底：未知异常统一 500，不泄露内部细节。"""
    logger.exception("未处理异常：%s", exc)
    return _error_response(INTERNAL_ERROR, MESSAGES[INTERNAL_ERROR], 500)
