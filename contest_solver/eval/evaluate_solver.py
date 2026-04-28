"""
评估框架占位实现。
后续扩展：接入 SolverPipeline + AnswerVerifier，对所有题目批量评分。
"""
from __future__ import annotations


def evaluate(results: list[dict]) -> dict:
    """
    对 SolverPipeline 输出的结果列表进行评估，返回汇总指标（占位）。

    Args:
        results: 每项为 answer_formatter 输出的字典，含 question_id / level /
                 final_answer / reasoning_trace。

    Returns:
        summary 字典，含 total / evaluated / placeholder 字段。
    """
    return {
        "total": len(results),
        "evaluated": 0,
        "accuracy": None,
        "trace_completeness": None,
        "note": "评估逻辑待实现，当前为占位返回",
    }


def score_trace(trace: list[dict], expected_points: list[str]) -> float:
    """计算解题轨迹覆盖期望要点的比例（占位，返回0.0）。"""
    return 0.0
