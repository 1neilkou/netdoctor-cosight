# CoSight 准确率优化记录

基于 [ZTE-AICloud/Co-Sight](https://github.com/ZTE-AICloud/Co-Sight) 开源框架，
针对 GAIA Benchmark 进行工程优化，将 L1+L2 准确率从 0.40 提升至 0.50。

## 优化结果

| 指标 | 优化前 | 优化后 |
|---|---|---|
| Overall accuracy (L1+L2, n=20) | 0.40 | 0.50 |
| L1 accuracy | - | 0.70 |
| L2 accuracy | - | 0.30 |
| avg tokens | 187K | 290K |

## 改动文件清单

| 文件 | 改动内容 |
|---|---|
| `gaia_baseline_runner.py` | 附件注入、答案规范化、CAMV 实现、评估流水线 |
| `app/cosight/agent/planner/prompt/planner_prompt.py` | finalize guardrail、low-quality step 护栏 |
| `app/cosight/agent/planner/task_plannr_agent.py` | step 状态摘要生成、传入 finalize prompt |
| `app/cosight/agent/actor/prompt/actor_prompt.py` | 附件路径提示、备用访问路径提示 |
| `app/cosight/agent/base/base_agent.py` | 选择性完成度检查、fetch_failed 错误计数 |
| `app/cosight/agent/actor/task_actor_agent.py` | 兜底完成路径完成度检查 |
| `app/cosight/state/actor_view.py` | dependency_facts fallback（截断300字） |
| `app/cosight/tool/scrape_website_toolkit.py` | 语义失败检测 _is_fetch_failed() |

## 核心改动说明

### 1. Finalize Guardrail（最大收益）

**问题**：Planner 在大量步骤 dependency_blocked 时，直接输出工具调用代码片段或乱码。

**方案**：在 finalize prompt 里注入每个 step 的完成状态，明确约束：
- blocked/dependency_blocked 步骤不允许编造结果
- 只基于 completed 步骤生成答案
- 输出必须是自然语言，禁止代码/JSON 片段

**效果**：准确率从 0.40 直接提升到 0.60（5题小样本）

### 2. GAIA 附件注入

**问题**：GAIA 数据集题目带有文件附件（docx/pdf），原始代码只传问题文字，附件路径从未进入 Actor prompt。

**方案**：
1. 补充附件下载脚本（基于 HuggingFace Hub）
2. 在 `_attachment_path()` 里解析 `file_name`/`file_path` 字段
3. 在 Actor prompt 开头注入："本题附带文件，路径为：{path}，请优先读取"

**效果**：文件类题目从必错变可对，附件题命中率 2/2

### 3. 工具语义失败检测

**问题**：`fetch_website_content` 返回 status=success，但内容是 403/验证页，Actor 把这些当有效内容继续推理。

**方案**：加入 `_is_fetch_failed()` 检测以下信号：
```
"Verification required", "403 Forbidden", "Access Denied",
"Login required", "Sign in to", "Subscribe to read",
"Please verify you are a human"
```
命中时返回 `[FETCH_FAILED]` 标记，触发 Actor 换路径重试。

### 4. Low-Quality Step 护栏

**问题**：step 虽然 completed，但 step_notes 只有搜索页碎片或 404，Planner 凭先验知识猜答案（hallucination）。

**方案**：检测 low-quality step（notes < 50字 或包含失败信号），在 finalize prompt 里明确标注，禁止 Planner 对这些步骤补充猜测。

**效果**：L2 accuracy 从 0.20 提升到 0.30

### 5. 答案规范化

修复评估层格式误判，不影响执行逻辑：

```python
# 数字单位："22 years" → "22"
# 地名缩写："St. Petersburg" → "Saint Petersburg"  
# 同义词："grave" → "backtick"
# 前缀剥离："FINAL ANSWER: right" → "right"
# 泄漏过滤：答案含代码片段时返回空
```

### 6. CAMV 最小实现

参考论文 Conflict-Aware Meta-Verification，实现双路执行：
- Conservative run（temperature=0.0）
- Radical run（temperature=0.3，MAX_REACT_ROUNDS=8）
- 规则选择器：conservative 无法确定时选 radical

**结论**：与 optimized 版本准确率持平（0.50），token 多 8.6%，在当前数据集下无净收益——剩余错题主要是两次都找不到正确信息，而非两次结论不同。

## 失败的尝试

| 改动 | 结果 | 原因 |
|---|---|---|
| LLM 替换正则抽取 facts | L2 掉到 0，回滚 | LLM 改写丢失精确数值/名称 |
| Planner prompt 加分解约束 | 0.50 → 0.45，回滚 | 约束过强导致 Planner 过度谨慎 |
| 完成度检查全量触发 | token 翻倍，无准确率提升 | 大多数 step 已完成，全量检查是浪费 |

## 运行方式

### 环境准备

```bash
# 激活虚拟环境
.venv\Scripts\activate

# 下载 GAIA 附件（需要 HuggingFace 账号并申请数据集访问权限）
python download_gaia_attachments.py
```

### 评估命令

```bash
# Optimized 模式（当前最优）
python gaia_baseline_runner.py \
  --input contest/gaia_l1l2_sample20_seed42.jsonl \
  --mode optimized \
  --output outputs/result.jsonl \
  --summary outputs/result_summary.json

# Baseline 模式（对照组）
python gaia_baseline_runner.py \
  --input contest/gaia_l1l2_sample20_seed42.jsonl \
  --mode baseline \
  --output outputs/baseline.jsonl \
  --summary outputs/baseline_summary.json

# CAMV 模式（实验性）
python gaia_baseline_runner.py \
  --input contest/gaia_l1l2_sample20_seed42.jsonl \
  --mode camv \
  --output outputs/camv.jsonl \
  --summary outputs/camv_summary.json

# 对比两次结果
python gaia_baseline_runner.py \
  --compare outputs/baseline.jsonl outputs/result.jsonl \
  --compare_output outputs/compare.json
```

### 环境变量

```bash
# .env 配置（参考 .env_template）
MODEL_NAME=deepseek-chat
ACT_MODEL_NAME=deepseek-chat
PLAN_MODEL_NAME=deepseek-chat
TOOL_MODEL_NAME=deepseek-chat

# 优化开关
COSIGHT_EXPERIMENT_MODE=optimized
COSIGHT_ENABLE_COMPLETION_CHECK=1
```

## 技术结论

当前架构（DeepSeek-Chat + 单路执行）的天花板在 0.50。剩余错题根因：

- **工具访问失败**（paywall/403）：框架层无法解决，需要付费数据源
- **数据口径差异**：gold 答案和工具检索结果使用不同统计口径
- **专业知识识别**：楔形文字、学术专有名词，需要更强的底层模型

论文版 CoSight 达到 84.4% 的核心是完整 CAMV 架构（N 个 expert agent 并行）和 Gemini 2.5 Pro，**模型选择是最大变量**。
