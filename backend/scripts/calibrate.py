"""MIN_SCORE 阈值校准脚本。

读取 data/dev_set.jsonl（每行 {"query": "...", "relevant_msg_ids": [...]}），
对阈值网格逐点运行混合检索，计算 precision / recall / F1，打印推荐阈值，
并把结果写入 runs/calibration.md。

用法（在 backend/ 目录下）：
    python scripts/calibrate.py

说明：
- 数据集缺失或为空时只输出提示并写一份说明型 calibration.md，不报错退出。
- 计分口径：以返回结果的 msg_id 与 relevant_msg_ids 的交集为准；
  low_relevance 状态兜底返回的候选也计入返回集合（它们同样展示给用户）。
- 校准会临时改写 app.search / app.search_hybrid 的阈值常量，结束后恢复。
"""

import json
import os
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import app.search as search_cfg  # noqa: E402
import app.search_hybrid as hybrid_mod  # noqa: E402

DATA_PATH = os.path.join(BASE_DIR, "data", "dev_set.jsonl")
OUT_PATH = os.path.join(BASE_DIR, "runs", "calibration.md")

# 阈值网格：0.10 ~ 0.70，步长 0.05（覆盖初值 0.35 两侧）
GRID = [round(0.10 + 0.05 * i, 2) for i in range(13)]

# 每个查询参与评估的最大结果数
EVAL_LIMIT = 20


def load_dev_set(path: str) -> list[dict]:
    """读取 JSONL 数据集，跳过缺少 query 或 relevant_msg_ids 的坏行。"""
    items: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                print(f"[跳过] 第 {lineno} 行不是合法 JSON")
                continue
            query = str(raw.get("query") or "").strip()
            relevant = raw.get("relevant_msg_ids")
            if not query or not isinstance(relevant, list):
                print(f"[跳过] 第 {lineno} 行缺少 query / relevant_msg_ids")
                continue
            items.append(
                {"query": query, "relevant_msg_ids": [str(x) for x in relevant]}
            )
    return items


def apply_threshold(threshold: float) -> None:
    """同时改写配置模块与检索模块的阈值常量（后者是 import 时的副本）。"""
    search_cfg.MIN_SCORE_KW = threshold
    search_cfg.MIN_SCORE_VEC = threshold
    hybrid_mod.MIN_SCORE_KW = threshold
    hybrid_mod.MIN_SCORE_VEC = threshold


def warmup_vector() -> bool:
    """预热向量模型，并把加载耗时排除在网格扫描之外。

    模型首次加载可能超过单次 3 秒超时，若不预热，前几次查询会被判为失败并触发熔断，
    导致后续阈值全部只走关键词，校准结果失真。
    """
    try:
        from app.embed import EmbeddingClient
        from app.search_vec import reset_vector_state

        EmbeddingClient().embed_texts(["校准预热"])
        reset_vector_state()
        return True
    except Exception as exc:  # noqa: BLE001 - 模型不可用时仍可用关键词校准
        print(f"[calibrate] 向量模型不可用，本次仅用关键词检索校准：{exc}")
        return False


def evaluate(threshold: float, dev_set: list[dict]) -> dict:
    """在给定阈值下评估整个数据集，返回宏平均指标与逐条明细。"""
    apply_threshold(threshold)
    per_query: list[dict] = []
    precision_sum = recall_sum = f1_sum = 0.0

    for item in dev_set:
        relevant = set(item["relevant_msg_ids"])
        if not relevant:
            continue
        out = hybrid_mod.hybrid_search(item["query"], limit=EVAL_LIMIT)
        retrieved = [r.get("msg_id") for r in out.get("results") or []]
        hits = len(set(retrieved) & relevant)

        precision = hits / len(retrieved) if retrieved else 0.0
        recall = hits / len(relevant)
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )

        precision_sum += precision
        recall_sum += recall
        f1_sum += f1
        per_query.append(
            {
                "query": item["query"],
                "outcome": (out.get("debug") or {}).get("outcome"),
                "retrieved": retrieved,
                "relevant": sorted(relevant),
                "hits": hits,
                "precision": precision,
                "recall": recall,
                "f1": f1,
            }
        )

    count = len(per_query)
    return {
        "threshold": threshold,
        "queries": count,
        "precision": precision_sum / count if count else 0.0,
        "recall": recall_sum / count if count else 0.0,
        "f1": f1_sum / count if count else 0.0,
        "per_query": per_query,
    }


