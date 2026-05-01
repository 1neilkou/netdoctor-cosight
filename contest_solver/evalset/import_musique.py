from __future__ import annotations

from .schema import make_question


def convert_musique_item(item: dict, idx: int) -> dict:
    """Convert one MuSiQue-style item to the Contest Solver schema."""
    question = item.get("question") or item.get("query") or ""
    answer = item.get("answer") or item.get("expected_answer") or ""
    paragraphs = item.get("paragraphs") or item.get("contexts") or []
    full_question = _append_paragraphs(str(question), paragraphs)
    return make_question(
        question_id=f"musique_{idx:05d}",
        source="musique",
        level=2,
        question_type="多跳问答",
        question=full_question,
        expected_answer=str(answer),
        expected_tools=["question_parser", "trace_recorder", "answer_verifier"],
        expected_trace_points=[
            "解析多跳问题",
            "筛选相关段落",
            "组合中间答案",
            "输出最终答案",
        ],
        metadata={
            "original_index": idx,
            "id": item.get("id"),
            "answer_aliases": item.get("answer_aliases", []),
        },
    )


def _append_paragraphs(question: str, paragraphs) -> str:
    if not paragraphs:
        return question
    return f"{question}\n\n【Paragraphs】\n{paragraphs}"
