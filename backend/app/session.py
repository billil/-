"""会话状态存储（U1）。

- `SessionStore` 是协议：get / save / touch。
- `InMemorySessionStore` 为默认实现：TTL 30 分钟，过期即视为不存在。
- 持久化实现（落 SQLite / Redis 等）由 D 提供，实现本协议即可替换，
  业务代码只依赖 SessionStore，不直接操作具体实现。
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Protocol

TTL_SECONDS = 30 * 60

STATUS_PARSED = "parsed"
STATUS_ASKED = "asked"
STATUS_ANSWERED = "answered"


@dataclass
class Session:
    """一轮会话的上下文。

    query 为 spec 之外的补充字段：等待追问回答时，需要用"原查询 + 回答"重新解析，
    否则丢失查询对象（如"志愿活动"）。
    """

    session_id: str
    status: str = STATUS_PARSED
    query: str = ""
    parse: dict = field(default_factory=dict)
    result_summary: Optional[str] = None
    ask_count: int = 0
    updated_at: datetime = field(default_factory=datetime.now)

    def is_expired(self, ttl_seconds: int = TTL_SECONDS) -> bool:
        """是否已超过 TTL。"""
        return datetime.now() - self.updated_at > timedelta(seconds=ttl_seconds)


class SessionStore(Protocol):
    """会话存储协议：持久化实现由 D 提供，实现本协议即可替换内存实现。"""

    def get(self, session_id: str) -> Optional[Session]:
        ...

    def save(self, session: Session) -> None:
        ...

    def touch(self, session_id: str) -> None:
        ...


class InMemorySessionStore:
    """进程内实现：进程重启即清空，当前阶段可接受。"""

    def __init__(self, ttl_seconds: int = TTL_SECONDS) -> None:
        self._ttl_seconds = ttl_seconds
        self._sessions: dict[str, Session] = {}

    def get(self, session_id: str) -> Optional[Session]:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.is_expired(self._ttl_seconds):
            # 过期即视为不存在：后续更正会退化为新查询（context_lost）
            self._sessions.pop(session_id, None)
            return None
        return session

    def save(self, session: Session) -> None:
        session.updated_at = datetime.now()
        self._sessions[session.session_id] = session

    def touch(self, session_id: str) -> None:
        session = self.get(session_id)
        if session is not None:
            session.updated_at = datetime.now()

    # 以下为内存实现的便捷方法，不属于 SessionStore 协议
    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def clear(self) -> None:
        self._sessions.clear()
