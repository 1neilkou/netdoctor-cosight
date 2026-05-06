# Contest Solver 工具体系泛化方案

本文档只做审计和方案设计，未修改主 pipeline，未读取 `.env`，未引入新依赖。目标是停止围绕 `sample_questions.json` 的专用补丁，转向通用工具 schema、确定性执行和 Co-Sight 原生能力复用。

## 1. 当前样题专用逻辑审计

| 文件 | 是否存在 `if question_id == ...` | 样题绑定情况 | 应改成的通用能力 | 可替代执行方式 |
| --- | --- | --- | --- | --- |
| `contest_solver/tools/calculator_tool.py` | 是。存在 `Q002`、`Q005`、`Q006`、`Q007`、`Q009`、`Q010` 分支。 | 明显绑定样题：吞吐量总和/平均/差值、RSRP 排序、PRB 常识、优化前后差值、KPI 时间序列、保障规划统计。 | 拆成 `generic_table_tool` 和 `generic_calculation_tool`。由 parser / LLM 生成结构化操作，如 aggregate、sort、diff、window_stats。 | 当前不加依赖时用 `csv`、`json`、`re`、`statistics`、`decimal`、安全 `ast`；若后续允许依赖，可用 pandas / DuckDB。 |
| `contest_solver/tools/rule_evaluator.py` | 未发现 question_id 分支。 | 没有按题号分支，但规则模式硬编码在电信样题表达里，如多级阈值、PRB_UL 连续拥塞、掉话率/切换成功率阈值、命名规则块。 | 改为 `generic_rule_tool`：输入 facts + declarative rules，执行层只解释比较、逻辑组合、时间窗口。电信规则作为 preset，而不是核心逻辑。 | 简单规则用 `operator` / `datetime` / stdlib；表格窗口规则后续可用 pandas / DuckDB；LLM 可抽取规则 JSON。 |
| `contest_solver/core/task_planner.py` | 是。存在 `Q010` 和 `Q008` 分支。 | 明显绑定样题：五一 CBD 保障计划、多跳邻区诊断计划。 | 改为 `generic_planner_tool`：输入 goal、constraints、resources、deadline、risks，输出 stages/steps/dependencies。 | 优先复用 Co-Sight `TaskPlannerAgent`、`PlanToolkit`、`Plan` DAG；离线模式可用 LLM 生成结构化计划再校验。 |
| `contest_solver/core/tool_executor.py` | 未发现 question_id 分支。 | 无直接题号绑定，但只手动分发 `rule_evaluator`、`calculator_tool`、`task_planner`，与 Co-Sight 原生 `BaseAgent` 工具执行机制并行。 | 改为 `tool_execution_adapter`，对接 Co-Sight local skill、MCP tool、deterministic local fallback。 | 复用 `BaseAgent` tool calls、MCP engine、Plan tool events；离线评测中保留最小同步执行。 |
| `contest_solver/core/tool_router.py` | 未发现 question_id 分支。 | 没有题号绑定，但靠题型和关键词路由，容易把 unsupported 工具路由出来；工具列表是静态硬编码。 | 改为 capability-based routing：从 semantic parse / LLM structured params / Co-Sight skill registry 选择工具。 | 静态规则只保留 fallback；真实工具能力来自 skill schema、MCP tools、工具参数可满足性校验。 |

## 2. 主要问题

1. 工具执行和样题答案耦合过紧：`calculator_tool` 与 `task_planner` 已经把验收题知识写入工具内部。
2. 工具名称和工具能力混在一起：`calculator_tool` 同时承担表格抽取、排序、聚合、差值、PRB 知识和时间序列统计。
3. LLM 没有稳定地产生工具参数：当前更多是 router 选工具，执行器内部再猜题型。
4. Co-Sight 原生工具 runtime 未被充分复用：Contest Solver 自建 `tool_executor`、trace、report，容易与原生 agent 能力重复。
5. 评测指标容易驱动样题补丁：为了让 Q002/Q005/Q007/Q009/Q010 通过，工具逐步变成题库映射器。

## 3. 泛化原则

- 不再新增 `if qid == "Qxxx"` 分支。
- LLM 负责理解题意、抽取结构化工具参数、提出候选计划；确定性工具负责计算、排序、规则判断和 schema 校验。
- 工具只接受 `question`、`parsed_result`、`metadata`、`tool_input` 等运行时信息，不读取 `expected_answer`。
- Contest Solver 保留评测层和统一 schema，工具注册、执行、trace 优先复用 Co-Sight 原生机制。
- 在不引入新依赖的当前阶段，使用 Python 标准库实现最小通用执行；后续允许依赖时，再接 pandas / DuckDB / JSONLogic 类框架。

