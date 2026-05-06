# Co-Sight 原生能力审计

本文档基于本地代码只读审计，未读取 `.env`，未修改主 pipeline。审计重点为 Co-Sight 是否已具备工具注册、工具执行、DAG / workflow / planner、MCP 外部工具接入、trace / trajectory / report 生成能力，以及 Contest Solver 应如何改为 adapter。

## 1. 原生能力概览

| 能力 | 发现情况 | 对应文件路径 | 能否复用 | 对 Contest Solver 的含义 |
| --- | --- | --- | --- | --- |
| 工具注册机制 | 已存在。Co-Sight 使用 `Skill` / `SkillFunction` 描述工具，并通过 `convert_skill_to_tool` 转为 LLM function tool schema。Actor agent 还会把内置 Python 函数注册为可调用工具。 | `app/agent_dispatcher/infrastructure/entity/Skill.py`, `app/agent_dispatcher/infrastructure/entity/SkillFunction.py`, `app/cosight/agent/base/skill_to_tool.py`, `app/cosight/agent/actor/task_actor_agent.py` | 高 | Contest Solver 不应长期维护独立的静态 `TOOL_REGISTRY`，应把 contest 工具注册为 Co-Sight skill/function，或在离线评测场景中提供兼容 adapter。 |
| 工具执行机制 | 已存在。`BaseAgent.execute()` 调用 `llm.create_with_tools()`，处理 tool calls，并通过线程池并发执行本地函数或 MCP 工具。工具参数会经过 `_normalize_tool_args` 做签名和 schema 对齐。 | `app/cosight/agent/base/base_agent.py`, `app/cosight/llm/chat_llm.py`, `app/cosight/agent/actor/task_actor_agent.py` | 高 | `contest_solver/core/tool_executor.py` 当前是并行自建执行器，后续应弱化为 `tool_execution_adapter`，负责把统一 schema 转成 Co-Sight tool call / local deterministic call。 |
| 内置工具能力 | 已存在较丰富的 actor 工具：搜索、Wiki、Tavily、代码执行、文件读写、文档抽取、多模态问答、网页内容获取、HTML 报告生成等。 | `app/cosight/agent/actor/task_actor_agent.py`, `app/cosight/agent/actor/instance/actor_agent_skill.py`, `app/cosight/tool/*` | 中到高 | 通用计算、表格处理、材料问答可优先复用 `execute_code`、文档抽取、搜索/检索工具，而不是为每道样题写工具分支。 |
| Planner / DAG / workflow | 已存在。`Plan` 明确描述为 DAG，包含 steps、dependencies、status、tool calls；`TaskPlannerAgent` 通过 `PlanToolkit.create_plan/update_plan` 生成和更新计划；`CoSight.execute()` 按 ready steps 调度 actor 执行。 | `CoSight.py`, `app/cosight/task/todolist.py`, `app/cosight/tool/plan_toolkit.py`, `app/cosight/agent/planner/task_plannr_agent.py`, `app/cosight/task/task_manager.py` | 高 | `contest_solver/core/task_planner.py` 不应保留 Q008/Q010 固定阶段计划，应改为 Co-Sight planner 的 schema adapter 或轻量离线 planner adapter。 |
| MCP / 外部工具接入 | 已存在 MCP 接入。`config/mcp_server_config.json` 当前为空数组，但 README 和代码支持 stdio / SSE MCP server 配置；`MCPEngine` 可 list / invoke MCP tools。 | `config/mcp_server_config.json`, `README.md`, `app/agent_dispatcher/domain/plan/action/skill/mcp/engine.py`, `app/agent_dispatcher/domain/plan/action/skill/mcp/server.py`, `app/cosight/agent/base/skill_to_tool.py`, `app/cosight/agent/base/base_agent.py` | 高 | 未来 public_eval、通用表格/规则/检索工具可作为 local skill 或 MCP skill 接入，Contest Solver 不必重复设计外部工具协议。 |
| Trace / trajectory / report | 已存在多层 trace。LLM 支持 Langfuse trace context；工具执行会发布 `tool_event`；计划创建、更新、执行过程会发布 plan events；`Plan.add_tool_call()` 记录 step 级工具调用；Web 前端读取 `step_tool_calls` 和 DAG 数据展示流程。 | `CoSight.py`, `app/cosight/llm/chat_llm.py`, `app/cosight/agent/base/base_agent.py`, `app/cosight/task/plan_report_manager.py`, `app/cosight/task/todolist.py`, `cosight_server/web/js/main.js`, `cosight_server/web/js/dag.js`, `cosight_server/web/data/data.js` | 高 | `contest_solver/core/trace_recorder.py` 和 `contest_solver/export/tool_report_exporter.py` 应转为报告视图 adapter，优先消费 Co-Sight plan/tool events，而不是维护另一套 trajectory 事实源。 |

