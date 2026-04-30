# CoSight 二次开发说明：GAIA 通信效率优化实验

## 项目目标

本项目基于 ZTE 开源 CoSight multi-agent 框架做二次开发，目标不是重写框架，而是在原有 Planner / Actor / Tool 执行链路上做可观测、可回滚的小步优化。

当前实验聚焦 GAIA Benchmark 场景下的 Actor 上下文膨胀问题：

- 保留原生 CoSight 作为 baseline。
- 新增 optimized 模式，只影响 Actor prompt/view 构造。
- 记录每题准确率、token、工具调用次数、耗时和 Actor prompt 分段长度。
- 用固定 5 题样本做 before/after 对比，保证实验可复现。

## 主要改动

### 1. GAIA 评测 Runner

文件：

- `gaia_baseline_runner.py`
- `eval/gaia_baseline_runner.py`

能力：

- 支持 `baseline` / `optimized` 双模式。
- 支持固定 seed 抽样和复用样本。
- 记录每题结果到 JSONL。
- 汇总 accuracy、total tokens、actor/planner tokens、tool calls、elapsed seconds。
- 支持 `--compare` 生成 baseline/optimized 对比报告。
- 支持 optimized 模式下的上下文裁剪调参。

新增 optimized 参数：

```bash
--max_recent_steps
--max_summary_chars
--max_recent_chars
--disable_recent_context
--disable_compact_overview
--disable_key_values
--disable_artifact_refs
```

### 2. Actor View 压缩

文件：

- `app/cosight/state/actor_view.py`
- `app/cosight/agent/actor/actor_view.py`

optimized 模式不再直接把完整 `Plan.format()` 传给 Actor，而是构建结构化 `actor_view`：

- `current_step`
- `dependency_summaries`
- `recent_completed_summaries`
- `compact_plan_overview`
- `failed_or_blocked_steps`
- `artifact_refs`
- `key_values`
- `tool_call_brief`

设计原则：

- 不传完整 `step_notes`。
- 不传完整 `tool_calls`。
- 不读取 artifact 文件内容，只传路径引用。
- 从历史 step notes 中用简单正则抽取短 key-value 信息。
- 所有字段使用 `getattr` / defensive parsing，避免 Plan 字段缺失导致崩溃。

### 3. Optimized Actor Prompt

文件：

- `app/cosight/agent/actor/prompt/actor_prompt.py`
- `app/cosight/agent/actor/task_actor_agent.py`

新增 `actor_execute_task_prompt_v2()`，只在 `COSIGHT_EXPERIMENT_MODE=optimized` 时启用。

prompt_v2 保留：

- 原始 question
- current step
- dependency summaries
- compact plan overview
- recent summaries
- key values
- artifact refs
- workspace path

去掉了原版 prompt 中较长的通用规则和完整 plan 文本，目标是减少 Actor 每次 LLM call 的输入长度。

### 4. Actor Prompt 可观测性

文件：

- `app/cosight/agent/base/base_agent.py`
- `gaia_baseline_runner.py`

每次 Actor 调用 LLM 前打印：

```text
[ACTOR_PROMPT]
[ACTOR_PROMPT_BREAKDOWN]
```

结果 JSONL 中新增：

- `actor_prompt_stats`
- `actor_prompt_breakdowns`
- `avg_current_step_chars`
- `avg_dependency_context_chars`
- `avg_recent_context_chars`
- `avg_compact_plan_overview_chars`
- `avg_key_values_chars`
- `avg_artifact_refs_chars`

这些指标用于判断 token 增长来自哪一部分上下文。

## 实验命令

以下命令在仓库根目录执行。

### Baseline

```bash
python gaia_baseline_runner.py ^
  --input ../contest/gaia_level1_sample5_seed42.jsonl ^
  --mode baseline ^
  --output outputs/l1_sample5_baseline.jsonl ^
  --summary outputs/l1_sample5_baseline_summary.json
```

### Optimized safe

