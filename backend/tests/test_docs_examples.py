"""T1 文档示例校验。

只校验文档与示例，不碰业务代码：
- docs/examples 下三组请求/响应 JSON 可解析、字段齐全、B/D 归属标注完整
- 引用 msg_id / quote 能溯源到示例候选原文（不编造）
- docs/B_interface.md 的错误码表与 app/errors.py 一致
"""

import json
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import app.errors as errors

EXAMPLES_DIR = BACKEND / "docs" / "examples"
EXAMPLES = {
    "A": "A_single_query.json",
    "B": "B_followup_correction.json",
    "C": "C_no_result.json",
}
SCENARIOS = list(EXAMPLES.keys())


def _load(scenario):
    """读取单个示例；返回 (路径, 解析后的 dict)。"""
    path = EXAMPLES_DIR / EXAMPLES[scenario]
    return path, json.loads(path.read_text(encoding="utf-8"))


def _all_candidate_contents(example):
    """汇总所有轮次 request.candidates 的 msg_id -> content，供引用溯源。"""
    contents = {}
    for round_ in example["rounds"]:
        for candidate in round_["request"]["candidates"]:
            contents[candidate["msg_id"]] = candidate["content"]
    return contents


# ---------------------------------------------------------------------------
# 文件与 JSON 基础
# ---------------------------------------------------------------------------


def test_three_example_files_exist():
    """三个示例文件必须存在。"""
    for filename in EXAMPLES.values():
        assert (EXAMPLES_DIR / filename).is_file(), f"缺少示例文件：{filename}"


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_example_is_valid_json(scenario):
    """每个示例都必须是合法 JSON 对象。"""
    _, example = _load(scenario)
    assert isinstance(example, dict)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_example_top_level_fields(scenario):
    """顶层字段齐全，scenario 与文件名对应。"""
    _, example = _load(scenario)
    assert example["version"] == "v1.0"
    assert example["scenario"] == scenario
    assert example["title"]
    assert example["description"]
    assert isinstance(example["rounds"], list)
    assert example["rounds"], "rounds 不能为空"


# ---------------------------------------------------------------------------
# 请求 / 响应结构与 B/D 归属
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_round_request_fields_and_owner(scenario):
    """每轮 request 字段齐全，且每个字段都有 B/D 归属标注。"""
    _, example = _load(scenario)
    for round_ in example["rounds"]:
        request = round_["request"]
        for key in ("query", "session", "today", "d_context", "candidates"):
            assert key in request, f"request 缺少字段：{key}"

        owner = request["_owner"]
        assert set(request) - {"_owner"} == set(owner), "_owner 必须覆盖 request 全部字段"
        assert set(owner.values()) <= {"B", "D"}

        # 这些只能由 D 注入/提供
        for key in ("query", "session", "today", "d_context", "candidates"):
            assert owner[key] == "D", f"{key} 必须标为 D"


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_round_response_fields_and_owner(scenario):
    """每轮 response 至少含 status/parse，且每个字段都有 B/D 归属标注。"""
    _, example = _load(scenario)
    for round_ in example["rounds"]:
        response = round_["response"]
        assert response["status"] in {
            "answer",
            "ask",
            "no_data",
            "low_relevance",
            "uncertain",
            "retrieval_degraded",
            "permission_denied",
        }
        assert isinstance(response["parse"], dict)

        owner = response["_owner"]
        assert set(response) - {"_owner"} == set(owner), "_owner 必须覆盖 response 全部字段"
        assert set(owner.values()) <= {"B", "D"}
        assert owner["status"] == "B"
        assert owner["parse"] == "B"


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_session_fields(scenario):
    """Session 结构字段齐全，status 取值合法。"""
    _, example = _load(scenario)
    for round_ in example["rounds"]:
        session = round_["request"]["session"]
        for key in (
            "session_id",
            "status",
            "parse",
            "last_query",
            "updated_at",
            "ask_count",
        ):
            assert key in session, f"session 缺少字段：{key}"
        assert session["status"] in {"parsed", "asked", "answered"}


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_candidate_fields_and_allowlist(scenario):
    """Candidate 字段齐全，且 msg_id 必须落在 D 注入的 allowed_msg_ids 内。"""
    _, example = _load(scenario)
    for round_ in example["rounds"]:
        d_context = round_["request"]["d_context"]
        allowed = set(d_context["allowed_msg_ids"])
        for candidate in round_["request"]["candidates"]:
            for key in (
                "msg_id",
                "content",
                "sender",
                "send_time",
                "group_id",
                "category",
                "file_hint",
                "score",
                "sources",
                "version",
            ):
                assert key in candidate, f"candidate 缺少字段：{key}"
            assert candidate["msg_id"] in allowed, "候选越出 allowed_msg_ids 白名单"
            assert candidate["group_id"] == d_context["group_id"], "候选跨群"


