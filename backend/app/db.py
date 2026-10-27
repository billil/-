"""SQLite 数据库初始化与连接管理。

负责创建 memory.db，建立 messages、leads 表以及 FTS5 虚拟表 messages_fts，
并通过 AFTER INSERT/UPDATE/DELETE 触发器保持 FTS 索引与 messages 表同步。
"""

import os
import sqlite3
from typing import Optional

# 默认数据库路径：backend/memory.db
# 仅用于旧版 api.py / agent.answer_flow 兼容入口；
# 新接入请走 app.facade，db_path 由 D 通过参数传入。
# 亦可设环境变量 MEMORY_DB_PATH 覆盖默认值。
DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "memory.db")


def get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    """获取 SQLite 连接，启用 WAL 与外键约束。"""
    path = db_path or os.environ.get("MEMORY_DB_PATH", DEFAULT_DB_PATH)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_column(
    cur: sqlite3.Cursor, table: str, column: str, ddl: str
) -> None:
    """旧库补列：列不存在时执行 ALTER TABLE（SQLite 不支持 ADD COLUMN IF NOT EXISTS）。"""
    columns = {row[1] for row in cur.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    """初始化数据库：建表 + FTS5 虚拟表 + 同步触发器。

    返回连接对象（调用方负责关闭）。重复调用幂等（IF NOT EXISTS）。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()

    # 消息主表
    # content        : 原始消息内容（用于展示，保证可溯源）
    # content_terms  : jieba 预分词后的空格分隔串（供 FTS5 unicode61 建索引）
    # embedding      : float32 向量的二进制（np.ndarray.tobytes()），供语义检索
    # 说明：unicode61 对中文按整段建 token，必须在索引侧预分词才能子串命中
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            msg_id        TEXT PRIMARY KEY,
            group_id      TEXT,
            sender        TEXT,
            sender_id     TEXT,
            send_time     TEXT,
            content       TEXT,
            content_terms TEXT,
            embedding     BLOB,
            raw_json      TEXT
        )
        """
    )

    # 线索表（AI 解析产物，B02 模块后续写入）
    # base_time     : event_time 换算所用的基准时间（通常为该消息 send_time）
    # time_uncertain: 原文有相对时间但无法换算时为 1，event_time 必须为 null
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS leads (
            lead_id      TEXT PRIMARY KEY,
            msg_id       TEXT,
            category     TEXT CHECK(category IN ('event','task','file')),
            title        TEXT,
            event_time   TEXT,
            time_raw     TEXT,
            base_time    TEXT,
            time_uncertain INTEGER DEFAULT 0,
            location     TEXT,
            participants TEXT,
            file_hint    TEXT,
            confidence   REAL,
            created_at   TEXT
        )
        """
    )

    # 旧库兼容：CREATE TABLE IF NOT EXISTS 不会补列，这里显式迁移
    _ensure_column(cur, "leads", "base_time", "TEXT")
    _ensure_column(cur, "leads", "time_uncertain", "INTEGER DEFAULT 0")

    # 变更表（U4）：改期 / 改地点 / 取消 / 恢复等，保留旧值与新值，可溯源
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS leads_changes (
            change_id  TEXT PRIMARY KEY,
            lead_id    TEXT,
            msg_id     TEXT,
            type       TEXT CHECK(type IN
                        ('reschedule_time','change_location','cancel','restore','other')),
            field      TEXT,
            old_value  TEXT,
            new_value  TEXT,
            quote      TEXT,
            confidence REAL,
            created_at TEXT
        )
        """
    )

    # U3：时间字段分离，send_time / event_time 分别建索引，供检索按各自字段过滤
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_send_time ON messages(send_time)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_group_id ON messages(group_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_leads_event_time ON leads(event_time)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_leads_msg_id ON leads(msg_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_leads_changes_msg_id ON leads_changes(msg_id)"
    )

    # FTS5 虚拟表：外部内容表指向 messages，索引 content_terms（jieba 预分词）与 sender
    cur.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            content_terms,
            sender,
            content='messages',
            content_rowid='rowid',
            tokenize='unicode61'
        )
        """
    )

    # 触发器：INSERT 同步到 FTS（用 content_terms 建索引）
    cur.execute(
        """
        CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, content_terms, sender)
            VALUES (new.rowid, new.content_terms, new.sender);
        END
        """
    )

    # 触发器：DELETE 同步到 FTS（FTS5 删除专用语法）
    cur.execute(
        """
        CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content_terms, sender)
            VALUES ('delete', old.rowid, old.content_terms, old.sender);
        END
        """
    )

    # 触发器：UPDATE 同步到 FTS（先删后插）
    cur.execute(
        """
        CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content_terms, sender)
            VALUES ('delete', old.rowid, old.content_terms, old.sender);
            INSERT INTO messages_fts(rowid, content_terms, sender)
            VALUES (new.rowid, new.content_terms, new.sender);
        END
        """
    )

    conn.commit()
    return conn
