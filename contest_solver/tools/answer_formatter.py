"""
answer_formatter.py — 答案格式化工具

公开接口（新）：
    format_answer(...) -> dict   ← 统一输出结构

向后兼容：
    AnswerFormatter 类（供旧版 solver_pipeline 使用）
"""
import json


def format_answer(
    question_id: str,
    level: "int | str",
    question_type: str,
    final_answer: str,
    confidence: float = 0.0,
    reasoning_trace: "list[dict] | None" = None,
    used_tools: "list[str] | None" = None,
    status: str = "success",
    error: "str | None" = None,
) -> dict:
    """
    返回统一的解题结果结构。

    {
        "question_id":    str,
        "level":          int | str,
        "question_type":  str,
        "final_answer":   str,
        "confidence":     float,   # 0.0 ~ 1.0
        "reasoning_trace": list[dict],
        "used_tools":     list[str],
        "status":         "success" | "partial" | "failed" | "error",
        "error":          str | None,
    }
    """
    return {
        "question_id":    question_id,
        "level":          level,
        "question_type":  question_type,
        "final_answer":   final_answer,
        "confidence":     round(max(0.0, min(1.0, confidence)), 4),
        "reasoning_trace": reasoning_trace if reasoning_trace is not None else [],
        "used_tools":     used_tools if used_tools is not None else [],
        "status":         status,
        "error":          error,
    }


# ---------------------------------------------------------------------------
# 向后兼容：保留 AnswerFormatter 类
# ---------------------------------------------------------------------------

class AnswerFormatter:
    """向后兼容包装器，供旧版 solver_pipeline 使用。"""

    def format(
        self,
        question_id: str,
        level: "int | str",
        final_answer: str,
        reasoning_trace: list,
    ) -> dict:
        return format_answer(
            question_id=question_id,
            level=level,
            question_type="",
            final_answer=final_answer,
            reasoning_trace=reasoning_trace,
        )

    def to_json(self, result: dict, indent: int = 2) -> str:
        return json.dumps(result, ensure_ascii=False, indent=indent)