# ---------------------------------------------------------------------------
# 场景语义
# ---------------------------------------------------------------------------


def test_scenario_a_one_shot_answer():
    """A：一次问清 → answer，且 evidence 非空。"""
    _, example = _load("A")
    response = example["rounds"][-1]["response"]
    assert response["status"] == "answer"
    assert isinstance(response["answer"], dict)
    assert response["evidence"]
    assert response["message"] == ""


def test_scenario_b_followup_then_correction():
    """B：第一轮追问，第二轮按更正换成下周后给出答案。"""
    _, example = _load("B")
    first, second = example["rounds"]
    assert first["response"]["status"] == "ask"
    assert first["response"]["question"]
    assert first["response"]["options"]

    assert second["request"]["session"]["status"] == "asked"
    response = second["response"]
    assert response["status"] == "answer"
    assert response["parse"]["time"]["start"] == "2026-11-09 00:00:00"
    assert response["parse"]["time"]["end"] == "2026-11-15 23:59:59"


def test_scenario_c_no_result():
    """C：确实没数据 → no_data，answer=null、evidence=[]、带建议。"""
    _, example = _load("C")
    response = example["rounds"][-1]["response"]
    assert response["status"] == "no_data"
    assert response["answer"] is None
    assert response["evidence"] == []
    assert response["suggestions"]
    assert response["message"] == "群里没有找到相关资料"


# ---------------------------------------------------------------------------
# 引用可溯源（不编造）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_evidence_traceable_to_candidate_content(scenario):
    """top-level evidence 与 events.sources 的 quote 必须是候选原文子串。"""
    _, example = _load(scenario)
    contents = _all_candidate_contents(example)

    for round_ in example["rounds"]:
        response = round_["response"]
        for citation in response.get("evidence") or []:
            assert set(citation) >= {"msg_id", "quote", "field", "version"}
            assert citation["msg_id"] in contents, "引用 msg_id 不在候选中"
            assert citation["quote"] in contents[citation["msg_id"]], "引用不是原文子串"
            assert isinstance(citation["version"], int)

        answer = response.get("answer") or {}
        for event in answer.get("events") or []:
            for source in event.get("sources") or []:
                assert source["msg_id"] in contents
                assert source["quote"] in contents[source["msg_id"]]


# ---------------------------------------------------------------------------
# 文档一致性
# ---------------------------------------------------------------------------


def test_error_code_table_matches_errors_module():
    """文档错误码表必须与 app/errors.py 的数字码一一对应，且不再保留旧错误码。"""
    doc = (BACKEND / "docs" / "B_interface.md").read_text(encoding="utf-8")
    codes = {
        name: value
        for name, value in vars(errors).items()
        if name.isupper() and isinstance(value, int)
    }
    assert codes

    for name, code in codes.items():
        assert name in doc, f"文档缺少错误码常量名：{name}"
        assert str(code) in doc, f"文档缺少错误码：{code}"

    # 旧文档把 LLM 错误写成 3001/3002，已修正为 2001/2002，不允许回退
    assert "3002" not in doc
    assert "已核对" in doc, "文档需写明函数签名已与 facade.py 核对"


def test_doc_has_required_sections_and_example_links():
    """文档需包含四个数据结构、字段映射、权限、错误码章节，并引用三个示例。"""
    doc = (BACKEND / "docs" / "B_interface.md").read_text(encoding="utf-8")
    # 版本号随接口变更递增；这里只校验存在 v1.x 版本声明，不锁死具体版本
    assert re.search(r"版本：v1\.\d+", doc), "文档缺少版本声明"
    for keyword in ("Message", "Candidate", "Session", "Citation",
                    "字段映射", "权限", "错误码"):
        assert keyword in doc, f"文档缺少章节/关键词：{keyword}"

    for filename in EXAMPLES.values():
        assert filename in doc, f"文档未引用示例：{filename}"
