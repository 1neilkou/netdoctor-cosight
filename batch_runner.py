"""Batch-run Co-Sight on a small GAIA validation slice.

The script reads all credentials from environment variables, writes one JSON
record per task, and keeps running when an individual task fails.
"""

from __future__ import annotations

import json
import os
import time
import traceback
from pathlib import Path
from statistics import mean
from typing import Any

from extract_answer import extract_answer
from test_single import CoSight, build_llm


DATASET_NAME = "gaia-benchmark/GAIA"
DATASET_CONFIG = "2023_level1"
DATASET_SPLIT = "validation"
RESULTS_PATH = Path("results/gaia_results.jsonl")
WORKSPACE_ROOT = Path("outputs/gaia_batch_workspace")
MAX_ITEMS = 5
SLEEP_SECONDS = 2


def _configure_console() -> None:
    """Make Chinese output readable on Windows terminals."""
    if hasattr(os.sys.stdout, "reconfigure"):
        os.sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(os.sys.stderr, "reconfigure"):
        os.sys.stderr.reconfigure(encoding="utf-8")


def _get_hf_token() -> str:
    """Read the HuggingFace token from common environment variable names."""
    token = (
        os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGINGFACE_HUB_TOKEN")
        or os.environ.get("HUGGINGFACEHUB_API_TOKEN")
    )
    if not token:
        raise RuntimeError(
            "Missing HuggingFace token. Set HF_TOKEN, HUGGINGFACE_HUB_TOKEN, or HUGGINGFACEHUB_API_TOKEN."
        )
    return token


def _load_gaia_validation() -> list[dict[str, Any]]:
    """Load the first five GAIA level-1 validation samples from HuggingFace."""
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise RuntimeError("Missing optional dependency: datasets. Install project requirements before running.") from exc

    ds = load_dataset(DATASET_NAME, DATASET_CONFIG, split=DATASET_SPLIT, token=_get_hf_token())
    return [dict(item) for item in ds.select(range(min(MAX_ITEMS, len(ds))))]


def _first_text(item: dict[str, Any], keys: list[str], default: str = "") -> str:
    """Return the first non-empty text value for a list of possible GAIA field names."""
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return default


def _task_id(item: dict[str, Any], index: int) -> str:
    """Read GAIA task_id with a stable fallback."""
    return _first_text(item, ["task_id", "id", "Task ID"], default=f"idx_{index}")


def _question(item: dict[str, Any]) -> str:
    """Read GAIA question text across likely dataset field variants."""
    return _first_text(item, ["Question", "question", "query", "task"])


def _gold_answer(item: dict[str, Any]) -> str:
    """Read GAIA gold answer across likely dataset field variants."""
    return _first_text(item, ["Final answer", "final_answer", "answer", "gold_answer"])


def _normalize_for_exact_match(value: str) -> str:
    """Normalize text for a simple exact-match score."""
    return " ".join(str(value or "").strip().lower().split())


def _is_exact_match(final_answer: str, gold_answer: str) -> bool:
    """Compute exact match after lightweight whitespace and case normalization."""
    if not gold_answer:
        return False
    return _normalize_for_exact_match(final_answer) == _normalize_for_exact_match(gold_answer)


def _append_jsonl(record: dict[str, Any]) -> None:
    """Append one UTF-8 JSON line immediately after each task."""
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _make_prompt(question: str) -> str:
    """Build the per-task prompt with the requested answer constraint."""
    return f"{question}\n\n请给出明确的最终答案，用一句话。"


def _build_cosight(plan_id: str, workspace_path: Path) -> CoSight:
    """Create a CoSight instance using environment-backed LLM objects."""
    os.environ["WORKSPACE_PATH"] = str(workspace_path)
    return CoSight(
        build_llm("PLAN"),
        build_llm("ACT"),
        build_llm("TOOL"),
        build_llm("VISION"),
        work_space_path=str(workspace_path),
        message_uuid=plan_id,
    )


def _run_one(item: dict[str, Any], index: int) -> dict[str, Any]:
    """Run one GAIA sample and return a JSON-serializable result record."""
    task_id = _task_id(item, index)
    question = _question(item)
    gold_answer = _gold_answer(item)
    plan_id = f"gaia_{task_id}"
    workspace_path = (WORKSPACE_ROOT / plan_id).resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)

    if not question:
        raise RuntimeError(f"Task {task_id} has no question field.")

    cosight = _build_cosight(plan_id, workspace_path)
    cosight.execute(_make_prompt(question))
    extracted = extract_answer(cosight.plan)
    final_answer = extracted.get("final_answer", "")
    steps = extracted.get("steps", [])

    return {
        "task_id": task_id,
        "question": question,
        "final_answer": final_answer,
        "gold_answer": gold_answer,
        "answer_source": extracted.get("answer_source", ""),
        "steps_count": len(steps),
        "exact_match": _is_exact_match(final_answer, gold_answer),
    }


def main() -> None:
    """Run the first five GAIA validation tasks and print aggregate stats."""
    _configure_console()
    items = _load_gaia_validation()
    total = len(items)
    success_records = []
    failure_count = 0

    for index, item in enumerate(items):
        task_id = _task_id(item, index)
        print(f"\n[{index + 1}/{total}] Running GAIA task {task_id}")
        try:
            record = _run_one(item, index)
            success_records.append(record)
            print(f"  answer_source={record['answer_source']}, steps_count={record['steps_count']}")
            print(f"  exact_match={record['exact_match']}")
        except Exception as exc:
            failure_count += 1
            record = {
                "task_id": task_id,
                "question": _question(item),
                "final_answer": "",
                "gold_answer": _gold_answer(item),
                "answer_source": "",
                "steps_count": 0,
                "error": f"{type(exc).__name__}: {exc}",
            }
            print(f"  error={record['error']}")
            traceback.print_exc()
        _append_jsonl(record)
        if index < total - 1:
            time.sleep(SLEEP_SECONDS)

    exact_matches = sum(1 for record in success_records if record.get("exact_match"))
    accuracy = exact_matches / total if total else 0.0
    avg_steps = mean(record["steps_count"] for record in success_records) if success_records else 0.0

    print("\n=== GAIA batch summary ===")
    print(f"total: {total}")
    print(f"exact_match_accuracy: {accuracy:.3f} ({exact_matches}/{total})")
    print(f"average_steps: {avg_steps:.2f}")
    print(f"failures: {failure_count}")
    print(f"results_path: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