def write_markdown(rows: list[dict], best: dict, dev_set_size: int) -> None:
    """把校准表与推荐阈值写入 runs/calibration.md。"""
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    lines = [
        "# MIN_SCORE 阈值校准",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 数据集：data/dev_set.jsonl（{dev_set_size} 条）",
        f"- 评估条数：{best['queries']}（relevant_msg_ids 为空的条目不参与）",
        "- 计分口径：返回 msg_id 与 relevant_msg_ids 的交集；low_relevance 兜底候选计入返回集合",
        "",
        "## 网格结果",
        "",
        "| MIN_SCORE | precision | recall | F1 |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['threshold']:.2f} | {row['precision']:.4f} | "
            f"{row['recall']:.4f} | {row['f1']:.4f} |"
        )

    lines += [
        "",
        "## 推荐阈值",
        "",
        f"`MIN_SCORE = {best['threshold']:.2f}`（F1 最高：{best['f1']:.4f}）",
        "",
        "## 推荐阈值下的逐条明细",
        "",
        "| query | outcome | 返回数 | 命中数 | precision | recall | F1 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in best["per_query"]:
        lines.append(
            f"| {item['query']} | {item['outcome']} | {len(item['retrieved'])} | "
            f"{item['hits']} | {item['precision']:.4f} | {item['recall']:.4f} | "
            f"{item['f1']:.4f} |"
        )
    lines.append("")

    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def write_missing_markdown(reason: str) -> None:
    """数据集缺失时写说明型报告，方便直接查看缺什么。"""
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    content = "\n".join(
        [
            "# MIN_SCORE 阈值校准",
            "",
            f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 状态：{reason}",
            "",
            "## 如何补齐数据集",
            "",
            "在 `backend/data/dev_set.jsonl` 每行写入一个 JSON：",
            "",
            "```json",
            '{"query": "这周的志愿活动", "relevant_msg_ids": ["m1", "m3"]}',
            "```",
            "",
            "然后重新执行：",
            "",
            "```bash",
            "python scripts/calibrate.py",
            "```",
            "",
        ]
    )
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        fh.write(content)


def main() -> int:
    if not os.path.isfile(DATA_PATH):
        reason = f"数据集不存在：{DATA_PATH}"
        print(f"[calibrate] {reason}")
        write_missing_markdown(reason)
        print(f"[calibrate] 已写入说明：{OUT_PATH}")
        return 0

    dev_set = load_dev_set(DATA_PATH)
    if not dev_set:
        reason = f"数据集为空或全部条目无效：{DATA_PATH}"
        print(f"[calibrate] {reason}")
        write_missing_markdown(reason)
        print(f"[calibrate] 已写入说明：{OUT_PATH}")
        return 0

    original = (
        search_cfg.MIN_SCORE_KW,
        search_cfg.MIN_SCORE_VEC,
        hybrid_mod.MIN_SCORE_KW,
        hybrid_mod.MIN_SCORE_VEC,
    )
    try:
        warmup_vector()
        rows = [evaluate(threshold, dev_set) for threshold in GRID]
    finally:
        (
            search_cfg.MIN_SCORE_KW,
            search_cfg.MIN_SCORE_VEC,
            hybrid_mod.MIN_SCORE_KW,
            hybrid_mod.MIN_SCORE_VEC,
        ) = original

    valid_rows = [r for r in rows if r["queries"] > 0]
    if not valid_rows:
        print("[calibrate] 所有条目的 relevant_msg_ids 均为空，无法计算指标")
        write_missing_markdown("数据集条目的 relevant_msg_ids 全为空")
        return 0

    # 推荐规则：F1 最高；同分时 recall 更高者优先，再同分取阈值较大者（少返回噪声）
    best = sorted(
        valid_rows,
        key=lambda r: (-r["f1"], -r["recall"], -r["threshold"]),
    )[0]

    print(f"[calibrate] 数据集：{len(dev_set)} 条，参与评估：{best['queries']} 条")
    print("[calibrate] 阈值   precision  recall    F1")
    for row in rows:
        print(
            f"  {row['threshold']:.2f}    {row['precision']:.4f}   "
            f"{row['recall']:.4f}   {row['f1']:.4f}"
        )
    print(
        f"[calibrate] 推荐 MIN_SCORE = {best['threshold']:.2f}"
        f"（F1={best['f1']:.4f}, P={best['precision']:.4f}, R={best['recall']:.4f}）"
    )

    write_markdown(rows, best, len(dev_set))
    print(f"[calibrate] 结果已写入：{OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
