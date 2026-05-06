from __future__ import annotations

from .schema import make_question


def convert_drop_item(item: dict, idx: int) -> dict:
    """Convert one DROP-style item to the Contest Solver schema."""
    passage = item.get("passage") or item.get("context") or ""
    question = item.get("question") or item.get("query") or ""
    answer = _extract_drop_answer(item)
    full_question = f"{passage}\n\n问题：{question}" if passage else str(question)
    return make_question(
        question_id=f"drop_{idx:05d}",
        source="drop",
        level=2,
        question_type="材料数值推理",
        question=full_question,
        expected_answer=answer,
        expected_tools=["question_parser", "calculator_tool", "answer_verifier"],
        expected_trace_points=[
            "阅读材料并定位相关信息",
            "提取需要计算或比较的数值",
            "执行数值推理",
            "输出答案",
        ],
        metadata={"original_index": idx, "query_id": item.get("query_id")},
    )


def _extract_drop_answer(item: dict) -> str:
    for key in ("answer", "validated_answers", "answers_spans", "spans"):
        value = item.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list) and value:
            return str(value[0])
        if isinstance(value, dict):
            spans = value.get("spans")
            if isinstance(spans, list) and spans:
                return ", ".join(str(x) for x in spans)
            number = value.get("number")
            if number:
                return str(number)
            date = value.get("date")
            if isinstance(date, dict):
                parts = [date.get("day"), date.get("month"), date.get("year")]
                return " ".join(str(p) for p in parts if p)
    return ""