## 4. 新工具体系方案

### 4.1 `generic_table_tool`

用途：处理表格抽取、排序、过滤、聚合、派生列、简单时间序列窗口统计。

输入 schema：

```json
{
  "table": {
    "rows": [],
    "text": "",
    "columns": []
  },
  "operations": [
    {
      "type": "sort | filter | aggregate | derive | window",
      "column": "string",
      "order": "asc | desc",
      "group_by": [],
      "metrics": [],
      "condition": {}
    }
  ],
  "output_fields": []
}
```

输出 schema：

```json
{
  "status": "success | failed | skipped",
  "result": {
    "rows": [],
    "aggregates": {},
    "derived_values": {}
  },
  "evidence": [],
  "warnings": []
}
```

LLM 参与：可选。LLM 只抽取表格、列名、操作计划，不直接给计算结果。

确定性执行：是。

可复用开源库或框架：当前阶段用 `csv`、`json`、`re`、`statistics`；后续可切 pandas 做 DataFrame 操作，DuckDB 做 SQL over table。

与 Co-Sight 连接：注册为 local skill/function；也可作为 MCP tool 暴露。执行事件由 `BaseAgent` 和 `Plan.add_tool_call()` 记录。

### 4.2 `generic_calculation_tool`

用途：执行明确公式、四则运算、差值、比例、单位换算、统计摘要。

输入 schema：

```json
{
  "variables": {
    "name": "number"
  },
  "expressions": [
    {
      "name": "string",
      "formula": "string",
      "unit": "string",
      "precision": 2
    }
  ],
  "assumptions": []
}
```

输出 schema：

```json
{
  "status": "success | failed | skipped",
  "result": {
    "values": {},
    "units": {}
  },
  "evidence": [],
  "warnings": []
}
```

LLM 参与：可选。LLM 抽取变量、公式和单位；执行器使用安全表达式求值。

确定性执行：是。

可复用开源库或框架：当前阶段用 `decimal`、`math`、`statistics`、安全 `ast`；后续可考虑 sympy，但当前不新增依赖。

与 Co-Sight 连接：作为 local skill/function 暴露给 `BaseAgent`；复杂计算可由 Co-Sight 现有 `execute_code` 在受控模式下辅助，但优先使用受限表达式执行。

### 4.3 `generic_rule_tool`

用途：处理条件判断、阈值规则、多条件组合、时间窗口规则、冲突规则解释。

输入 schema：

```json
{
  "facts": {},
  "rules": [
    {
      "rule_id": "string",
      "description": "string",
      "conditions": [
        {
          "left": "metric_name",
          "op": "> | >= | < | <= | == | != | in | contains",
          "right": "value"
        }
      ],
      "logic": "all | any",
      "window": {
        "field": "time",
        "min_consecutive": 0
      },
      "conclusion": "string"
    }
  ],
  "conflict_policy": "list_all | highest_severity | first_match"
}
```

输出 schema：

```json
{
  "status": "success | failed | skipped",
  "triggered_rules": [],
  "non_triggered_rules": [],
  "rule_evidence": {},
  "conflicts": [],
  "warnings": []
}
```

LLM 参与：可选。LLM 抽取自然语言规则为 JSON；执行层不让 LLM 判定最终触发与否。

确定性执行：是。

可复用开源库或框架：当前阶段用 `operator`、`datetime`、stdlib 数据结构；后续对表格/时间序列规则可接 pandas / DuckDB。

与 Co-Sight 连接：作为 deterministic local skill；规则执行结果写入 tool event 和 plan step evidence。

### 4.4 `generic_planner_tool`

用途：复杂任务拆解、阶段计划、依赖关系、风险和约束检查。

输入 schema：

```json
{
  "goal": "string",
  "context": "string",
  "constraints": [],
  "resources": [],
  "deadlines": [],
  "required_outputs": [],
  "risk_factors": []
}
```

输出 schema：

```json
{
  "status": "success | failed | skipped",
  "plan": {
    "title": "string",
    "steps": [],
    "dependencies": {}
  },
  "stages": [
    {
      "stage": "string",
      "time_window": "string",
      "tasks": [],
      "constraints_checked": [],
      "risk_notes": []
    }
  ],
  "evidence": [],
  "warnings": []
}
```

LLM 参与：是。LLM 负责规划草案；确定性校验负责检查 schema、依赖引用、deadline 覆盖、空阶段等。

确定性执行：部分确定性。计划生成是 LLM 驱动，校验和格式化是确定性的。

