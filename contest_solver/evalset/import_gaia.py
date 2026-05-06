from __future__ import annotations

from .schema import make_question


def convert_gaia_item(item: dict, idx: int) -> dict:
    """Convert one GAIA-style item to the Contest Solver schema."""
    question = item.get("Question") or item.get("question") or item.get("task") or ""
    answer = item.get("Final answer") or item.get("final_answer") or item.get("answer") or ""
    level = _normalize_level(item.get("Level") or item.get("level") or 3)
    return make_question(
        question_id=f"gaia_{idx:05d}",
        source="gaia",
        level=level,
        question_type="复杂工具任务",
        question=str(question),
        expected_answer=str(answer),
        expected_tools=["question_parser", "task_planner", "trace_recorder", "answer_verifier"],
        expected_trace_points=[
            "解析复杂任务目标",
            "规划需要的工具和步骤",
            "执行或模拟多步信息处理",
            "校验最终答案",
        ],
        metadata={
            "original_index": idx,
            "file_name": item.get("file_name") or item.get("File"),
            "raw_level": item.get("Level") or item.get("level"),
        },
    )


def _normalize_level(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 3
    return min(3, max(1, n))