```bash
python gaia_baseline_runner.py ^
  --input ../contest/gaia_level1_sample5_seed42.jsonl ^
  --mode optimized ^
  --max_recent_steps 1 ^
  --max_summary_chars 500 ^
  --max_recent_chars 300 ^
  --output outputs/l1_sample5_opt_safe.jsonl ^
  --summary outputs/l1_sample5_opt_safe_summary.json
```

### Optimized min

```bash
python gaia_baseline_runner.py ^
  --input ../contest/gaia_level1_sample5_seed42.jsonl ^
  --mode optimized ^
  --max_recent_steps 0 ^
  --disable_key_values ^
  --disable_artifact_refs ^
  --output outputs/l1_sample5_opt_min.jsonl ^
  --summary outputs/l1_sample5_opt_min_summary.json
```

### Optimized key-values

```bash
python gaia_baseline_runner.py ^
  --input ../contest/gaia_level1_sample5_seed42.jsonl ^
  --mode optimized ^
  --max_recent_steps 0 ^
  --max_summary_chars 400 ^
  --max_recent_chars 200 ^
  --output outputs/l1_sample5_opt_keyvalues.jsonl ^
  --summary outputs/l1_sample5_opt_keyvalues_summary.json
```

### Compare

```bash
python gaia_baseline_runner.py ^
  --compare outputs/l1_sample5_baseline.jsonl outputs/l1_sample5_opt_keyvalues.jsonl ^
  --compare_output outputs/l1_sample5_compare_keyvalues.json
```

## 当前 5 题实验结果

样本：`gaia_level1_sample5_seed42.jsonl`

| 模式 | Accuracy | Avg Tokens | Avg Actor Prompt Chars | Avg Tool Calls | Avg Elapsed |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseline | 0.8 | 285062.8 | 60009.4 | 25.6 | 84.48s |
| opt_safe | 0.6 | 393372.6 | 28031.49 | 34.2 | 153.23s |
| opt_min | 0.4 | 319399.0 | 25356.53 | 32.8 | 134.67s |
| opt_keyvalues | 0.8 | 292072.0 | 28475.95 | 27.2 | 126.76s |

阶段性结论：

- `opt_keyvalues` 在保持 0.8 accuracy 的同时，将 Actor prompt 平均字符数从 60009.4 降到 28475.95，下降约 52.55%。
- 端到端 total tokens 仍比 baseline 高约 2.46%，说明仅裁剪初始 actor view 还不够。
- token 反增主要来自 Actor 同一步内多轮工具失败重试导致的 history 累积。
- 后续优化重点应转向“失败工具重复调用抑制”和“同一步 Actor history 压缩”。

## 已知问题

当前本地实验环境中存在若干工具失败来源：

- `search_wiki` 依赖 `wikipedia` 包，缺包会触发重复失败。
- `execute_code` 依赖 `astor`，缺包会触发重复失败。
- `test_single.py` 中部分工具是 stub，例如 `fetch_website_content`、`DocumentProcessingToolkit`。

这些问题会放大 Actor 多轮重试，导致 token 消耗升高。它们不是 actor_view 裁剪本身的问题，但会影响端到端实验指标。

## 下一步计划

1. 增加失败工具重复调用抑制：同一步内同一工具连续失败后，提示 Actor 停止重复调用并基于已有 evidence 总结。
2. 增加 Actor 单步 history 压缩：保留最近工具结果摘要，移除重复 error trace。
3. 将 5 题样本扩展到 GAIA Level 1 全量 53 题，观察指标稳定性。
4. 在 compare 报告中加入失败工具统计，区分“上下文过长”和“工具失败重试”两类 token 消耗。

## 简历描述

基于 CoSight multi-agent 框架构建 GAIA Benchmark 评测与通信优化实验，完成 baseline/optimized 双模式、Actor 结构化上下文裁剪、prompt 分段统计和对比报告生成。在固定 5 题样本上，优化版本在保持准确率 0.8 的情况下将 Actor prompt 平均字符数降低约 52.55%，并定位到工具失败重试是端到端 token 未明显下降的主要瓶颈。
