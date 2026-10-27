"""带引用答案生成（T3-2）。

流程：候选消息 → 组装提示词输入 → 调大模型 → validate_answer 逐条校验引用。
校验不通过的内容一律丢弃，全部丢弃则降级为 no_result。
"""

import json
from typing import Optional

from .guard import sanitize
from .llm import chat_json
from .prompts.answer import ANSWER_SYSTEM_PROMPT
from .validate import validate_answer


def _no_result() -> dict:
    return {
        "status": "no_result",
        "answer": "没有找到能支撑回答的原始消息，无法给出确定答案。",
        "events": [],
    }


def generate_answer(
    question: str,
    candidates: list[dict],
    assumptions: Optional[list[str]] = None,
    clusters: Optional[list[dict]] = None,
) -> dict:
    """基于检索候选生成带引用答案；无候选时不调用大模型，直接返回 no_result。

    clusters 为事件关联结果（含改期/取消标记），仅作为理解上下文提供给模型；
    引用校验仍只认 candidates 里的原文。
    """
    messages = [
        {
            "msg_id": c.get("msg_id"),
            "content": c.get("content") or "",
            "sender": c.get("sender"),
            "send_time": c.get("send_time"),
        }
        for c in candidates
        if c.get("msg_id")
    ]
    if not messages:
        return _no_result()

    # corpus 保留原文并带 send_time：相对时间（如"下周六"）的字段级校验
    # 需要以该条消息的发送时间为基准换算
    corpus = {
        m["msg_id"]: {"content": m["content"], "send_time": m.get("send_time")}
        for m in messages
    }

    # 送入大模型的消息正文先做注入防护；corpus 保留原文，供引用逐字校验
    prompt_messages = [
        {**m, "content": sanitize(m["content"])} for m in messages
    ]

    payload = json.dumps(
        {
            "question": question,
            "messages": prompt_messages,
            "clusters": clusters or [],
            "assumptions": assumptions or [],
        },
        ensure_ascii=False,
    )
    raw = chat_json(
        [
            {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
            {"role": "user", "content": payload},
        ]
    )
    return validate_answer(raw, corpus)
