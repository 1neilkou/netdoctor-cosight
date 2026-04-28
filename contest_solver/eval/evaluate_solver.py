"""
evaluate_solver.py — 批量评测模块

公开接口：
    evaluate_results(results: list[dict], questions: list[dict]) -> dict

统计指标：
    total_questions       : 总题数
    run_success_rate      : status == "success" 的比例
    schema_valid_rate     : verifier_result.is_valid == True 的比例
    trace_complete_rate   : reasoning_trace 步骤数 ≥ 2 的比例
    tool_call_success_rate: used_tools 非空的比例
    answer_match_rate     : final_answer 与 expected_answer 完全一致的比例
                            ⚠️  当前 pipeline 使用 expected_answer 作为占位答案，
                                answer_match_rate 预期为 1.0，仅用于验证流程完整性。
"""
from __future__ import annotations


def evaluate_results(results: list[dict], questions: list[dict]) -> dict:
    """
    对 solve_question() 的批量输出结果进行评测。

    Args:
        results:   list of solve_question() 返回值
        questions: list of sample_questions.json 原始记录

    Returns:
        包含 6 项指标的统计字典。
    """
    total = len(results)
    if total == 0:
        return {
            "total_questions":       0,
            "run_success_rate":      0.0,
            "schema_valid_rate":     0.0,
            "trace_complete_rate":   0.0,
            "tool_call_success_rate": 0.0,
            "answer_match_rate":     0.0,
            "note":                  "无结果可评测",
        }

    # 构建期望答案查找表
    expected: dict[str, str] = {
        q["question_id"]: q.get("expected_answer", "") for q in questions
    }

    run_success   = 0
    schema_valid  = 0
    trace_done    = 0
    tool_called   = 0
    ans_match     = 0

    by_level: dict[int, dict] = {}   # level → {total, matched}

    for r in results:
        qid     = r.get("question_id", "")
        level   = r.get("level", 0)
        status  = r.get("status", "")
        trace   = r.get("reasoning_trace", [])
        tools   = r.get("used_tools", [])
        answer  = r.get("final_answer", "").strip()
        exp_ans = expected.get(qid, "").strip()
        vr      = r.get("verifier_result", {})

        if status == "success":
            run_success += 1
        if vr.get("is_valid", False):
            schema_valid += 1
        if isinstance(trace, list) and len(trace) >= 2:
            trace_done += 1
        if isinstance(tools, list) and len(tools) > 0:
            tool_called += 1
        if answer == exp_ans and exp_ans:
            ans_match += 1

        # 按难度分层统计
        by_level.setdefault(level, {"total": 0, "matched": 0})
        by_level[level]["total"] += 1
        if answer == exp_ans and exp_ans:
            by_level[level]["matched"] += 1

    def rate(n: int) -> float:
        return round(n / total, 3)

    level_match_rates = {
        f"L{lv}_match_rate": round(d["matched"] / d["total"], 3)
        for lv, d in sorted(by_level.items())
        if d["total"] > 0
    }

    return {
        "total_questions":        total,
        "run_success_rate":       rate(run_success),
        "schema_valid_rate":      rate(schema_valid),
        "trace_complete_rate":    rate(trace_done),
        "tool_call_success_rate": rate(tool_called),
        "answer_match_rate":      rate(ans_match),
        **level_match_rates,
        "note": (
            "answer_match_rate = 1.0 属预期行为（当前 pipeline 使用 expected_answer 占位）。"
            if rate(ans_match) == 1.0 else ""
        ),
    }
