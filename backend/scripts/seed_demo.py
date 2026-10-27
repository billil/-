"""一键初始化：建库 → 导入示例数据 → 增量抽取线索。

用法（在 backend/ 目录下）：
    python -m scripts.seed_demo

无大模型环境变量时只导入原文，抽取步骤会跳过并打印提示（不影响服务启动）。
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from app.db import get_conn, init_db  # noqa: E402
from app.extract import extract_leads  # noqa: E402
from app.ingest import import_messages  # noqa: E402


def _pending_messages() -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT m.msg_id, m.sender, m.content
            FROM messages AS m
            WHERE m.content IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM leads AS l WHERE l.msg_id = m.msg_id)
            ORDER BY m.send_time, m.msg_id
            """
        ).fetchall()
    finally:
        conn.close()
    return [{"msg_id": r[0], "sender": r[1], "content": r[2]} for r in rows]


def main() -> int:
    init_db().close()
    print("[1/3] 数据库初始化完成")

    sample = os.path.join(BASE_DIR, "scripts", "sample_messages.json")
    try:
        stats = import_messages(sample)
    except Exception as exc:  # 向量模型不可用时降级为只导入原文
        print(f"      向量生成失败（{exc}），改为只导入原文")
        stats = import_messages(sample, generate_embeddings=False)
    print(f"[2/3] 示例数据导入完成：{stats}")

    if not os.environ.get("LLM_API_KEY") or not os.environ.get("LLM_BASE_URL"):
        print("[3/3] 未配置 LLM_API_KEY / LLM_BASE_URL，跳过线索抽取（检索仍可用）")
        return 0

    pending = _pending_messages()
    if not pending:
        print("[3/3] 没有待抽取的消息，跳过线索抽取")
        return 0
    try:
        result = extract_leads(pending)
        print(f"[3/3] 线索抽取完成：{result}")
    except Exception as exc:
        print(f"[3/3] 线索抽取失败（{exc}），不影响服务启动")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
