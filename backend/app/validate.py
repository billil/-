"""答案引用校验（不信任大模型输出）。

规则：
1. 每条 source 的 quote 必须是对应 msg_id 原文的逐字子串，否则丢弃该 source。
2. events 的 sources 全部被丢弃时，丢弃该 event。
3. 全部 event 都被丢弃时，降级为 no_result（不保留模型的任何结论）。
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _clean_str(value) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _clean_participants(value) -> Optional[list[str]]:
    if not isinstance(value, list):
        return None
    names = [_clean_str(v) for v in value]
    names = [n for n in names if n]
    return names or None


def _no_result() -> dict:
    return {
        "status": "no_result",
        "answer": "没有找到能支撑回答的原始消息，无法给出确定答案。",
        "events": [],
    }


def validate_answer(result, corpus: dict[str, str]) -> dict:
    """校验并规整答案，返回 {"status": "ok"|"no_result", "answer": str, "events": [...]}。

    corpus: {msg_id: 原文 content}，只允许引用其中的 msg_id。
    """
    if not isinstance(result, dict):
        return _no_result()

    answer = _clean_str(result.get("answer")) or ""
    raw_events = result.get("events")
    if not isinstance(raw_events, list):
        return _no_result()

    events = []
    for raw_event in raw_events:
        if not isinstance(raw_event, dict):
            continue

        kept_sources = []
        seen = set()
        raw_sources = raw_event.get("sources")
        for source in raw_sources if isinstance(raw_sources, list) else []:
            if not isinstance(source, dict):
                continue
            msg_id = _clean_str(source.get("msg_id"))
            quote = source.get("quote")
            if not msg_id or not isinstance(quote, str) or not quote.strip():
                logger.warning("丢弃引用：msg_id 或 quote 缺失")
                continue

            content = corpus.get(msg_id)
            if content is None:
                logger.warning("丢弃引用：msg_id=%s 不在检索候选中", msg_id)
                continue
            if quote not in content:
                logger.warning("丢弃引用：quote 不是 msg_id=%s 的原文字串", msg_id)
                continue

            key = (msg_id, quote)
            if key in seen:
                continue
            seen.add(key)
            kept_sources.append({"msg_id": msg_id, "quote": quote})

        if not kept_sources:
            logger.warning("丢弃事件：%s 没有任何通过校验的引用", _clean_str(raw_event.get("summary")))
            continue

        events.append(
            {
                "summary": _clean_str(raw_event.get("summary")),
                "time": _clean_str(raw_event.get("time")),
                "time_raw": _clean_str(raw_event.get("time_raw")),
                "location": _clean_str(raw_event.get("location")),
                "participants": _clean_participants(raw_event.get("participants")),
                "sources": kept_sources,
                "msg_ids": [s["msg_id"] for s in kept_sources],
            }
        )

    if not events:
        return _no_result()

    return {
        "status": "ok",
        "answer": answer or "已找到相关聊天记录，详见下方引用。",
        "events": events,
    }
