from __future__ import annotations

import argparse
import json
from pathlib import Path

from .schema import validate_question

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "public_eval_questions.json"


def merge_evalsets(input_paths: list[str | Path], output_path: str | Path = DEFAULT_OUTPUT) -> list[dict]:
    """Merge converted public-eval JSON files into one Contest Solver dataset."""
    merged: list[dict] = []
    seen_ids: set[str] = set()

    for input_path in input_paths:
        path = Path(input_path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("data", [data])
        if not isinstance(data, list):
            raise ValueError(f"{path} must contain a list or a dict with a data list")

        for record in data:
            normalized = validate_question(record)
            qid = normalized["question_id"]
            if qid in seen_ids:
                raise ValueError(f"duplicate question_id: {qid}")
            seen_ids.add(qid)
            merged.append(normalized)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge converted public evalset JSON files into Contest Solver schema."
    )
    parser.add_argument("inputs", nargs="+", help="Converted JSON files to merge")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output path. Default: contest_solver/data/public_eval_questions.json",
    )
    args = parser.parse_args()
    merged = merge_evalsets(args.inputs, args.output)
    print(f"merged {len(merged)} records -> {args.output}")


if __name__ == "__main__":
    main()