## 2. 细节发现

### 2.1 工具注册与 LLM tool schema

Co-Sight 的工具注册主要围绕 `Skill`、`SkillFunction` 和 agent instance 展开。`SkillFunction.parameters` 保存 JSON schema 风格参数，`convert_skill_to_tool()` 将 skill 转换为 OpenAI function tool 结构。`TaskActorAgent` 还会把运行期构造的 Python 函数放入 `all_functions`，再交给 `BaseAgent` 统一执行。

可复用判断：高。Contest Solver 的 `question_parser`、`generic_table_tool`、`generic_rule_tool`、`answer_verifier` 等应优先以 skill/function 的方式暴露。

### 2.2 工具执行与事件发布

`BaseAgent` 已处理以下通用执行问题：

- LLM 生成 tool calls；
- JSON 参数解析和容错；
- 函数签名 / schema 参数归一化；
- 本地工具和 MCP 工具分流；
- `ThreadPoolExecutor` 并发执行多个 tool call；
- 工具开始、完成、失败事件发布；
- 工具调用写入 `Plan.step_tool_calls`。

可复用判断：高。Contest Solver 当前的 `execute_tools()` 可以保留为离线评测 adapter，但不应继续扩展为另一套 agent runtime。

### 2.3 Planner / DAG / workflow

`Plan` 类保存 steps、dependencies、step status、step notes、step files、step tool calls，且 `get_ready_steps()` 会根据依赖关系找出可执行步骤。`PlanToolkit.create_plan()` 默认生成顺序依赖，也支持显式 dependencies。`CoSight.execute()` 会循环调度 ready steps，并为每个 step 启动 `TaskActorAgent`。

可复用判断：高。复杂规划题应映射成 Co-Sight plan，而不是在 Contest Solver 内硬编码阶段。

### 2.4 MCP 接入

MCP 配置入口存在于 `config/mcp_server_config.json`，当前内容为空数组。`MCPEngine.get_server()` 支持 command 型 stdio server 和 url 型 SSE server；`get_mcp_tools()` 和 `invoke_mcp_tool()` 提供工具发现和调用。`BaseAgent` 会将 MCP tools 合并进 LLM tool schema，并在执行时调用 `_execute_mcp_tool_call()`。

可复用判断：高。后续如果要接 DuckDB 服务、数据库、浏览器、文件分析工具，应优先考虑 MCP 或 Co-Sight skill，而不是在 Contest Solver 内增加长期耦合。

### 2.5 Trace / report

Co-Sight 已具备三类可复用轨迹：

- LLM 层：`ChatLLM.set_trace_context()` 支持 Langfuse trace/session/tags/metadata；
- 工具层：`BaseAgent._push_tool_event()` 发布 tool start / complete / error；
- 计划层：`Plan.add_tool_call()`、`plan_report_event_manager.publish()`、Web 端 DAG / step tool call 展示。

