#!/usr/bin/env python3
"""查询本地 SQLite 中已保存的 QQ 群消息。"""

import argparse
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="查询 QQ 群消息")
    parser.add_argument("--db", default=str(BASE_DIR / "data" / "qq_messages.db"))
    parser.add_argument("--group", help="只查询指定 group_openid")
    parser.add_argument("--limit", type=int, default=50, help="返回条数，默认 50，最大 500")
    args = parser.parse_args()
    limit = max(1, min(args.limit, 500))
    database = Path(args.db)
    if not database.exists():
        raise SystemExit(f"数据库不存在：{database}")

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    sql = (
        "SELECT timestamp, group_openid, username, member_openid, content "
        "FROM messages"
    )
    params = []
    if args.group:
        sql += " WHERE group_openid = ?"
        params.append(args.group)
    sql += " ORDER BY received_at DESC LIMIT ?"
    params.append(limit)
    rows = connection.execute(sql, params).fetchall()
    connection.close()

    for row in reversed(rows):
        content = (row["content"] or "").replace("\r", "\\r").replace("\n", "\\n")
        print(
            f"[{row['timestamp'] or '-'}] {row['group_openid']} | "
            f"{row['username'] or row['member_openid'] or '未知成员'}：{content}"
        )
    print(f"\n共显示 {len(rows)} 条（按时间正序）。")


if __name__ == "__main__":
    main()
