from __future__ import annotations

import re

from .schema import make_question


def convert_gsm8k_item(item: dict, idx: int) -> dict:
    """Convert one GSM8K-style item to the Contest Solver schema."""
    question = item.get("question") or item.get("input") or item.get("problem") or ""
    raw_answer = item.get("answer") or item.get("target") or item.get("final_answer") or ""
    expected_answer = _extract_gsm8k_answer(str(raw_answer))
    return make_question(
        question_id=f"gsm8k_{idx:05d}",
        source="gsm8k",
        level=1,
        question_type="简单计算",
        question=str(question),
        expected_answer=expected_answer,
        expected_tools=["calculator_tool", "answer_verifier"],
        expected_trace_points=[
            "读取题目中的数值和关系",
            "执行必要的算术计算",
            "输出最终数值答案",
        ],
        metadata={"original_index": idx, "raw_answer": raw_answer},
    )


def _extract_gsm8k_answer(answer: str) -> str:
    match = re.search(r"####\s*([^\n]+)", answer)
    if match:
        return match.group(1).strip()
    return answer.strip()
