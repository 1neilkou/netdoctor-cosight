# Public Eval Import Framework

This directory contains hand-written miniature examples and notes for importing public evaluation datasets into the Contest Solver schema.

No data is downloaded by this repository. Put locally obtained public-eval samples in your own working directory, convert them with the adapters under `contest_solver/evalset/`, then merge converted JSON files with `merge_evalsets.py`.

## Unified Schema

Each converted item uses these fields:

- `question_id`
- `source`
- `level`
- `question_type`
- `question`
- `expected_answer`
- `expected_tools`
- `expected_trace_points`
- `metadata`

## Recommended Public Evaluation Sets

These datasets are useful for supplementing generalization tests:

- GAIA
- HotpotQA
- GSM8K
- DROP
- MuSiQue
- LongBench

They are intended to broaden coverage across arithmetic reasoning, multi-hop QA, reading comprehension, numerical reasoning, and complex tool-use tasks. They do not replace official regional contest questions, domain-specific ZTE Co-Sight tasks, or locally audited contest samples.

## Adapter Modules

- `import_gsm8k.py`: math word problems -> `简单计算`
- `import_hotpotqa.py`: multi-hop QA -> `多跳问答`
- `import_drop.py`: passage numerical reasoning -> `材料数值推理`
- `import_musique.py`: multi-hop decomposed QA -> `多跳问答`
- `import_gaia.py`: complex tool tasks -> `复杂工具任务`

## Example Merge

After converting one or more local files into the unified schema:

```bash
python -m contest_solver.evalset.merge_evalsets converted_gsm8k.json converted_hotpotqa.json
```

The default output is:

```text
contest_solver/data/public_eval_questions.json
```

`sample_public_eval_questions.json` is not real public-eval data. It contains five tiny hand-written records only for schema and pipeline smoke tests.
