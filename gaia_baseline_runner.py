"""Run local GAIA JSONL splits with CoSight baseline/optimized modes.

Examples:
    python gaia_baseline_runner.py --input contest/gaia_level1_validation.jsonl --limit 5 --sample_seed 42 --sample_output contest/gaia_level1_sample5_seed42.jsonl --mode baseline --output outputs/gaia_l1_sample5_baseline.jsonl --summary outputs/gaia_l1_sample5_baseline_summary.json
    python gaia_baseline_runner.py --input contest/gaia_level1_sample5_seed42.jsonl --mode optimized --output outputs/gaia_l1_sample5_optimized.jsonl --summary outputs/gaia_l1_sample5_optimized_summary.json
    python gaia_baseline_runner.py --compare outputs/gaia_l1_sample5_baseline.jsonl outputs/gaia_l1_sample5_optimized.jsonl --compare_output outputs/gaia_l1_sample5_compare.json
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import statistics
import time
import traceback
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

from extract_answer import extract_answer
from test_single import CoSight, build_llm


RESULTS_DIR = Path("results")
WORKSPACE_ROOT = Path("outputs/gaia_baseline_workspace")


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


def _normalize_answer(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t\r\n\"'`.,;:")


def _exact_match(prediction: str, gold: str) -> bool:
    return bool(gold) and _normalize_answer(prediction) == _normalize_answer(gold)


def _make_prompt(question: str) -> str:
    return (
        f"{question}\n\n"
        "Give the final answer as concisely as possible. "
        "At the end, include a line exactly in this format: FINAL ANSWER: <answer>."
    )


def _extract_final_answer(text: str) -> str:
    matches = re.findall(r"FINAL ANSWER\s*:\s*(.+)", text or "", flags=re.IGNORECASE)
    if matches:
        return matches[-1].strip()
    return (text or "").strip()


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


def _run_one(item: dict[str, Any], index: int, mode: str, args: argparse.Namespace) -> dict[str, Any]:
    task_id = _task_id(item, index)
    question = _question(item)
    gold = _gold_answer(item)
    workspace_path = (WORKSPACE_ROOT / mode / task_id).resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)

    previous_mode = os.environ.get("COSIGHT_EXPERIMENT_MODE")
    previous_task_id = os.environ.get("COSIGHT_TASK_ID")
    previous_options = {
        name: os.environ.get(name)
        for name in [
            "COSIGHT_MAX_RECENT_STEPS",
            "COSIGHT_MAX_SUMMARY_CHARS",
            "COSIGHT_MAX_RECENT_CHARS",
            "COSIGHT_DISABLE_COMPACT_OVERVIEW",
            "COSIGHT_DISABLE_KEY_VALUES",
            "COSIGHT_DISABLE_ARTIFACT_REFS",
        ]
    }
    os.environ["COSIGHT_EXPERIMENT_MODE"] = mode
    os.environ["COSIGHT_TASK_ID"] = task_id
    if mode == "optimized":
        max_recent_steps = 0 if args.disable_recent_context else args.max_recent_steps
        os.environ["COSIGHT_MAX_RECENT_STEPS"] = str(max_recent_steps)
        os.environ["COSIGHT_MAX_SUMMARY_CHARS"] = str(args.max_summary_chars)
        os.environ["COSIGHT_MAX_RECENT_CHARS"] = str(args.max_recent_chars)
        os.environ["COSIGHT_DISABLE_COMPACT_OVERVIEW"] = "1" if args.disable_compact_overview else "0"
        os.environ["COSIGHT_DISABLE_KEY_VALUES"] = "1" if args.disable_key_values else "0"
        os.environ["COSIGHT_DISABLE_ARTIFACT_REFS"] = "1" if args.disable_artifact_refs else "0"
    started = time.time()
    try:
        cosight = _build_cosight(f"gaia_{mode}_{task_id}", workspace_path)
        cosight.execute(_make_prompt(question))
    finally:
        if previous_mode is None:
            os.environ.pop("COSIGHT_EXPERIMENT_MODE", None)
        else:
            os.environ["COSIGHT_EXPERIMENT_MODE"] = previous_mode
        if previous_task_id is None:
            os.environ.pop("COSIGHT_TASK_ID", None)
        else:
            os.environ["COSIGHT_TASK_ID"] = previous_task_id
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

    return {
        "task_id": task_id,
        "mode": mode,
        "level": _first_text(item, ["Level", "level"]),
        "question": question,
        "prediction": final_answer,
        "raw_prediction": raw_answer,
        "gold_answer": gold,
        "exact_match": _exact_match(final_answer, gold),
        "elapsed_seconds": round(elapsed, 3),
        "steps_count": len(extracted.get("steps", []) or []),
        "tool_calls": tool_calls,
        "token_usage": usage["total"],
        "token_usage_by_role": usage["by_role"],
        "llm_calls": usage["records"],
        "actor_prompt_stats": actor_stats,
        "actor_prompt_breakdowns": actor_breakdowns,
        "avg_actor_prompt_chars": _avg_actor_prompt(actor_stats, "prompt_chars"),
        "avg_actor_prompt_est_tokens": _avg_actor_prompt(actor_stats, "prompt_est_tokens"),
        "avg_current_step_chars": _avg_actor_prompt(actor_breakdowns, "current_step_chars"),
        "avg_dependency_context_chars": _avg_actor_prompt(actor_breakdowns, "dependency_context_chars"),
        "avg_recent_context_chars": _avg_actor_prompt(actor_breakdowns, "recent_context_chars"),
        "avg_compact_plan_overview_chars": _avg_actor_prompt(actor_breakdowns, "compact_plan_overview_chars"),
        "avg_key_values_chars": _avg_actor_prompt(actor_breakdowns, "key_values_chars"),
        "avg_artifact_refs_chars": _avg_actor_prompt(actor_breakdowns, "artifact_refs_chars"),
        "workspace": str(workspace_path),
    }


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
    exact = sum(1 for r in records if r.get("exact_match"))

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
        "total_tokens": sum(tokens),
        "avg_tokens": avg(tokens),
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
        "avg_tool_calls": avg(tool_calls),
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
        "avg_tool_calls": summary["avg_tool_calls"],
        "avg_elapsed_seconds": summary["avg_elapsed_seconds"],
    }


def _pct_change(old: float, new: float) -> float | None:
    if old == 0:
        return None
    return round((new - old) / old * 100, 2)


def _load_success_records(path: Path) -> list[dict[str, Any]]:
    return [record for record in _load_jsonl(path) if "error" not in record]


def _compare_runs(baseline_path: Path, optimized_path: Path, compare_output: Path) -> dict[str, Any]:
    baseline_records = _load_success_records(baseline_path)
    optimized_records = _load_success_records(optimized_path)
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
        per_task.append(
            {
                "task_id": task_id,
                "baseline_exact_match": bool(b.get("exact_match")),
                "optimized_exact_match": bool(o.get("exact_match")),
                "baseline_total_tokens": b_tokens,
                "optimized_total_tokens": o_tokens,
                "token_change_pct": _pct_change(b_tokens, o_tokens),
                "baseline_actor_prompt_chars": b_prompt,
                "optimized_actor_prompt_chars": o_prompt,
                "prompt_chars_change_pct": _pct_change(b_prompt, o_prompt),
            }
        )

    compare = {
        "num_tasks": len(common_ids),
        "baseline": baseline_metrics,
        "optimized": optimized_metrics,
        "delta": {
            "accuracy_change": round(optimized_metrics["accuracy"] - baseline_metrics["accuracy"], 4),
            "total_tokens_change_pct": _pct_change(baseline_metrics["avg_total_tokens"], optimized_metrics["avg_total_tokens"]),
            "input_tokens_change_pct": _pct_change(baseline_metrics["avg_input_tokens"], optimized_metrics["avg_input_tokens"]),
            "prompt_chars_change_pct": _pct_change(baseline_metrics["avg_actor_prompt_chars"], optimized_metrics["avg_actor_prompt_chars"]),
            "tool_calls_change_pct": _pct_change(baseline_metrics["avg_tool_calls"], optimized_metrics["avg_tool_calls"]),
            "latency_change_pct": _pct_change(baseline_metrics["avg_elapsed_seconds"], optimized_metrics["avg_elapsed_seconds"]),
            "avg_total_tokens_change_pct": _pct_change(baseline_metrics["avg_total_tokens"], optimized_metrics["avg_total_tokens"]),
            "avg_input_tokens_change_pct": _pct_change(baseline_metrics["avg_input_tokens"], optimized_metrics["avg_input_tokens"]),
            "avg_actor_prompt_chars_change_pct": _pct_change(baseline_metrics["avg_actor_prompt_chars"], optimized_metrics["avg_actor_prompt_chars"]),
            "avg_tool_calls_change_pct": _pct_change(baseline_metrics["avg_tool_calls"], optimized_metrics["avg_tool_calls"]),
            "avg_elapsed_seconds_change_pct": _pct_change(baseline_metrics["avg_elapsed_seconds"], optimized_metrics["avg_elapsed_seconds"]),
        },
        "per_task": per_task,
    }
    compare_output.parent.mkdir(parents=True, exist_ok=True)
    compare_output.write_text(json.dumps(compare, ensure_ascii=False, indent=2), encoding="utf-8")
    return compare


def _run_dataset(args: argparse.Namespace) -> None:
    if load_dotenv:
        load_dotenv(Path(".env"), override=False)

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
            record = _run_one(item, index, args.mode, args)
            records.append(record)
            _append_jsonl(output_path, record)
            tokens = record["token_usage"]["total_tokens"]
            print(
                f"  exact={record['exact_match']} tokens={tokens} "
                f"actor_prompt_chars={record['avg_actor_prompt_chars']} "
                f"tools={record['tool_calls']} seconds={record['elapsed_seconds']}"
            )
        except Exception as exc:
            failure = {
                "task_id": task_id,
                "mode": args.mode,
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
    parser.add_argument("--mode", choices=["baseline", "optimized"], default="baseline")
    parser.add_argument("--max_recent_steps", type=int, default=1)
    parser.add_argument("--max_summary_chars", type=int, default=500)
    parser.add_argument("--max_recent_chars", type=int, default=300)
    parser.add_argument("--disable_recent_context", action="store_true")
    parser.add_argument("--disable_compact_overview", action="store_true")
    parser.add_argument("--disable_key_values", action="store_true")
    parser.add_argument("--disable_artifact_refs", action="store_true")
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