可复用判断：高。Contest Solver 的验收报告可以保留 Markdown/JSON 离线导出，但事实来源应逐步切到 Co-Sight 原生事件和 plan 数据。

## 3. Contest Solver 应改为 Adapter 的模块

| 当前模块 | 当前职责 | 建议 adapter 方向 |
| --- | --- | --- |
| `contest_solver/core/tool_router.py` | 按题型、关键词、semantic parse 静态选择工具 | 改为 capability adapter：把统一题目 schema 转成 Co-Sight skill selection / planner prompt / tool schema 约束。 |
| `contest_solver/core/tool_executor.py` | 手动分发 `rule_evaluator`、`calculator_tool`、`task_planner` | 改为 `tool_execution_adapter`：优先调用 Co-Sight `BaseAgent` / local skill / MCP；离线模式下仅作为 deterministic fallback。 |
| `contest_solver/core/task_planner.py` | 生成固定子任务或样题阶段计划 | 改为 Co-Sight `TaskPlannerAgent` / `PlanToolkit` adapter；输出 Contest Solver 需要的 stages 视图。 |
| `contest_solver/core/trace_recorder.py` | 自建 reasoning trace | 改为 trace view adapter：消费 `Plan.step_tool_calls`、`tool_event`、LLM trace metadata。 |
| `contest_solver/export/tool_report_exporter.py` | 导出工具执行验收报告 | 保留为离线报告 exporter，但输入应来自 Co-Sight 原生 plan/tool events 或 adapter 规范结果。 |
| `contest_solver/tools/calculator_tool.py` | 样题计算分支 | 改为 `generic_calculation_tool` / `generic_table_tool` adapter，不再按 question_id 分支。 |
| `contest_solver/tools/rule_evaluator.py` | 固定电信规则 regex | 改为 `generic_rule_tool`：规则由结构化 schema 输入，执行层只做确定性判断。 |
| `contest_solver/core/llm_answerer.py` | 生成最终答案 | 可继续保留 Contest Solver 模式开关，但底层 LLM 调用应尽量复用 Co-Sight `ChatLLM` 配置和 trace context。 |

## 4. 可以删除或弱化的自定义工具

| 自定义工具 / 逻辑 | 建议 |
| --- | --- |
| `CalculatorTool.execute()` 中基于 Q002/Q005/Q006/Q007/Q009/Q010 的分支 | 删除或迁移到测试 fixture。生产工具改为通用表格、表达式、聚合、排序、时间序列操作。 |
| `TaskPlanner._may_day_cbd_plan()` / `_multi_hop_diagnostic_plan()` | 删除或降级为文档示例。复杂规划交给 Co-Sight planner，并用 schema 校验阶段、依赖、约束。 |
| `ToolRouter.TOOL_REGISTRY` 静态列表 | 弱化为兼容层。真实工具列表应来自 Co-Sight skills、MCP tools 或统一 registry adapter。 |
| `TraceRecorder` 独立轨迹事实源 | 弱化为评测视图。事实源改用 plan events、tool events、LLM trace。 |
| `rule_evaluator.py` 中硬编码电信阈值模式 | 保留为 telecom rule preset，但核心执行器改为通用 rule schema，不再把样题规则写死。 |

## 5. 建议迁移路径

1. 冻结样题专用补丁，不再新增 Q002/Q005/Q007 这类分支。
2. 定义 `tool_execution_adapter`，让 Contest Solver 的统一 schema 可以映射到 Co-Sight skill/function/MCP。
3. 将计算、表格、规则、规划拆成通用工具 schema，LLM 只负责抽取工具参数，确定性工具负责执行。
4. 将验收报告的数据源从 Contest Solver 自建 `tool_results` 逐步切到 Co-Sight `Plan.step_tool_calls` 和 `tool_event`。
5. 保留 Contest Solver 的 evaluator、grader、public_eval importer 作为评测层，不侵入 Co-Sight 原生核心逻辑。
