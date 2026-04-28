"""
evaluate_solver.py — 批量评测模块

公开接口：
    evaluate_results(results: list[dict], questions: list[dict]) -> dict

统计指标：
    total_questions        : 总题数
    run_success_rate       : status == "success" 的比例
    schema_valid_rate      : verifier_result.is_valid == True 的比例
    trace_complete_rate    : reasoning_trace 步骤数 ≥ 2 的比例
    tool_call_success_rate : used_tools 非空的比例
    answer_match_rate      : final_answer 与 expected_answer 完全一致的比例（alias: exact_match_rate）
                             ⚠️  占位模式下预期为 1.0，LLM 模式下反映与参考答案的精确匹配率
    exact_match_rate       : 同 answer_match_rate（grading_result.exact_match 汇总）
    normalized_match_rate  : grading_result.normalized_match == True 的比例
    average_semantic_score : grading_result.semantic_score 的平均值
    json_field_coverage_rate: JSON 类题型的平均语义得分
    numeric_match_rate     : 数值类题型的平均语义得分
    llm_answer_success_rate: answer_source == "llm" 且 final_answer 非空的比例
    llm_answer_source_rate : answer_source == "llm" 的比例
    partial_rate           : status == "partial" 的比例
"""
from __future__ import annotations
import re

_JSON_TYPES    = {"JSON格式转换", "文本信息抽取"}
_NUMERIC_TYPES = {"简单计算", "表格排序", "材料问答"}


