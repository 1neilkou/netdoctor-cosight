# Co-Sight 比赛作品最小执行计划

本文档目标是将比赛作品路线从“继续扩展 Contest Solver 自定义工具”调整为“最小成本复用 Co-Sight 原生能力”。本阶段只做方案收束，不修改核心代码，不新增自定义样题工具，不读取、不输出、不修改敏感环境配置文件。

## 1. Co-Sight 原生能力如何用于比赛

### 1.1 工具注册

Co-Sight 已具备基于 `Skill` / `SkillFunction` 的工具描述机制，并能将工具转换为 LLM function tool schema。比赛作品中不再优先扩展 Contest Solver 内部静态工具列表，而是把需要的比赛能力包装为 Co-Sight 可识别的 skill/function。

比赛使用方式：

- 复用 Co-Sight 现有 actor agent 工具集，包括搜索、文件读写、文档抽取、代码执行、网页内容获取、报告生成等。
- 如确需新增比赛工具，优先注册为 Co-Sight local skill，而不是写入 Contest Solver 专用工具分支。
- Contest Solver 只维护题目 schema 到 Co-Sight skill 调用输入之间的 adapter。

### 1.2 工具执行

Co-Sight 的 `BaseAgent` 已支持 LLM tool calls、参数解析、参数归一化、本地工具执行、MCP 工具执行和工具事件记录。比赛主执行链应复用这套机制。

比赛使用方式：

- 每道题作为一个 Co-Sight task 输入。
- Co-Sight planner / actor 根据题目内容选择工具并执行。
- Contest Solver 不再把 `tool_executor` 扩展为独立 runtime，只负责批量调度、结果解析和评测导出。

### 1.3 Planner / DAG

Co-Sight 原生 `Plan` 支持 steps、dependencies、status、tool calls，本质上已经是 DAG 执行模型。`TaskPlannerAgent` 和 `PlanToolkit` 可生成和更新计划。

比赛使用方式：

- 对复杂题、多跳题、规划题，优先交给 Co-Sight planner 生成 DAG / step plan。
- Contest Solver 从 Co-Sight plan 中提取 reasoning trace，而不是维护另一套独立 trace 事实源。
- 简单题也可以走单 step plan，保持统一执行入口。

### 1.4 MCP

Co-Sight 已有 MCP server 配置、工具发现和工具调用能力。比赛 MVP 不强制依赖外部 MCP，但保留作为扩展入口。

比赛使用方式：

- MVP 阶段以本地 skill 和 Co-Sight 内置工具为主。
- 后续如需接数据库、浏览器、表格分析、检索服务，优先通过 MCP 接入。
- Contest Solver 不重复设计外部工具协议。

### 1.5 Trace / Report

Co-Sight 已支持 LLM trace context、tool event、plan process event、step tool calls 和报告生成能力。比赛作品应把这些原生数据作为推理轨迹和展示材料的主要来源。

比赛使用方式：

- `reasoning_traces.json` 从 Co-Sight plan、step status、tool calls、tool events 中抽取。
- Markdown/HTML 说明报告可以复用 Co-Sight report 能力或基于原生 trace 做离线导出。
- Contest Solver 的报告导出只作为最终提交格式 adapter。

## 2. Contest Solver 以后只作为 Adapter

Contest Solver 后续定位从“独立求解器”调整为“比赛输入输出和评测 adapter”。

| 职责 | 说明 |
| --- | --- |
| 批量输入题目 | 读取比赛题目或本地评测题目，转换为 Co-Sight task 输入。 |
| 调用 Co-Sight 执行 | 批量创建 task，启动 planner / actor 执行，收集每题执行状态。 |
| 解析 Co-Sight 输出 | 从 plan result、step notes、tool calls、tool events 中抽取最终答案和推理轨迹。 |
| 导出 `final_answers.json` | 按比赛提交格式输出每题最终答案。 |
| 导出 `reasoning_traces.json` | 输出可审计推理过程，包括 plan steps、工具调用、关键证据和失败信息。 |
| 本地 public_eval 评测 | 使用已有 public_eval adapter 做泛化测试，不替代官方赛题，不反向驱动样题补丁。 |

该定位下，Contest Solver 不再承担通用 agent runtime、planner runtime、MCP runtime 或 trace runtime。

