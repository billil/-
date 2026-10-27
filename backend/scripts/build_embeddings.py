"""离线脚本：为 messages 表中 embedding 为空的行增量构建向量。

用法：
    python scripts/build_embeddings.py [--db PATH]
"""

import argparse
import sys

# 允许以脚本方式直接运行
sys.path.insert(0, ".")

from app.db import get_conn, init_db
from app.ingest import _fill_null_embeddings


def main():
    parser = argparse.ArgumentParser(description="增量构建 messages 表的向量")
    parser.add_argument("--db", default=None, help="数据库路径（默认 backend/memory.db）")
    args = parser.parse_args()

    init_db(args.db)
    conn = get_conn(args.db)
    n = _fill_null_embeddings(conn)
    conn.close()
    print(f"已为 {n} 条消息生成向量。")


if __name__ == "__main__":
    main()
