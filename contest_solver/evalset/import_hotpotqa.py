from __future__ import annotations

from .schema import make_question


def convert_hotpotqa_item(item: dict, idx: int) -> dict:
    """Convert one HotpotQA-style item to the Contest Solver schema."""
    question = item.get("question") or item.get("query") or ""
    answer = item.get("answer") or item.get("expected_answer") or ""
    context = item.get("context")
    full_question = _with_context(str(question), context)
    return make_question(
        question_id=f"hotpotqa_{idx:05d}",
        source="hotpotqa",
        level=2,
        question_type="多跳问答",
        question=full_question,
        expected_answer=str(answer),
        expected_tools=["question_parser", "trace_recorder", "answer_verifier"],
        expected_trace_points=[
            "解析问题目标",
            "定位多个支撑事实",
            "跨事实链路推理",
            "输出最终答案",
        ],
        metadata={
            "original_index": idx,
            "supporting_facts": item.get("supporting_facts", []),
            "level": item.get("level"),
            "type": item.get("type"),
        },
    )


def _with_context(question: str, context) -> str:
    if not context:
        return question
    if isinstance(context, str):
        return f"{question}\n\n【Context】\n{context}"
    return f"{question}\n\n【Context】\n{context}"
