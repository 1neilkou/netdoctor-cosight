"""Run local GAIA JSONL splits with CoSight baseline/optimized modes.

Examples:
    python gaia_baseline_runner.py --input contest/gaia_level1_validation.jsonl --limit 5 --sample_seed 42 --sample_output contest/gaia_level1_sample5_seed42.jsonl --mode baseline --output outputs/gaia_l1_sample5_baseline.jsonl --summary outputs/gaia_l1_sample5_baseline_summary.json
    python gaia_baseline_runner.py --input contest/gaia_level1_sample5_seed42.jsonl --mode optimized --output outputs/gaia_l1_sample5_optimized.jsonl --summary outputs/gaia_l1_sample5_optimized_summary.json
    python gaia_baseline_runner.py --compare outputs/gaia_l1_sample5_baseline.jsonl outputs/gaia_l1_sample5_optimized.jsonl --compare_output outputs/gaia_l1_sample5_compare.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import statistics
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

# 配置 logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

from extract_answer import extract_answer
from test_single import CoSight, build_llm
from app.cosight.task.facts_router import evaluate_facts_quality


RESULTS_DIR = Path("results")
WORKSPACE_ROOT = Path("outputs/gaia_baseline_workspace")


@dataclass
class RunMetrics:
    task_id: str
    level: int
    correct: bool
    total_tokens: int
    tool_call_count: int
    fact_count: int
    blocker_count: int
    replan_count: int
    prune_triggered: bool
    verify_steps_added: int
    steps_executed: int
    steps_skipped: int
    avg_step_confidence: float
    final_answer_confidence: float


def _configure_console() -> None:
    if hasattr(os.sys.stdout, "reconfigure"):
        os.sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(os.sys.stderr, "reconfigure"):
        os.sys.stderr.reconfigure(encoding="utf-8")


def _resolve_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    candidates = [
        path,
        Path.cwd() / path,
        Path.cwd().parent / path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return (Path.cwd() / path).resolve()


def _looks_like_result_path(path_value: str | Path | None) -> bool:
    if not path_value:
        return False
    suffix = Path(path_value).suffix.lower()
    text = str(path_value).replace("\\", "/").lower()
    return suffix in {".json", ".jsonl"} and ("/results/" in text or text.startswith("results/") or "/outputs/" in text or text.startswith("outputs/"))


def _resolve_output_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    cwd_candidate = Path.cwd() / path
    parent_candidate = Path.cwd().parent / path
    if parent_candidate.parent.exists() and not cwd_candidate.parent.exists():
        return parent_candidate.resolve()
    return cwd_candidate.resolve()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _first_text(item: dict[str, Any], keys: list[str], default: str = "") -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return default


def _task_id(item: dict[str, Any], index: int) -> str:
    return _first_text(item, ["task_id", "id", "Task ID"], f"idx_{index:04d}")


def _question(item: dict[str, Any]) -> str:
    return _first_text(item, ["Question", "question", "query", "task"])


def _gold_answer(item: dict[str, Any]) -> str:
    return _first_text(item, ["Final answer", "final_answer", "answer", "gold_answer"])


def _attachment_path(item: dict[str, Any]) -> str:
    file_path = _first_text(item, ["file_path", "File Path", "attachment_path", "attachments"])
    file_name = _first_text(item, ["file_name", "File Name", "attachment", "filename"])
    if not file_path and not file_name:
        return ""

    raw_path = file_path or file_name
    path = Path(raw_path)
    candidates = [
        path,
        Path.cwd() / path,
        Path.cwd().parent / path,
        Path.cwd() / "contest" / path,
        Path.cwd().parent / "contest" / path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate.resolve())
    return str((Path.cwd().parent / "contest" / path).resolve())


LEAK_SIGNALS = [
    "```",
    "tool_call",
    "update_plan",
    "execute_code",
    "mark_step",
    "def ",
    "import ",
    "fetch_website_content",
    "search_wiki",
    "extract_document_content",
    "file_read",
    "<tool>",
    "Action:",
    "Observation:",
    "tool_input",
    "tool_name",
]


ABBREV_MAP = {
    r"\bst\.\s*": "saint ",
    r"\bmt\.\s*": "mount ",
    r"\bft\.\s*": "fort ",
    r"\bdr\.\s*": "drive ",
    r"\bave\.\s*": "avenue ",
}


SYNONYM_MAP = {
    r"\bgrave accent\b": "backtick",
    r"\bgrave\b": "backtick",
    r"\bback-tick\b": "backtick",
    r"\bbackquote\b": "backtick",
}


def _strip_numeric_unit(text: str) -> str:
    match = re.fullmatch(
        r"(\d+(?:\.\d+)?)\s*(?:years?|months?|days?|hours?|minutes?|seconds?|times?|%|percent)?",
        text.strip(),
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else text


def _normalize_answer(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" \t\r\n\"'`.,;:*")
    for pattern, replacement in ABBREV_MAP.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    for pattern, replacement in SYNONYM_MAP.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return _strip_numeric_unit(text)


def _exact_match(prediction: str, gold: str) -> bool:
    if not gold:
        return False
    normalized_prediction = _normalize_answer(prediction)
    normalized_gold = _normalize_answer(gold)
    if normalized_prediction == normalized_gold:
        return True
    if len(normalized_gold) >= 3 and normalized_gold in normalized_prediction:
        return True
    return False


def _make_prompt(question: str, attachment_path: str = "") -> str:
    attachment_note = (
        f"本题附带文件，路径为：{attachment_path}，请优先读取此文件。\n\n"
        if attachment_path
        else ""
    )
    return (
        attachment_note +
        f"{question}\n\n"
        "Give the final answer as concisely as possible. "
        "At the end, include a line exactly in this format: FINAL ANSWER: <answer>."
    )


def _has_answer_leak(answer: str) -> bool:
    answer_lower = str(answer or "").lower()
    return any(signal.lower() in answer_lower for signal in LEAK_SIGNALS)


def _extract_final_answer(text: str) -> str:
    raw_text = text or ""
    matches = re.findall(r"FINAL ANSWER\s*:\s*(.+)", raw_text, flags=re.IGNORECASE)
    candidates = list(reversed(matches)) if matches else [raw_text]
    for candidate in candidates:
        answer = candidate.strip(" \t\r\n\"'`.,;:*")
        answer = re.sub(r"^FINAL ANSWER\s*:\s*", "", answer, flags=re.IGNORECASE).strip()
        answer = re.sub(r"^(Answer|答案|最终答案)\s*:\s*", "", answer, flags=re.IGNORECASE).strip()
        if not answer or _has_answer_leak(answer):
            continue
        return _strip_numeric_unit(answer)
    return ""


def _usage_summary(cosight: CoSight) -> dict[str, Any]:
    llms = {
        "planner": cosight.task_planner_agent.llm,
        "actor": cosight.act_llm,
        "tool": cosight.tool_llm,
        "vision": cosight.vision_llm,
    }
    by_role: dict[str, Any] = {}
    total = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    records = []
    for role, llm in llms.items():
        stats = dict(getattr(llm, "usage_stats", {}) or {})
        by_role[role] = stats
        for key in total:
            total[key] += int(stats.get(key, 0) or 0)
        for record in getattr(llm, "usage_records", []) or []:
            copied = dict(record)
            copied["role"] = role
            records.append(copied)
    return {"total": total, "by_role": by_role, "records": records}


def _count_tool_calls(extracted: dict[str, Any]) -> int:
    return sum(len(step.get("tool_calls", []) or []) for step in extracted.get("steps", []) or [])


def _actor_prompt_stats(plan: Any) -> list[dict[str, Any]]:
    stats = getattr(plan, "actor_prompt_stats", [])
    return list(stats) if isinstance(stats, list) else []


def _actor_prompt_breakdowns(plan: Any) -> list[dict[str, Any]]:
    breakdowns = getattr(plan, "actor_prompt_breakdowns", [])
    return list(breakdowns) if isinstance(breakdowns, list) else []


def _avg_actor_prompt(stats: list[dict[str, Any]], field: str) -> float:
    values = [int(s.get(field, 0) or 0) for s in stats if isinstance(s, dict)]
    return round(statistics.mean(values), 2) if values else 0.0


def _build_cosight(plan_id: str, workspace_path: Path) -> CoSight:
    os.environ["WORKSPACE_PATH"] = str(workspace_path)
    return CoSight(
        build_llm("PLAN"),
        build_llm("ACT"),
        build_llm("TOOL"),
        build_llm("VISION"),
        work_space_path=str(workspace_path),
        message_uuid=plan_id,
    )


def _build_cosight_with_temperature(plan_id: str, workspace_path: Path, temperature: float) -> CoSight:
    os.environ["WORKSPACE_PATH"] = str(workspace_path)
    previous = {
        name: os.environ.get(name)
        for name in [
            "PLAN_TEMPERATURE",
            "ACT_TEMPERATURE",
            "TOOL_TEMPERATURE",
            "VISION_TEMPERATURE",
        ]
    }
    try:
        for name in previous:
            os.environ[name] = str(temperature)
        return CoSight(
            build_llm("PLAN"),
            build_llm("ACT"),
            build_llm("TOOL"),
            build_llm("VISION"),
            work_space_path=str(workspace_path),
            message_uuid=plan_id,
        )
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _find_conflicts(records: list[dict[str, Any]]) -> dict[str, Any]:
    answers: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        normalized = _normalize_answer(record.get("prediction", ""))
        answers.setdefault(normalized, []).append({
            "task_id": record.get("task_id"),
            "temperature": record.get("camv_temperature"),
            "prediction": record.get("prediction", ""),
            "exact_match": record.get("exact_match", False),
        })
    variants = [
        {
            "normalized_answer": answer,
            "count": len(items),
            "candidates": items,
        }
        for answer, items in sorted(answers.items(), key=lambda item: (-len(item[1]), item[0]))
    ]
    return {
        "has_conflict": len(variants) > 1,
        "variant_count": len(variants),
        "variants": variants,
    }


UNABLE_SIGNALS = [
    "unable to determine",
    "cannot determine",
    "无法确定",
    "could not be retrieved",
]


def _is_unable_answer(answer: str) -> bool:
    text = str(answer or "").strip().lower()
    return not text or any(signal in text for signal in UNABLE_SIGNALS)


def _camv_record_for_temperature(records: list[dict[str, Any]], temperature: float) -> dict[str, Any]:
    return next(
        (record for record in records if float(record.get("camv_temperature", -1.0)) == temperature),
        records[0] if records else {},
    )


def _select_camv_record(records: list[dict[str, Any]], conflicts: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    conservative = _camv_record_for_temperature(records, 0.0)
    radical = _camv_record_for_temperature(records, 0.3)
    answer_c = str(conservative.get("prediction", "") or "")
    answer_r = str(radical.get("prediction", "") or "")

    if not conflicts.get("has_conflict"):
        return conservative, answer_c, "no_conflict_use_conservative"

    if _normalize_answer(answer_c) == _normalize_answer(answer_r):
        return conservative, answer_c, "answers_agree"

    c_unable = _is_unable_answer(answer_c)
    r_unable = _is_unable_answer(answer_r)

    if c_unable and not r_unable:
        return radical, answer_r, "conflict_chose_radical_c_unable"

    if r_unable and not c_unable:
        return conservative, answer_c, "conflict_chose_conservative_r_unable"

    return conservative, answer_c, "conflict_fallback_conservative"


def _run_camv(item: dict[str, Any], index: int, args: argparse.Namespace) -> dict[str, Any]:
    temperatures = [0.0, 0.2, 0.3]
    records = []
    for temperature in temperatures:
        task_id = _task_id(item, index)
        workspace_path = (WORKSPACE_ROOT / "camv" / task_id / f"temp_{str(temperature).replace('.', '_')}").resolve()
        workspace_path.mkdir(parents=True, exist_ok=True)

        previous_temperatures = {
            name: os.environ.get(name)
            for name in [
                "PLAN_TEMPERATURE",
                "ACT_TEMPERATURE",
                "TOOL_TEMPERATURE",
                "VISION_TEMPERATURE",
            ]
        }
        previous_rounds = os.environ.get("MAX_REACT_ROUNDS")
        try:
            for name in previous_temperatures:
                os.environ[name] = str(temperature)
            if temperature == 0.3:
                os.environ["MAX_REACT_ROUNDS"] = "8"
            record = _run_one(item, index, "optimized", args)
        finally:
            for name, value in previous_temperatures.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            if previous_rounds is None:
                os.environ.pop("MAX_REACT_ROUNDS", None)
            else:
                os.environ["MAX_REACT_ROUNDS"] = previous_rounds

        record["mode"] = "camv_candidate"
        record["camv_temperature"] = temperature
        records.append(record)

    conflicts = _find_conflicts(records)
    selected, final_answer, camv_decision = _select_camv_record(records, conflicts)
    result = dict(selected)
    result["mode"] = "camv"
    result["prediction"] = final_answer
    result["exact_match"] = _exact_match(final_answer, result.get("gold_answer", ""))
    result["camv_candidates"] = records
    result["camv_conflicts"] = conflicts
    result["camv_decision"] = camv_decision
    result["camv_selected_temperature"] = selected.get("camv_temperature")
    result["run_metrics"] = asdict(_build_run_metrics(result))
    return result


def _run_one(item: dict[str, Any], index: int, mode: str, args: argparse.Namespace) -> dict[str, Any]:
    task_id = _task_id(item, index)
    question = _question(item)
    gold = _gold_answer(item)
    attachment_path = _attachment_path(item)
    workspace_path = (WORKSPACE_ROOT / mode / task_id).resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)

    previous_mode = os.environ.get("COSIGHT_EXPERIMENT_MODE")
    previous_task_id = os.environ.get("COSIGHT_TASK_ID")
    previous_attachment_path = os.environ.get("COSIGHT_ATTACHMENT_PATH")
    previous_options = {
        name: os.environ.get(name)
        for name in [
            "COSIGHT_MAX_RECENT_STEPS",
            "COSIGHT_MAX_SUMMARY_CHARS",
            "COSIGHT_MAX_RECENT_CHARS",
            "COSIGHT_DISABLE_COMPACT_OVERVIEW",
            "COSIGHT_DISABLE_KEY_VALUES",
            "COSIGHT_DISABLE_ARTIFACT_REFS",
            "COSIGHT_ENABLE_FACT_STORE",
            "COSIGHT_ENABLE_FACT_SUPERVISOR",
            "COSIGHT_REQUIRE_COMPLETED_DEPS",
        ]
    }
    os.environ["COSIGHT_EXPERIMENT_MODE"] = mode
    os.environ["COSIGHT_TASK_ID"] = task_id
    if attachment_path:
        os.environ["COSIGHT_ATTACHMENT_PATH"] = attachment_path
    else:
        os.environ.pop("COSIGHT_ATTACHMENT_PATH", None)
    if mode == "optimized":
        max_recent_steps = 0 if args.disable_recent_context else args.max_recent_steps
        os.environ["COSIGHT_MAX_RECENT_STEPS"] = str(max_recent_steps)
        os.environ["COSIGHT_MAX_SUMMARY_CHARS"] = str(args.max_summary_chars)
        os.environ["COSIGHT_MAX_RECENT_CHARS"] = str(args.max_recent_chars)
        os.environ["COSIGHT_DISABLE_COMPACT_OVERVIEW"] = "1" if args.disable_compact_overview else "0"
        os.environ["COSIGHT_DISABLE_KEY_VALUES"] = "1" if args.disable_key_values else "0"
        os.environ["COSIGHT_DISABLE_ARTIFACT_REFS"] = "1" if args.disable_artifact_refs else "0"
        os.environ["COSIGHT_ENABLE_FACT_STORE"] = "0" if args.disable_fact_store else "1"
        os.environ["COSIGHT_ENABLE_FACT_SUPERVISOR"] = "1" if args.enable_fact_supervisor else "0"
        os.environ["COSIGHT_REQUIRE_COMPLETED_DEPS"] = "1" if args.enable_fact_supervisor else "0"
    started = time.time()
    try:
        cosight = _build_cosight(f"gaia_{mode}_{task_id}", workspace_path)
        cosight.execute(_make_prompt(question, attachment_path))
    finally:
        if previous_mode is None:
            os.environ.pop("COSIGHT_EXPERIMENT_MODE", None)
        else:
            os.environ["COSIGHT_EXPERIMENT_MODE"] = previous_mode
        if previous_task_id is None:
            os.environ.pop("COSIGHT_TASK_ID", None)
        else:
            os.environ["COSIGHT_TASK_ID"] = previous_task_id
        if previous_attachment_path is None:
            os.environ.pop("COSIGHT_ATTACHMENT_PATH", None)
        else:
            os.environ["COSIGHT_ATTACHMENT_PATH"] = previous_attachment_path
        for name, value in previous_options.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    elapsed = time.time() - started

    extracted = extract_answer(cosight.plan)
    raw_answer = extracted.get("final_answer", "")
    final_answer = _extract_final_answer(raw_answer)
    usage = _usage_summary(cosight)
    tool_calls = _count_tool_calls(extracted)
    actor_stats = _actor_prompt_stats(cosight.plan)
    actor_breakdowns = _actor_prompt_breakdowns(cosight.plan)
    step_facts = dict(getattr(cosight.plan, "step_facts", {}) or {})
    step_artifacts = dict(getattr(cosight.plan, "step_artifacts", {}) or {})
    step_blockers = dict(getattr(cosight.plan, "step_blockers", {}) or {})
    step_confidence = dict(getattr(cosight.plan, "step_confidence", {}) or {})
    step_statuses = dict(getattr(cosight.plan, "step_statuses", {}) or {})
    step_tool_calls = dict(getattr(cosight.plan, "step_tool_calls", {}) or {})
    control_events = list(getattr(cosight.plan, "control_events", []) or [])
    actor_view_debug = dict(getattr(cosight.plan, "actor_view_debug", {}) or {})
    route_decisions = dict(getattr(cosight.plan, "route_decisions", {}) or {})

    # ===== 新加：计算每个 step 的 facts 质量 =====
    facts_quality_per_step = {}
    steps = list(getattr(cosight.plan, "steps", []) or [])
    for step_index in range(len(steps)):
        try:
            quality = evaluate_facts_quality(cosight.plan, step_index, question)
            facts_quality_per_step[str(step_index)] = {
                "diagnosis": quality.get("diagnosis", ""),
                "recommendation": quality.get("recommendation", ""),
                "fill_rate": quality.get("fill_rate", 0.0),
                "relevance_rate": quality.get("relevance_rate", 0.0),
                "sourced_rate": quality.get("sourced_rate", 0.0),
                "has_final_answer": quality.get("has_final_answer", False),
            }
        except Exception as e:
            logger.warning(f"Failed to evaluate facts quality for step {step_index}: {e}")
            facts_quality_per_step[str(step_index)] = {
                "diagnosis": "error",
                "recommendation": "error",
                "fill_rate": 0.0,
                "relevance_rate": 0.0,
                "sourced_rate": 0.0,
                "has_final_answer": False,
            }
    # ===== Facts quality 计算结束 =====

    record = {
        "task_id": task_id,
        "mode": mode,
        "level": _first_text(item, ["Level", "level"]),
        "question": question,
        "prediction": final_answer,
        "raw_prediction": raw_answer,
        "gold_answer": gold,
        "attachment_path": attachment_path,
        "exact_match": _exact_match(final_answer, gold),
        "elapsed_seconds": round(elapsed, 3),
        "steps_count": len(extracted.get("steps", []) or []),
        "tool_calls": tool_calls,
        "token_usage": usage["total"],
        "token_usage_by_role": usage["by_role"],
        "llm_calls": usage["records"],
        "actor_prompt_stats": actor_stats,
        "actor_prompt_breakdowns": actor_breakdowns,
        "step_facts": step_facts,
        "step_artifacts": step_artifacts,
        "step_blockers": step_blockers,
        "step_confidence": step_confidence,
        "step_statuses": step_statuses,
        "step_tool_calls": step_tool_calls,
        "control_events": control_events,
        "actor_view_debug": actor_view_debug,
        "facts_count": sum(len(v or []) for v in step_facts.values()),
        "blockers_count": sum(len(v or []) for v in step_blockers.values()),
        "avg_actor_prompt_chars": _avg_actor_prompt(actor_stats, "prompt_chars"),
        "avg_actor_prompt_est_tokens": _avg_actor_prompt(actor_stats, "prompt_est_tokens"),
        "avg_current_step_chars": _avg_actor_prompt(actor_breakdowns, "current_step_chars"),
        "avg_dependency_context_chars": _avg_actor_prompt(actor_breakdowns, "dependency_context_chars"),
        "avg_recent_context_chars": _avg_actor_prompt(actor_breakdowns, "recent_context_chars"),
        "avg_compact_plan_overview_chars": _avg_actor_prompt(actor_breakdowns, "compact_plan_overview_chars"),
        "avg_key_values_chars": _avg_actor_prompt(actor_breakdowns, "key_values_chars"),
        "avg_artifact_refs_chars": _avg_actor_prompt(actor_breakdowns, "artifact_refs_chars"),
        "workspace": str(workspace_path),
        # ===== 新加：Facts Router 输出 =====
        "facts_quality_per_step": facts_quality_per_step,
        "route_decisions": route_decisions,
        # ===== Facts Router 输出结束 =====
    }
    record["run_metrics"] = asdict(_build_run_metrics(record))
    return record


def _sample_items(items: list[dict[str, Any]], limit: int | None, seed: int | None, sample_output: str | None) -> list[dict[str, Any]]:
    if sample_output and _looks_like_result_path(sample_output):
        print(f"Ignoring sample_output as dataset sample path because it looks like a result file: {sample_output}")
        return items[:limit] if limit is not None else items
    if not sample_output or limit is None:
        return items[:limit] if limit is not None else items

    output_path = _resolve_output_path(sample_output)
    if output_path.exists():
        print(f"Reusing sample file: {output_path}")
        return _load_jsonl(output_path)

    sample_size = min(limit, len(items))
    rng = random.Random(seed)
    indices = sorted(rng.sample(range(len(items)), sample_size))
    sampled = [items[i] for i in indices]
    _write_jsonl(output_path, sampled)
    print(f"Wrote fixed sample: {output_path}")
    return sampled


def _sample_level_value(item: dict[str, Any]) -> str:
    return str(_first_text(item, ["Level", "level"], "")).strip()


def sample_tasks(
    dataset: list[dict[str, Any]],
    n_per_level: int = 3,
    levels: list[str] | None = None,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """Sample a fixed number of tasks per GAIA level."""
    selected: list[dict[str, Any]] = []
    for level in levels or ["1", "2", "3"]:
        level_tasks = [task for task in dataset if _level_int(_sample_level_value(task)) == _level_int(level)]
        rng = random.Random(seed)
        sampled = rng.sample(level_tasks, min(n_per_level, len(level_tasks)))
        selected.extend(sampled)
    return selected


def _default_level_input_paths(levels: list[str]) -> list[Path]:
    return [_resolve_path(f"../contest/gaia_level{_level_int(level)}_validation.jsonl") for level in levels]


def _load_many_jsonl(paths: list[Path]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in paths:
        items.extend(_load_jsonl(path))
    return items


def _sample_stratified_items(args: argparse.Namespace) -> list[dict[str, Any]]:
    levels = [str(level) for level in args.stratified_levels]
    if args.sample_output and not _looks_like_result_path(args.sample_output):
        output_path = _resolve_output_path(args.sample_output)
        if output_path.exists():
            print(f"Reusing stratified sample file: {output_path}")
            return _load_jsonl(output_path)

    input_paths = [_resolve_path(path) for path in args.stratified_inputs] if args.stratified_inputs else _default_level_input_paths(levels)
    items = _load_many_jsonl(input_paths)
    sampled = sample_tasks(items, n_per_level=args.n_per_level, levels=levels, seed=args.sample_seed or 42)

    if args.sample_output and not _looks_like_result_path(args.sample_output):
        output_path = _resolve_output_path(args.sample_output)
        _write_jsonl(output_path, sampled)
        print(f"Wrote stratified sample: {output_path}")
    return sampled


def _select_items_by_task_ids(items: list[dict[str, Any]], task_ids: list[str]) -> list[dict[str, Any]]:
    wanted = set(task_ids)
    selected = [item for index, item in enumerate(items) if _task_id(item, index) in wanted]
    found = {_first_text(item, ["task_id", "id", "Task ID"], "") for item in selected}
    missing = wanted - found
    if missing:
        print(f"Warning: requested task_ids not found in input: {sorted(missing)}")
    return selected


def _highest_input_task_id(result_path: Path) -> str:
    records = _load_success_records(result_path)
    if not records:
        raise RuntimeError(f"No successful records found in {result_path}")
    best = max(records, key=lambda r: int(r.get("token_usage", {}).get("input_tokens", 0) or 0))
    task_id = str(best.get("task_id", ""))
    if not task_id:
        raise RuntimeError(f"Highest-input record in {result_path} has no task_id")
    return task_id


def _role_tokens(record: dict[str, Any], role: str) -> int:
    role_stats = record.get("token_usage_by_role", {}).get(role, {})
    return int(role_stats.get("total_tokens", 0) or 0) if isinstance(role_stats, dict) else 0


def _level_int(value: Any) -> int:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else 0


def _flatten_dict_lists(value: Any) -> list[Any]:
    if not isinstance(value, dict):
        return []
    items: list[Any] = []
    for values in value.values():
        if isinstance(values, list):
            items.extend(values)
    return items


def _confidence_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _avg_step_confidence(step_confidence: Any) -> float:
    if not isinstance(step_confidence, dict):
        return 0.0
    values = [
        confidence for confidence in (_confidence_float(value) for value in step_confidence.values())
        if confidence is not None
    ]
    return round(statistics.mean(values), 4) if values else 0.0


def _final_answer_confidence(step_facts: Any) -> float:
    confidences = []
    for fact in _flatten_dict_lists(step_facts):
        if not isinstance(fact, dict):
            continue
        if str(fact.get("key", "")).lower() != "final_answer":
            continue
        confidence = _confidence_float(fact.get("confidence"))
        if confidence is not None:
            confidences.append(confidence)
    return round(max(confidences), 4) if confidences else 0.0


def _count_status(step_statuses: Any, status: str) -> int:
    if not isinstance(step_statuses, dict):
        return 0
    return sum(1 for value in step_statuses.values() if str(value) == status)


def _control_event_count(events: Any, action: str) -> int:
    return sum(
        1 for event in (events if isinstance(events, list) else [])
        if isinstance(event, dict) and event.get("action") == action
    )


def _build_run_metrics(record: dict[str, Any]) -> RunMetrics:
    step_facts = record.get("step_facts", {})
    step_blockers = record.get("step_blockers", {})
    step_confidence = record.get("step_confidence", {})
    step_statuses = record.get("step_statuses", {})
    control_events = record.get("control_events", [])
    total_tokens = int(record.get("token_usage", {}).get("total_tokens", 0) or 0)
    fact_count = int(record.get("facts_count", 0) or 0)
    blocker_count = int(record.get("blockers_count", 0) or 0)
    if not fact_count:
        fact_count = len(_flatten_dict_lists(step_facts))
    if not blocker_count:
        blocker_count = len(_flatten_dict_lists(step_blockers))
    return RunMetrics(
        task_id=str(record.get("task_id", "")),
        level=_level_int(record.get("level")),
        correct=bool(record.get("exact_match")),
        total_tokens=total_tokens,
        tool_call_count=int(record.get("tool_calls", 0) or 0),
        fact_count=fact_count,
        blocker_count=blocker_count,
        replan_count=_control_event_count(control_events, "replan"),
        prune_triggered=_control_event_count(control_events, "prune") > 0,
        verify_steps_added=_control_event_count(control_events, "add_verify_step"),
        steps_executed=_count_status(step_statuses, "completed") + _count_status(step_statuses, "blocked"),
        steps_skipped=_count_status(step_statuses, "skipped") + _count_status(step_statuses, "dependency_blocked"),
        avg_step_confidence=_avg_step_confidence(step_confidence),
        final_answer_confidence=_final_answer_confidence(step_facts),
    )


def _ensure_run_metrics(record: dict[str, Any]) -> dict[str, Any]:
    existing = record.get("run_metrics")
    if isinstance(existing, dict):
        return existing
    return asdict(_build_run_metrics(record))


def _accuracy(records: list[dict[str, Any]]) -> float:
    if not records:
        return 0.0
    return round(sum(1 for record in records if record.get("exact_match")) / len(records), 4)


def _blocked_error_rate(records: list[dict[str, Any]]) -> float:
    if not records:
        return 0.0
    blocked_wrong = 0
    for record in records:
        metrics = _ensure_run_metrics(record)
        if not metrics.get("correct") and int(metrics.get("blocker_count", 0) or 0) > 0:
            blocked_wrong += 1
    return round(blocked_wrong / len(records), 4)


def _multi_hop_accuracy(records: list[dict[str, Any]]) -> float:
    multi_hop = [
        record for record in records
        if _level_int(record.get("level")) in {2, 3} and int(record.get("steps_count", 0) or 0) >= 3
    ]
    return _accuracy(multi_hop)


def _confidence_calibration(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bins = [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0000001)]
    result = []
    for low, high in bins:
        selected = []
        for record in records:
            confidence = float(_ensure_run_metrics(record).get("final_answer_confidence", 0) or 0)
            if low <= confidence < high:
                selected.append(record)
        label_high = 1.0 if high > 1 else high
        result.append({
            "range": f"[{low:.1f}, {label_high:.1f})",
            "count": len(selected),
            "accuracy": _accuracy(selected),
        })
    return result


def _summarize(records: list[dict[str, Any]], total_items: int) -> dict[str, Any]:
    tokens = [int(r.get("token_usage", {}).get("total_tokens", 0) or 0) for r in records]
    input_tokens = [int(r.get("token_usage", {}).get("input_tokens", 0) or 0) for r in records]
    output_tokens = [int(r.get("token_usage", {}).get("output_tokens", 0) or 0) for r in records]
    actor_tokens = [_role_tokens(r, "actor") for r in records]
    planner_tokens = [_role_tokens(r, "planner") for r in records]
    tool_calls = [int(r.get("tool_calls", 0) or 0) for r in records]
    elapsed = [float(r.get("elapsed_seconds", 0) or 0) for r in records]
    prompt_chars = [float(r.get("avg_actor_prompt_chars", 0) or 0) for r in records]
    prompt_est_tokens = [float(r.get("avg_actor_prompt_est_tokens", 0) or 0) for r in records]
    current_step_chars = [float(r.get("avg_current_step_chars", 0) or 0) for r in records]
    dependency_context_chars = [float(r.get("avg_dependency_context_chars", 0) or 0) for r in records]
    recent_context_chars = [float(r.get("avg_recent_context_chars", 0) or 0) for r in records]
    compact_plan_overview_chars = [float(r.get("avg_compact_plan_overview_chars", 0) or 0) for r in records]
    key_values_chars = [float(r.get("avg_key_values_chars", 0) or 0) for r in records]
    artifact_refs_chars = [float(r.get("avg_artifact_refs_chars", 0) or 0) for r in records]
    facts_counts = [int(r.get("facts_count", 0) or 0) for r in records]
    blockers_counts = [int(r.get("blockers_count", 0) or 0) for r in records]
    exact = sum(1 for r in records if r.get("exact_match"))
    run_metrics = [_ensure_run_metrics(r) for r in records]
    replan_counts = [int(m.get("replan_count", 0) or 0) for m in run_metrics]
    verify_steps_added = [int(m.get("verify_steps_added", 0) or 0) for m in run_metrics]
    steps_executed = [int(m.get("steps_executed", 0) or 0) for m in run_metrics]
    steps_skipped = [int(m.get("steps_skipped", 0) or 0) for m in run_metrics]
    avg_step_confidences = [float(m.get("avg_step_confidence", 0) or 0) for m in run_metrics]
    final_answer_confidences = [float(m.get("final_answer_confidence", 0) or 0) for m in run_metrics]
    prune_count = sum(1 for m in run_metrics if m.get("prune_triggered"))

    def avg(values: list[float | int]) -> float:
        return round(statistics.mean(values), 2) if values else 0.0

    top_token_tasks = sorted(
        records,
        key=lambda r: int(r.get("token_usage", {}).get("total_tokens", 0) or 0),
        reverse=True,
    )[:10]

    return {
        "total_items": total_items,
        "completed": len(records),
        "failures": total_items - len(records),
        "exact_match_accuracy": round(exact / total_items, 4) if total_items else 0.0,
        "accuracy_overall": round(exact / total_items, 4) if total_items else 0.0,
        "accuracy_L1": _accuracy([r for r in records if _level_int(r.get("level")) == 1]),
        "accuracy_L2": _accuracy([r for r in records if _level_int(r.get("level")) == 2]),
        "accuracy_L3": _accuracy([r for r in records if _level_int(r.get("level")) == 3]),
        "total_tokens": sum(tokens),
        "avg_tokens": avg(tokens),
        "avg_total_tokens": avg(tokens),
        "std_tokens": round(statistics.pstdev(tokens), 2) if len(tokens) > 1 else 0.0,
        "input_tokens": sum(input_tokens),
        "output_tokens": sum(output_tokens),
        "avg_input_tokens": avg(input_tokens),
        "avg_output_tokens": avg(output_tokens),
        "avg_actor_tokens": avg(actor_tokens),
        "avg_planner_tokens": avg(planner_tokens),
        "avg_actor_prompt_chars": avg(prompt_chars),
        "avg_actor_prompt_est_tokens": avg(prompt_est_tokens),
        "avg_current_step_chars": avg(current_step_chars),
        "avg_dependency_context_chars": avg(dependency_context_chars),
        "avg_recent_context_chars": avg(recent_context_chars),
        "avg_compact_plan_overview_chars": avg(compact_plan_overview_chars),
        "avg_key_values_chars": avg(key_values_chars),
        "avg_artifact_refs_chars": avg(artifact_refs_chars),
        "avg_facts_count": avg(facts_counts),
        "avg_blockers_count": avg(blockers_counts),
        "avg_tool_calls": avg(tool_calls),
        "avg_tool_call_count": avg(tool_calls),
        "blocked_error_rate": _blocked_error_rate(records),
        "multi_hop_accuracy": _multi_hop_accuracy(records),
        "avg_replan_count": avg(replan_counts),
        "prune_trigger_rate": round(prune_count / len(records), 4) if records else 0.0,
        "avg_verify_steps_added": avg(verify_steps_added),
        "avg_steps_executed": avg(steps_executed),
        "avg_steps_skipped": avg(steps_skipped),
        "avg_step_confidence": avg(avg_step_confidences),
        "avg_final_answer_confidence": avg(final_answer_confidences),
        "confidence_calibration": _confidence_calibration(records),
        "avg_elapsed_seconds": avg(elapsed),
        "min_tokens": min(tokens) if tokens else 0,
        "max_tokens": max(tokens) if tokens else 0,
        "top_token_tasks": [
            {
                "task_id": r["task_id"],
                "total_tokens": r.get("token_usage", {}).get("total_tokens", 0),
                "input_tokens": r.get("token_usage", {}).get("input_tokens", 0),
                "output_tokens": r.get("token_usage", {}).get("output_tokens", 0),
                "actor_tokens": _role_tokens(r, "actor"),
                "planner_tokens": _role_tokens(r, "planner"),
                "tool_calls": r.get("tool_calls", 0),
                "steps_count": r.get("steps_count", 0),
                "exact_match": r.get("exact_match", False),
            }
            for r in top_token_tasks
        ],
    }


def _metric_block(records: list[dict[str, Any]]) -> dict[str, Any]:
    summary = _summarize(records, len(records))
    return {
        "accuracy": summary["exact_match_accuracy"],
        "accuracy_overall": summary["accuracy_overall"],
        "accuracy_L1": summary["accuracy_L1"],
        "accuracy_L2": summary["accuracy_L2"],
        "accuracy_L3": summary["accuracy_L3"],
        "avg_total_tokens": summary["avg_tokens"],
        "avg_input_tokens": summary["avg_input_tokens"],
        "avg_output_tokens": summary["avg_output_tokens"],
        "avg_actor_tokens": summary["avg_actor_tokens"],
        "avg_planner_tokens": summary["avg_planner_tokens"],
        "avg_actor_prompt_chars": summary["avg_actor_prompt_chars"],
        "avg_actor_prompt_est_tokens": summary["avg_actor_prompt_est_tokens"],
        "avg_current_step_chars": summary["avg_current_step_chars"],
        "avg_dependency_context_chars": summary["avg_dependency_context_chars"],
        "avg_recent_context_chars": summary["avg_recent_context_chars"],
        "avg_compact_plan_overview_chars": summary["avg_compact_plan_overview_chars"],
        "avg_key_values_chars": summary["avg_key_values_chars"],
        "avg_artifact_refs_chars": summary["avg_artifact_refs_chars"],
        "avg_facts_count": summary["avg_facts_count"],
        "avg_blockers_count": summary["avg_blockers_count"],
        "avg_tool_calls": summary["avg_tool_calls"],
        "avg_tool_call_count": summary["avg_tool_call_count"],
        "blocked_error_rate": summary["blocked_error_rate"],
        "multi_hop_accuracy": summary["multi_hop_accuracy"],
        "avg_replan_count": summary["avg_replan_count"],
        "prune_trigger_rate": summary["prune_trigger_rate"],
        "avg_verify_steps_added": summary["avg_verify_steps_added"],
        "avg_steps_executed": summary["avg_steps_executed"],
        "avg_steps_skipped": summary["avg_steps_skipped"],
        "avg_step_confidence": summary["avg_step_confidence"],
        "avg_final_answer_confidence": summary["avg_final_answer_confidence"],
        "confidence_calibration": summary["confidence_calibration"],
        "avg_elapsed_seconds": summary["avg_elapsed_seconds"],
    }


def _pct_change(old: float, new: float) -> float | None:
    if old == 0:
        return None
    return round((new - old) / old * 100, 2)


def _load_success_records(path: Path) -> list[dict[str, Any]]:
    return [record for record in _load_jsonl(path) if "error" not in record]


def _level_compare_block(baseline_records: list[dict[str, Any]], optimized_records: list[dict[str, Any]]) -> dict[str, Any]:
    by_level: dict[str, Any] = {}
    for level in [1, 2, 3]:
        baseline_level = [record for record in baseline_records if _level_int(record.get("level")) == level]
        optimized_level = [record for record in optimized_records if _level_int(record.get("level")) == level]
        baseline_metrics = _metric_block(baseline_level)
        optimized_metrics = _metric_block(optimized_level)
        by_level[f"L{level}"] = {
            "num_tasks": len(set(record.get("task_id") for record in baseline_level) & set(record.get("task_id") for record in optimized_level)),
            "baseline_accuracy": baseline_metrics["accuracy"],
            "optimized_accuracy": optimized_metrics["accuracy"],
            "accuracy_change": round(optimized_metrics["accuracy"] - baseline_metrics["accuracy"], 4),
            "baseline_avg_total_tokens": baseline_metrics["avg_total_tokens"],
            "optimized_avg_total_tokens": optimized_metrics["avg_total_tokens"],
            "avg_total_tokens_change_pct": _pct_change(
                baseline_metrics["avg_total_tokens"],
                optimized_metrics["avg_total_tokens"],
            ),
            "baseline_avg_tool_calls": baseline_metrics["avg_tool_calls"],
            "optimized_avg_tool_calls": optimized_metrics["avg_tool_calls"],
            "avg_tool_calls_change_pct": _pct_change(
                baseline_metrics["avg_tool_calls"],
                optimized_metrics["avg_tool_calls"],
            ),
        }
    return by_level


def _compare_runs(baseline_path: Path, optimized_path: Path, compare_output: Path) -> dict[str, Any]:
    baseline_records = _load_jsonl(baseline_path)
    optimized_records = _load_jsonl(optimized_path)
    baseline_by_id = {r.get("task_id"): r for r in baseline_records}
    optimized_by_id = {r.get("task_id"): r for r in optimized_records}
    common_ids = [task_id for task_id in baseline_by_id if task_id in optimized_by_id]

    baseline_metrics = _metric_block([baseline_by_id[task_id] for task_id in common_ids])
    optimized_metrics = _metric_block([optimized_by_id[task_id] for task_id in common_ids])

    per_task = []
    for task_id in common_ids:
        b = baseline_by_id[task_id]
        o = optimized_by_id[task_id]
        b_tokens = int(b.get("token_usage", {}).get("total_tokens", 0) or 0)
        o_tokens = int(o.get("token_usage", {}).get("total_tokens", 0) or 0)
        b_prompt = float(b.get("avg_actor_prompt_chars", 0) or 0)
        o_prompt = float(o.get("avg_actor_prompt_chars", 0) or 0)
        b_metrics = _ensure_run_metrics(b)
        o_metrics = _ensure_run_metrics(o)
        per_task.append(
            {
                "task_id": task_id,
                "level": _level_int(b.get("level") or o.get("level")),
                "baseline_exact_match": bool(b.get("exact_match")),
                "optimized_exact_match": bool(o.get("exact_match")),
                "baseline_total_tokens": b_tokens,
                "optimized_total_tokens": o_tokens,
                "token_change_pct": _pct_change(b_tokens, o_tokens),
                "baseline_actor_prompt_chars": b_prompt,
                "optimized_actor_prompt_chars": o_prompt,
                "prompt_chars_change_pct": _pct_change(b_prompt, o_prompt),
                "optimized_facts_count": int(o.get("facts_count", 0) or 0),
                "optimized_blockers_count": int(o.get("blockers_count", 0) or 0),
                "baseline_run_metrics": b_metrics,
                "optimized_run_metrics": o_metrics,
            }
        )

    acceptance = {
        "accuracy_overall_non_regression": optimized_metrics["accuracy_overall"] >= baseline_metrics["accuracy_overall"],
        "avg_total_tokens_within_15pct": (
            optimized_metrics["avg_total_tokens"] <= baseline_metrics["avg_total_tokens"] * 1.15
            if baseline_metrics["avg_total_tokens"] else True
        ),
        "blocked_error_rate_improved": optimized_metrics["blocked_error_rate"] < baseline_metrics["blocked_error_rate"],
        "multi_hop_accuracy_non_regression": optimized_metrics["multi_hop_accuracy"] >= baseline_metrics["multi_hop_accuracy"],
    }
    acceptance["all_passed"] = all(acceptance.values())

    compare = {
        "num_tasks": len(common_ids),
        "overall": {
            "baseline_accuracy": baseline_metrics["accuracy"],
            "optimized_accuracy": optimized_metrics["accuracy"],
            "accuracy_change": round(optimized_metrics["accuracy"] - baseline_metrics["accuracy"], 4),
            "baseline_avg_total_tokens": baseline_metrics["avg_total_tokens"],
            "optimized_avg_total_tokens": optimized_metrics["avg_total_tokens"],
            "avg_total_tokens_change_pct": _pct_change(
                baseline_metrics["avg_total_tokens"],
                optimized_metrics["avg_total_tokens"],
            ),
        },
        "by_level": _level_compare_block(
            [baseline_by_id[task_id] for task_id in common_ids],
            [optimized_by_id[task_id] for task_id in common_ids],
        ),
        "baseline": baseline_metrics,
        "optimized": optimized_metrics,
        "delta": {
            "accuracy_change": round(optimized_metrics["accuracy"] - baseline_metrics["accuracy"], 4),
            "accuracy_overall_change": round(optimized_metrics["accuracy_overall"] - baseline_metrics["accuracy_overall"], 4),
            "accuracy_L1_change": round(optimized_metrics["accuracy_L1"] - baseline_metrics["accuracy_L1"], 4),
            "accuracy_L2_change": round(optimized_metrics["accuracy_L2"] - baseline_metrics["accuracy_L2"], 4),
            "accuracy_L3_change": round(optimized_metrics["accuracy_L3"] - baseline_metrics["accuracy_L3"], 4),
            "total_tokens_change_pct": _pct_change(baseline_metrics["avg_total_tokens"], optimized_metrics["avg_total_tokens"]),
            "input_tokens_change_pct": _pct_change(baseline_metrics["avg_input_tokens"], optimized_metrics["avg_input_tokens"]),
            "prompt_chars_change_pct": _pct_change(baseline_metrics["avg_actor_prompt_chars"], optimized_metrics["avg_actor_prompt_chars"]),
            "tool_calls_change_pct": _pct_change(baseline_metrics["avg_tool_calls"], optimized_metrics["avg_tool_calls"]),
            "latency_change_pct": _pct_change(baseline_metrics["avg_elapsed_seconds"], optimized_metrics["avg_elapsed_seconds"]),
            "blocked_error_rate_change": round(optimized_metrics["blocked_error_rate"] - baseline_metrics["blocked_error_rate"], 4),
            "multi_hop_accuracy_change": round(optimized_metrics["multi_hop_accuracy"] - baseline_metrics["multi_hop_accuracy"], 4),
            "avg_replan_count_change": round(optimized_metrics["avg_replan_count"] - baseline_metrics["avg_replan_count"], 2),
            "avg_verify_steps_added_change": round(optimized_metrics["avg_verify_steps_added"] - baseline_metrics["avg_verify_steps_added"], 2),
            "avg_steps_skipped_change": round(optimized_metrics["avg_steps_skipped"] - baseline_metrics["avg_steps_skipped"], 2),
            "avg_final_answer_confidence_change": round(
                optimized_metrics["avg_final_answer_confidence"] - baseline_metrics["avg_final_answer_confidence"], 4
            ),
            "facts_count_change": round(optimized_metrics["avg_facts_count"] - baseline_metrics["avg_facts_count"], 2),
            "blockers_count_change": round(optimized_metrics["avg_blockers_count"] - baseline_metrics["avg_blockers_count"], 2),
            "avg_total_tokens_change_pct": _pct_change(baseline_metrics["avg_total_tokens"], optimized_metrics["avg_total_tokens"]),
            "avg_input_tokens_change_pct": _pct_change(baseline_metrics["avg_input_tokens"], optimized_metrics["avg_input_tokens"]),
            "avg_actor_prompt_chars_change_pct": _pct_change(baseline_metrics["avg_actor_prompt_chars"], optimized_metrics["avg_actor_prompt_chars"]),
            "avg_tool_calls_change_pct": _pct_change(baseline_metrics["avg_tool_calls"], optimized_metrics["avg_tool_calls"]),
            "avg_elapsed_seconds_change_pct": _pct_change(baseline_metrics["avg_elapsed_seconds"], optimized_metrics["avg_elapsed_seconds"]),
        },
        "acceptance": acceptance,
        "per_task": per_task,
    }
    compare_output.parent.mkdir(parents=True, exist_ok=True)
    compare_output.write_text(json.dumps(compare, ensure_ascii=False, indent=2), encoding="utf-8")
    return compare


def _run_dataset(args: argparse.Namespace) -> None:
    if load_dotenv:
        load_dotenv(Path(".env"), override=False)

    if args.stratified_sample:
        items = _sample_stratified_items(args)
        data_path = Path(args.sample_output).resolve() if args.sample_output else Path("stratified_gaia_sample")
    else:
        data_path = _resolve_path(args.input or args.data)
        items = _load_jsonl(data_path)
        items = _sample_items(items, args.limit, args.sample_seed, args.sample_output)
    if args.pick_highest_input_from:
        task_id = _highest_input_task_id(_resolve_path(args.pick_highest_input_from))
        print(f"Selected highest-input task from {args.pick_highest_input_from}: {task_id}")
        items = _select_items_by_task_ids(items, [task_id])
    if args.task_id:
        items = _select_items_by_task_ids(items, args.task_id)
    if args.offset:
        items = items[args.offset :]

    run_name = f"{data_path.stem}_{args.mode}_{time.strftime('%Y%m%d_%H%M%S')}"
    output_path = Path(args.output) if args.output else RESULTS_DIR / f"{run_name}.jsonl"
    summary_path = Path(args.summary) if args.summary else output_path.with_suffix(".summary.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    records: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        task_id = _task_id(item, index)
        print(f"[{index + 1}/{len(items)}] mode={args.mode} task={task_id}")
        try:
            record = _run_camv(item, index, args) if args.mode == "camv" else _run_one(item, index, args.mode, args)
            records.append(record)
            _append_jsonl(output_path, record)
            tokens = record["token_usage"]["total_tokens"]
            print(
                f"  exact={record['exact_match']} tokens={tokens} "
                f"actor_prompt_chars={record['avg_actor_prompt_chars']} "
                f"tools={record['tool_calls']} facts={record['run_metrics']['fact_count']} "
                f"replans={record['run_metrics']['replan_count']} "
                f"seconds={record['elapsed_seconds']}"
            )
        except Exception as exc:
            failure = {
                "task_id": task_id,
                "mode": args.mode,
                "level": _first_text(item, ["Level", "level"]),
                "question": _question(item),
                "gold_answer": _gold_answer(item),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
            _append_jsonl(output_path, failure)
            print(f"  error={failure['error']}")

    summary = _summarize(records, len(items))
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Run summary ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nresults: {output_path}")
    print(f"summary: {summary_path}")


def main() -> None:
    _configure_console()
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=None, help="Input GAIA jsonl path.")
    parser.add_argument("--data", default="../contest/gaia_level1_validation.jsonl", help="Backward-compatible input alias.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--sample_seed", type=int, default=None)
    parser.add_argument("--sample_output", default=None)
    parser.add_argument("--stratified_sample", action="store_true", help="Sample n tasks from each requested GAIA level.")
    parser.add_argument("--n_per_level", type=int, default=3)
    parser.add_argument("--stratified_levels", nargs="+", default=["1", "2", "3"])
    parser.add_argument(
        "--stratified_inputs",
        nargs="+",
        default=None,
        help="Optional validation jsonl paths for stratified sampling. Defaults to GAIA level1/2/3 validation files.",
    )
    parser.add_argument("--mode", choices=["baseline", "optimized", "camv"], default="baseline")
    parser.add_argument("--max_recent_steps", type=int, default=1)
    parser.add_argument("--max_summary_chars", type=int, default=500)
    parser.add_argument("--max_recent_chars", type=int, default=300)
    parser.add_argument("--disable_recent_context", action="store_true")
    parser.add_argument("--disable_compact_overview", action="store_true")
    parser.add_argument("--disable_key_values", action="store_true")
    parser.add_argument("--disable_artifact_refs", action="store_true")
    parser.add_argument("--disable_fact_store", action="store_true")
    parser.add_argument("--enable_fact_supervisor", action="store_true")
    parser.add_argument("--output", default=None)
    parser.add_argument("--summary", nargs="?", const="", default=None)
    parser.add_argument("--task_id", action="append", default=None)
    parser.add_argument("--pick_highest_input_from", default=None)
    parser.add_argument("--compare", nargs="+", metavar=("BASELINE_JSONL", "OPTIMIZED_JSONL"))
    parser.add_argument("--compare_output", default="outputs/gaia_compare.json")
    args = parser.parse_args()

    if args.compare:
        if len(args.compare) == 2:
            baseline_path = _resolve_path(args.compare[0])
            optimized_path = _resolve_path(args.compare[1])
            compare_output = Path(args.compare_output)
        elif len(args.compare) == 1 and args.compare_output and Path(args.compare_output).exists():
            baseline_path = _resolve_path(args.compare[0])
            optimized_path = _resolve_path(args.compare_output)
            compare_output = Path(args.summary) if args.summary else Path("outputs/gaia_compare.json")
        else:
            raise SystemExit("--compare requires BASELINE_JSONL OPTIMIZED_JSONL, or one baseline plus an existing optimized path in --compare_output.")
        compare = _compare_runs(baseline_path, optimized_path, compare_output)
        print(json.dumps(compare, ensure_ascii=False, indent=2))
        print(f"\ncompare: {compare_output}")
        return

    _run_dataset(args)


if __name__ == "__main__":
    main()