## 3. 停止扩展的自定义模块

| 模块 | 停止扩展原因 | 后续处理 |
| --- | --- | --- |
| `contest_solver/core/tool_executor.py` | 与 Co-Sight `BaseAgent` 工具执行机制重复。 | 冻结为 legacy/offline fallback，后续改为 `tool_execution_adapter`。 |
| `contest_solver/tools/calculator_tool.py` 样题分支 | Q002/Q005/Q006/Q007/Q009/Q010 等分支会把工具变成题库映射器。 | 不再新增样题分支；已有逻辑仅作为历史验收参考，后续迁移到通用 skill 或测试 fixture。 |
| `contest_solver/core/task_planner.py` 样题分支 | Q008/Q010 固定计划与 Co-Sight planner 重复，且不可泛化。 | 不再扩展；复杂规划交给 Co-Sight `TaskPlannerAgent` / `PlanToolkit`。 |
| `contest_solver/core/trace_recorder.py` 独立事实源 | 容易与 Co-Sight plan/tool event 产生双轨事实源。 | 降级为 trace view adapter，事实来源改为 Co-Sight 原生执行数据。 |

## 4. 最小可交付版本 MVP

MVP 目标不是做完通用工具体系，而是证明比赛作品可以基于 Co-Sight 原生能力跑通端到端流程。

### 4.1 MVP 能力边界

1. 能启动 Co-Sight。
2. 能批量跑 10 道本地模拟题。
3. 能为每道题生成最终答案。
4. 能导出 `final_answers.json`。
5. 能导出 `reasoning_traces.json`。
6. 能生成源码包和说明文档。

### 4.2 MVP 执行链路

1. Contest Solver adapter 读取题目列表。
2. 每道题转换为 Co-Sight task prompt。
3. Co-Sight planner 生成 plan，actor 执行工具和推理。
4. Adapter 收集 plan result、step tool calls、tool events。
5. Adapter 抽取 final answer 和 reasoning trace。
6. 导出比赛提交文件和说明材料。

### 4.3 MVP 验收标准

| 验收项 | 标准 |
| --- | --- |
| Co-Sight 启动 | 本地服务或脚本入口可运行。 |
| 10 道题批量执行 | 每题都有执行记录，不因单题失败中断全局流程。 |
| 答案导出 | `final_answers.json` 字段完整、可提交或可人工检查。 |
| 轨迹导出 | `reasoning_traces.json` 包含 plan steps、工具调用、关键证据。 |
| 说明文档 | 能说明系统架构、执行方式、复用 Co-Sight 原生能力的边界。 |
| 源码包 | 包含必要源码、docs、示例输入输出，不包含敏感配置内容。 |

## 5. 后续 Public Eval 测试计划

public_eval 用于补充泛化测试，不替代官方区域赛题，也不应驱动样题专用工具补丁。

| 数据集 | 用途 | 关注点 |
| --- | --- | --- |
| HotpotQA | 多跳问答 | Co-Sight planner 是否能拆解多跳证据链，trace 是否可审计。 |
| GSM8K | 数学计算 | 是否能把计算题交给通用执行能力，而不是样题分支。 |
| DROP | 材料数值推理 | 表格、段落、数值推理和答案抽取是否稳定。 |
| GAIA | 复杂工具任务 | Planner、工具调用、外部信息获取和多步骤执行是否协同。 |
| MuSiQue | 多跳问答 | 长链路问答、证据组合和推理轨迹完整性。 |

后续测试顺序建议：

1. 先用每个数据集 3 到 5 条极小样本验证 schema 和导出格式。
2. 再扩展到小批量样本，统计运行成功率、答案可用率、trace 完整率。
3. 最后针对失败样本调整 adapter prompt、输出解析和 trace 映射，不回到 question_id 专用工具补丁路线。

## 6. 决策结论

比赛交付主线应改为：

1. Co-Sight 负责 agent、planner、tool execution、MCP、trace/report。
2. Contest Solver 负责批量输入、输出解析、提交文件导出、本地评测。
3. 自定义样题工具冻结，不再继续扩展。
4. MVP 优先完成“可运行、可导出、可说明”的端到端比赛作品。
