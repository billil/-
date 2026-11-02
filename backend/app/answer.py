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
    group_scope: Optional[list[str]] = None,
    allowed_msg_ids: Optional[list[str]] = None,
) -> dict:
    """基于检索候选生成带引用答案；无候选时不调用大模型，直接返回 no_result。

    clusters 为事件关联结果（含改期/取消标记），仅作为理解上下文提供给模型；
    引用校验仍只认 candidates 里的原文。
    group_scope / allowed_msg_ids 会继续传给 validate_answer 的 L3 归属校验。
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

    # corpus 保留原文并带 send_time / group_id / msg_status / version：
    # 相对时间换算需要 send_time，L3/L4 需要授权群与消息版本信息。
    # 同一 msg_id 出现多个版本时优先保留 version 最新的那条。
    corpus = {}
    for candidate in candidates:
        msg_id = candidate.get("msg_id")
        if not msg_id:
            continue
        entry = {
            "content": candidate.get("content") or "",
            "send_time": candidate.get("send_time"),
            "group_id": candidate.get("group_id"),
            "msg_status": candidate.get("msg_status") or "normal",
            "version": candidate.get("version") or 1,
            "edited_at": candidate.get("edited_at"),
        }
        previous = corpus.get(msg_id)
        if previous is None or int(entry["version"]) >= int(previous["version"]):
            corpus[msg_id] = entry

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
    return validate_answer(
        raw,
        corpus,
        group_scope=group_scope,
        allowed_msg_ids=allowed_msg_ids,
    )