可复用开源库或框架：优先复用 Co-Sight `TaskPlannerAgent`、`PlanToolkit`、`Plan` DAG；后续可借鉴 LangGraph / workflow engine 思想，但当前不新增依赖。

与 Co-Sight 连接：这是最应复用 Co-Sight 的工具。Contest Solver 只需把题目 schema 转成 planner prompt，并把 `Plan` 转成评测需要的 stages / trace。

### 4.5 `answer_verifier / critic_tool`

用途：校验最终答案是否覆盖关键点、数值是否一致、格式是否合规，并输出可审计反馈。

输入 schema：

```json
{
  "question": "string",
  "answer": "string | object",
  "rubric": {
    "required_points": [],
    "numeric_tolerance": {},
    "format_schema": {}
  },
  "tool_results": {},
  "expected_answer": "optional, evaluation-only"
}
```

输出 schema：

```json
{
  "status": "success | failed | skipped",
  "exact_match": false,
  "normalized_match": false,
  "semantic_score": 0.0,
  "matched_points": [],
  "missing_points": [],
  "critique": "string",
  "risk_flags": []
}
```

LLM 参与：可选。确定性 verifier 先做格式、数值、JSON、关键点检查；LLM critic 只用于语义覆盖判断。

确定性执行：混合。exact / numeric / schema 检查确定性；semantic critic 非确定性。

可复用开源库或框架：当前复用现有 semantic grader / normalizer；标准库 `json`、`difflib`、`re` 做基础校验。

与 Co-Sight 连接：作为评测层工具，不应参与生成答案时读取 `expected_answer`。在训练/验收模式中可接入 Co-Sight trace，记录 critic 依据。

### 4.6 `tool_execution_adapter`

用途：统一 Contest Solver schema 与 Co-Sight 原生执行机制，避免主流程直接关心具体 runtime。

输入 schema：

```json
{
  "question_record": {},
  "selected_tools": [],
  "tool_inputs": {},
  "execution_mode": "cosight | local | mcp",
  "trace_context": {}
}
```

输出 schema：

```json
{
  "tool_results": {},
  "executed_tools": [],
  "failed_tools": [],
  "events": [],
  "evidence": []
}
```

LLM 参与：adapter 本身不需要 LLM；上游 agent / planner 可使用 LLM 选择工具和生成参数。

确定性执行：adapter 调度逻辑应确定性；具体工具可能是确定性或 LLM 驱动。

可复用开源库或框架：当前不需要新增。复用 Co-Sight `BaseAgent`、`TaskActorAgent`、`MCPEngine`、`PlanReportEventManager`。

与 Co-Sight 连接：这是 Contest Solver 和 Co-Sight 的边界层。它应负责把 local tool result、MCP result、Plan tool call、tool event 统一成 Contest Solver 的评测输出。

## 5. LLM 生成结构化工具参数的建议

LLM 不应直接“算答案”，而应生成如下中间结构：

- 对表格题：表格列、行、操作列表、排序方向、聚合指标；
- 对计算题：变量、公式、单位、精度；
- 对规则题：facts、rules、阈值、逻辑组合、时间窗口；
- 对规划题：goal、constraints、resources、deadlines、risk_factors；
- 对验证题：rubric required_points、format_schema、numeric_tolerance。

执行器只接受这些结构化参数并给出确定性结果。这样可以避免工具内出现 Q002/Q005/Q007 之类的隐式题库知识。

## 6. 分阶段落地计划

1. 文档冻结：把当前 QID 分支标记为 legacy sample fixture，不继续扩展。
2. Schema 先行：新增通用 tool input/output schema，并把现有结果 summary 映射到通用 schema。
3. Adapter 接入：实现 `tool_execution_adapter`，优先调用 Co-Sight skill/function/MCP，保留本地 deterministic fallback。
4. 工具泛化：用 `generic_table_tool`、`generic_calculation_tool`、`generic_rule_tool` 替代样题分支。
5. Planner 迁移：将 `task_planner` 输出转为 Co-Sight `Plan` / DAG，再导出 Contest Solver stages。
6. Trace 统一：报告 exporter 消费 Co-Sight `tool_event`、`Plan.step_tool_calls`、LLM trace metadata。
7. 评测扩展：在工具泛化后再接 public_eval，避免 public_eval 继续反向驱动样题补丁。

## 7. 应立即停止的开发方向

- 为单个 `question_id` 增加工具分支；
- 在 calculator 内继续增加领域知识解释；
- 在 planner 内硬编码某个题目的完整阶段计划；
- 为提高 sample_questions 指标而扩大关键词路由；
- 在 Contest Solver 内重复实现 Co-Sight 已有的 agent runtime、MCP runtime 和 plan event runtime。
