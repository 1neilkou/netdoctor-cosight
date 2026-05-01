from __future__ import annotations

from copy import deepcopy

SCHEMA_FIELDS = (
    "question_id",
    "source",
    "level",
    "question_type",
    "question",
    "expected_answer",
    "expected_tools",
    "expected_trace_points",
    "metadata",
)


def make_question(
    question_id: str,
    source: str,
    level: int,
    question_type: str,
    question: str,
    expected_answer: str,
    expected_tools: list[str] | None = None,
    expected_trace_points: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    """Build one Contest Solver public-eval question record."""
    return {
        "question_id": str(question_id),
        "source": str(source),
        "level": int(level),
        "question_type": str(question_type),
        "question": str(question or ""),
        "expected_answer": str(expected_answer or ""),
        "expected_tools": list(expected_tools or []),
        "expected_trace_points": list(expected_trace_points or []),
        "metadata": deepcopy(metadata or {}),
    }


def validate_question(record: dict) -> dict:
    """Return a normalized record or raise ValueError for missing schema fields."""
    missing = [field for field in SCHEMA_FIELDS if field not in record]
    if missing:
        raise ValueError(f"missing schema fields: {missing}")
    return make_question(
        question_id=record["question_id"],
        source=record["source"],
        level=record["level"],
        question_type=record["question_type"],
        question=record["question"],
        expected_answer=record["expected_answer"],
        expected_tools=record["expected_tools"],
        expected_trace_points=record["expected_trace_points"],
        metadata=record["metadata"],
    )