def evaluate_results(results: list[dict], questions: list[dict]) -> dict:
    """
    对 solve_question() 的批量输出结果进行评测。

    Args:
        results:   list of solve_question() 返回值
        questions: list of sample_questions.json 原始记录

    Returns:
        包含各项指标的统计字典。
    """
    total = len(results)
    if total == 0:
        return {
            "total_questions":          0,
            "run_success_rate":         0.0,
            "schema_valid_rate":        0.0,
            "trace_complete_rate":      0.0,
            "tool_call_success_rate":   0.0,
            "answer_match_rate":        0.0,
            "exact_match_rate":         0.0,
            "normalized_match_rate":    0.0,
            "average_semantic_score":   0.0,
            "json_field_coverage_rate": 0.0,
            "numeric_match_rate":       0.0,
            "llm_answer_success_rate":  0.0,
            "llm_answer_source_rate":   0.0,
            "partial_rate":             0.0,
            "expected_tools_coverage_rate": 0.0,
            "expected_trace_points_coverage_rate": 0.0,
            "executed_tools_rate":       0.0,
            "note":                     "无结果可评测",
        }

    expected: dict[str, str] = {
        q["question_id"]: q.get("expected_answer", "") for q in questions
    }
    question_by_id: dict[str, dict] = {q["question_id"]: q for q in questions}

    run_success     = 0
    schema_valid    = 0
    trace_done      = 0
    tool_called     = 0
    ans_match       = 0
    llm_ans_success = 0
    llm_ans_source  = 0
    partial_count   = 0
    expected_tools_covered = 0
    expected_tools_total   = 0
    trace_points_covered   = 0
    trace_points_total     = 0
    routed_tools_total     = 0
    executed_routed_total  = 0

    exact_match_count    = 0
    normalized_match_cnt = 0
    semantic_scores:     list[float] = []
    json_scores:         list[float] = []
    numeric_scores:      list[float] = []

    by_level: dict[int, dict] = {}

    for r in results:
        qid     = r.get("question_id", "")
        level   = r.get("level", 0)
        qtype   = r.get("question_type", "")
        status  = r.get("status", "")
        trace   = r.get("reasoning_trace", [])
        tools   = r.get("used_tools", [])
        answer  = r.get("final_answer", "").strip()
        exp_ans = expected.get(qid, "").strip()
        vr      = r.get("verifier_result", {})
        ans_src = r.get("answer_source", "placeholder")
        gr      = r.get("grading_result", {})
        routed_tools = r.get("routed_tools", tools)
        executed_tools = r.get("executed_tools", [])
        q_item = question_by_id.get(qid, {})

        if status == "success":
            run_success += 1
        if status == "partial":
            partial_count += 1
        if vr.get("is_valid", False):
            schema_valid += 1
        if isinstance(trace, list) and len(trace) >= 2:
            trace_done += 1
        if isinstance(tools, list) and len(tools) > 0:
            tool_called += 1
        if answer == exp_ans and exp_ans:
            ans_match += 1
        if ans_src == "llm":
            llm_ans_source += 1
            if answer:
                llm_ans_success += 1

        expected_tools = q_item.get("expected_tools", [])
        if expected_tools:
            expected_tools_total += 1
            if set(expected_tools).issubset(set(executed_tools)):
                expected_tools_covered += 1

        expected_trace_points = q_item.get("expected_trace_points", [])
        if expected_trace_points:
            trace_text = _trace_to_text(trace)
            for point in expected_trace_points:
                trace_points_total += 1
                if _trace_point_covered(point, trace_text):
                    trace_points_covered += 1

        if isinstance(routed_tools, list):
            routed_tools_total += len(routed_tools)
            executed_routed_total += len(set(routed_tools) & set(executed_tools))

        # 语义评测指标（来自 grading_result）
        if gr:
            if gr.get("exact_match", False):
                exact_match_count += 1
            if gr.get("normalized_match", False):
                normalized_match_cnt += 1
            sem_score = gr.get("semantic_score", 0.0)
            semantic_scores.append(sem_score)
            if qtype in _JSON_TYPES:
                json_scores.append(sem_score)
            if qtype in _NUMERIC_TYPES:
                numeric_scores.append(sem_score)

        by_level.setdefault(level, {"total": 0, "matched": 0})
        by_level[level]["total"] += 1
        if answer == exp_ans and exp_ans:
            by_level[level]["matched"] += 1

    def rate(n: int) -> float:
        return round(n / total, 3)

    def avg(lst: list[float]) -> float:
        return round(sum(lst) / len(lst), 3) if lst else 0.0

    level_match_rates = {
        f"L{lv}_match_rate": round(d["matched"] / d["total"], 3)
        for lv, d in sorted(by_level.items())
        if d["total"] > 0
    }

    ans_match_r = rate(ans_match)
    if ans_match_r == 1.0 and llm_ans_source == 0:
        note = "answer_match_rate = 1.0 属预期行为（当前 pipeline 使用 expected_answer 占位）。"
    elif llm_ans_source > 0:
        note = (
            f"LLM 答案模式：{llm_ans_source}/{total} 题由 LLM 生成答案，"
            f"answer_match_rate 反映 LLM 答案与参考答案的完全匹配率，仅供本地参考。"
        )
    else:
        note = ""

    return {
        "total_questions":          total,
        "run_success_rate":         rate(run_success),
        "schema_valid_rate":        rate(schema_valid),
        "trace_complete_rate":      rate(trace_done),
        "tool_call_success_rate":   rate(tool_called),
        "answer_match_rate":        ans_match_r,
        "exact_match_rate":         rate(exact_match_count) if semantic_scores else ans_match_r,
        "normalized_match_rate":    rate(normalized_match_cnt) if semantic_scores else 0.0,
        "average_semantic_score":   avg(semantic_scores),
        "json_field_coverage_rate": avg(json_scores),
        "numeric_match_rate":       avg(numeric_scores),
        "llm_answer_success_rate":  rate(llm_ans_success),
        "llm_answer_source_rate":   rate(llm_ans_source),
        "partial_rate":             rate(partial_count),
        "expected_tools_coverage_rate": (
            round(expected_tools_covered / expected_tools_total, 3)
            if expected_tools_total else 0.0
        ),
        "expected_trace_points_coverage_rate": (
            round(trace_points_covered / trace_points_total, 3)
            if trace_points_total else 0.0
        ),
        "executed_tools_rate": (
            round(executed_routed_total / routed_tools_total, 3)
            if routed_tools_total else 0.0
        ),
        **level_match_rates,
        "note": note,
    }


def _trace_to_text(trace: list) -> str:
    parts: list[str] = []
    for step in trace if isinstance(trace, list) else []:
        if not isinstance(step, dict):
            parts.append(str(step))
            continue
        parts.append(str(step.get("action", "")))
        parts.append(str(step.get("tool", "")))
        parts.append(str(step.get("input_summary", "")))
        parts.append(str(step.get("observation", "")))
    return " ".join(parts)


def _trace_point_covered(point: str, trace_text: str) -> bool:
    tokens = _key_tokens(point)
    if not tokens:
        return point[:12] in trace_text
    hits = sum(1 for token in tokens if token in trace_text)
    return hits / len(tokens) >= 0.5


def _key_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    tokens.extend(re.findall(r"-?\d+(?:\.\d+)?", text))
    tokens.extend(re.findall(r"CELL_[A-Z0-9_]+|DAS_[A-Z0-9_]+|PRB_UL|RSRP|A3|CIO", text))
    tokens.extend(re.findall(r"[A-Za-z_]{2,}", text))
    tokens.extend(re.findall(r"[\u4e00-\u9fff]{2,4}", text))
    seen: set[str] = set()
    unique: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            unique.append(token)
    return unique[:12]
