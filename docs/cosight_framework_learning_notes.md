# Co-Sight 框架主链路学习笔记

本文档只基于本地源码做只读分析，用来快速理解 Co-Sight 主链路，并规划中兴捧月比赛作品的最小接入方案。本文不修改 Co-Sight 核心逻辑，不继续修 `contest_solver` 样题专用工具，不读取、不输出、不修改 `.env`。

## 一、Co-Sight 是怎么启动的？

### README 推荐的启动方式

`README-zh.md` 给出的常规启动方式是：

1. 准备 Python 3.11+。
2. 安装依赖：

```shell
pip install -r requirements.txt
```

3. 根据 `.env_template` 创建本地 `.env`，配置模型、搜索等参数。
4. 启动服务入口：

```shell
python cosight_server/deep_research/main.py
```

5. 浏览器访问：

```text
http://localhost:7788/cosight/
```

也就是说，README 推荐的体验方式是“启动 FastAPI 服务 + 打开 Web 前端”。比赛批处理可以不一定走 Web，但这个服务入口很重要，因为它展示了官方链路如何组织 workspace、日志、事件流和前端展示。

### 服务入口在哪里？

服务入口在：

```text
cosight_server/deep_research/main.py
```

关键对象和逻辑：

- `app = FastAPI()`
- `app.mount("/cosight", StaticFiles(...), name="web")`
- `app.include_router(searchRouter, prefix=...)`
- `uvicorn.run(app=app, host="0.0.0.0", port=int(args.port), ws_max_size=...)`

`main.py` 做的事情不是直接跑 agent，而是：

- 加载运行配置；
- 初始化 `custom_config`；
- 创建 `work_space` 和 `work_space/plans`；
- 挂载上传文件目录、工作空间目录、Web 静态资源；
- 注册 search、websocket、chat 等 router；
- 启动 FastAPI 服务。

### Web 入口是什么？

Web 入口是：

```text
http://localhost:7788/cosight/
```

源码位置：

```text
cosight_server/deep_research/main.py
```

其中：

```python
app.mount(f"/cosight", StaticFiles(directory=web_dir, html=True), name="web")
```

Web 前端会通过后端 router 发起任务，并接收 plan/tool 事件展示任务过程。

### `.env` 主要配置哪些东西？

我没有读取 `.env`，这里只根据 `README-zh.md`、`.env_template` 的变量名和 `config/config.py` 的读取逻辑总结配置类别。

主要配置分几类：

```text
基础模型:
- API_KEY
- API_BASE_URL
- MODEL_NAME
- MAX_TOKENS
- TEMPERATURE
- THINKING_MODE
- PROXY

Planner 专用模型，可选:
- PLAN_API_KEY
- PLAN_API_BASE_URL
- PLAN_MODEL_NAME
- PLAN_MAX_TOKENS
- PLAN_TEMPERATURE
- PLAN_THINKING_MODE
- PLAN_PROXY

Actor 专用模型，可选:
- ACT_API_KEY
- ACT_API_BASE_URL
- ACT_MODEL_NAME
- ACT_MAX_TOKENS
- ACT_TEMPERATURE
- ACT_THINKING_MODE
- ACT_PROXY

Tool 专用模型，可选:
- TOOL_API_KEY
- TOOL_API_BASE_URL
- TOOL_MODEL_NAME
- TOOL_MAX_TOKENS
- TOOL_TEMPERATURE
- TOOL_THINKING_MODE
- TOOL_PROXY

Vision 专用模型，可选:
- VISION_API_KEY
- VISION_API_BASE_URL
- VISION_MODEL_NAME
- VISION_MAX_TOKENS
- VISION_TEMPERATURE
- VISION_THINKING_MODE
- VISION_PROXY

搜索工具:
- GOOGLE_API_KEY
- SEARCH_ENGINE_ID
- TAVILY_API_KEY

运行和上下文:
- WORKSPACE_PATH / WORKSPACE_PATH_ENV
- LLM_TIMEOUT
- TURBO_MODE
- MAX_MESSAGES
- MAX_TOOL_CONTENT_LENGTH
- ENABLE_CONTEXT_COMPRESSION
- MAX_CONTEXT_TOKENS
- COMPRESSION_THRESHOLD
- KEEP_INITIAL_TURNS
- KEEP_RECENT_TURNS

观测，可选:
- LANGFUSE_ENABLED
```

对应源码：

- `config/config.py`
- `llm.py`
- `app/cosight/llm/chat_llm.py`
- `cosight_server/deep_research/main.py`

## 二、任务是怎么进入 Co-Sight 的？

### 用户输入从哪里进入？

Web 服务模式下，用户输入先进入：

```text
cosight_server/deep_research/routers/search.py
```

关键入口：

```python
@searchRouter.post("/deep-research/search")
async def search(request: Request, params: Any = Body(None)):
```

`search()` 会从请求参数里取：

```python
session_info = params.get("sessionInfo", {})
plan_id = session_info.get("messageSerialNumber", "")
content_array = params.get("content", [])
query_content = content_array[0]["value"]
```

所以用户任务的自然形态就是一段 `query_content` 文本。

### 最终调用到哪个类/函数？

服务模式最终调用：

```python
cosight = CoSight(
    llm_for_plan,
    llm_for_act,
    llm_for_tool,
    llm_for_vision,
    work_space_path=work_space_path_time,
    message_uuid=plan_id
)
result = cosight.execute(query_content)
```

对应源码：

```text
cosight_server/deep_research/routers/search.py
CoSight.py
```

如果比赛批处理想绕开 Web，最小入口就是直接调用：

```python
CoSight(...).execute(question)
```

### `CoSight.py` 里核心执行流程是什么？

核心类：

```text
CoSight.py
class CoSight
```

构造函数：

```python
def __init__(
    self,
    plan_llm,
    act_llm,
    tool_llm,
    vision_llm,
    work_space_path: str = None,
    message_uuid: str | None = None
)
```

执行函数：

```python
def execute(self, question, output_format=""):
```

`CoSight.__init__()` 主要做：

- 设置 `work_space_path`；
- 生成或接收 `plan_id`；
- 创建空 `Plan()`；
- `TaskManager.set_plan(self.plan_id, self.plan)`；
- 给 plan/act/tool/vision 四类 LLM 设置 trace context；
- 创建 `TaskPlannerAgent`；
- 保存 actor/tool/vision LLM 供后续 step 执行使用。

`CoSight.execute()` 主流程：

1. 把当前任务摘要写入 LLM metadata。
2. 调用 `TaskPlannerAgent.create_plan(question, output_format)`。
3. 如果 `Plan.get_ready_steps()` 为空，最多重试 3 次创建 plan。
4. 循环检查 ready steps。
5. 对每个 ready step 启动线程，调用 `_execute_single_step(question, step_index)`。
6. `_execute_single_step()` 为每个 step 创建 `TaskActorAgent`。
7. Actor 执行完成后，step status / notes / tool calls 写回同一个 `Plan`。
8. 没有 active threads 且没有 ready steps 时退出循环。
9. 调用 `TaskPlannerAgent.finalize_plan()` 生成最终总结并返回。

一句话：`CoSight.execute()` 已经是完整的“规划、DAG 调度、执行、总结”主链路。

## 三、Planner 是怎么工作的？

### `TaskPlannerAgent` 做什么？

源码：

```text
app/cosight/agent/planner/task_plannr_agent.py
class TaskPlannerAgent(BaseAgent)
```

它负责“让 LLM 通过工具创建和更新计划”。

构造时：

```python
self.plan = TaskManager.get_plan(plan_id)
plan_toolkit = PlanToolkit(self.plan)
terminate_toolkit = TerminateToolkit()
all_functions = {
    "create_plan": plan_toolkit.create_plan,
    "update_plan": plan_toolkit.update_plan,
    "terminate": terminate_toolkit.terminate
}
```

也就是说，Planner 不是直接手写 `Plan`，而是把 `create_plan` / `update_plan` 作为工具暴露给 LLM。LLM 需要通过 tool call 来创建计划。

关键方法：

- `create_plan(question, output_format="")`
- `re_plan(question, output_format="")`
- `finalize_plan(question, output_format="")`

`finalize_plan()` 会调用：

```python
self.plan.set_plan_result(result)
plan_report_event_manager.publish("plan_result", self.plan)
```

这就是最终结果写入和发布的位置。

### `PlanToolkit.create_plan / update_plan` 做什么？

源码：

```text
app/cosight/tool/plan_toolkit.py
class PlanToolkit
```

`create_plan(title, steps, dependencies=None)`：

- 接收 title、steps、dependencies；
- 如果 dependencies 是字符串，尝试 `ast.literal_eval()` 转为 dict；
- 如果没给 dependencies，则默认按顺序生成依赖；
- 调用 `self.plan.update(title, steps, dependencies)`；
- 发布 `plan_created` 事件；
- 返回格式化后的 plan 文本。

`update_plan(title=None, steps=None, dependencies=None)`：

- 修改已有 `Plan`；
- 尽量保留已经开始或完成的 step 状态；
- 发布 `plan_updated` 事件。

所以 `PlanToolkit` 是 Planner LLM 和 `Plan` 数据结构之间的写入适配层。

### `Plan / DAG` 的数据结构是什么？

源码：

```text
app/cosight/task/todolist.py
class Plan
```

`Plan.__init__()` 中的核心字段：

```text
title
steps
step_statuses
step_notes
step_details
step_files
step_tool_calls
dependencies
result
work_space_path
```

这些字段组合起来就是当前 Co-Sight 的 DAG 状态。

### step、dependency、status 是怎么表示的？

`steps` 是 step 文本列表：

```python
self.steps = steps if steps else []
```

`dependencies` 是 step index 到前置 step index 列表的映射：

```python
self.dependencies = {i: [i - 1] for i in range(1, len(self.steps))}
```

如果 LLM 传入 dependencies，则会进入 `_normalize_dependencies()`，支持字符串 key、1 基编号转 0 基编号等兼容处理。

`step_statuses` 是 step 文本到状态的 dict：

```python
self.step_statuses = {step: "not_started" for step in self.steps}
```

常见状态：

```text
not_started
in_progress
completed
blocked
```

`get_ready_steps()` 会检查：

- 当前 step 还是 `not_started`；
- 所有依赖 step 都不再是 `not_started` 或 `in_progress`。

这就是 Co-Sight 的 DAG 调度判断。

## 四、Actor 是怎么工作的？

### `TaskActorAgent` 做什么？

源码：

```text
app/cosight/agent/actor/task_actor_agent.py
class TaskActorAgent(BaseAgent)
```

`TaskActorAgent` 是 step 执行器。Planner 只负责拆任务，Actor 负责把某个 step 真正做完。

构造时，它会：

- 从 `TaskManager.get_plan(plan_id)` 拿到同一个 `Plan`；
- 初始化多个 toolkit；
- 组装 `all_functions`；
- 调用 `BaseAgent.__init__()` 完成工具注册；
- 根据 plan title 判断中文/英文，写入 system prompt。

Actor 注册的 Python 工具包括：

```text
mark_step
search_google
search_wiki
tavily_search
audio_recognition
execute_code
file_saver
file_read
file_str_replace
file_find_in_content
ask_question_about_image
ask_question_about_video
fetch_website_content
fetch_website_content_with_images
fetch_website_images_only
extract_document_content
create_html_report
```

代码中还有一些注释掉的工具，比如 `deep_search`、`search_baidu`、`image_search`、`browser_use`，当前没有进入 `all_functions`。

### 每个 plan step 怎么被执行？

关键方法：

```python
TaskActorAgent.act(question, step_index)
```

流程：

1. 保存原始 `question`。
2. `self.plan.mark_step(step_index, step_status="in_progress")`。
3. 发布 `plan_process`。
4. 生成 actor task prompt，包含 question、step_index、当前 plan、workspace。
5. 调用 `self.execute(self.history, step_index=step_index)`，这里进入 `BaseAgent.execute()`。
6. 如果 step 仍是 `in_progress`，标记为 `completed`，并把执行结果写入 `step_notes`。
7. 如果异常，标记为 `blocked`，错误写入 `step_notes`。
8. 再次发布 `plan_process`。

### Actor 和 Planner 的关系是什么？

关系很清晰：

- Planner：生成和维护 `Plan` / DAG。
- Actor：执行 `Plan` 里的单个 step。
- `CoSight.execute()`：负责调度 Planner 和多个 Actor。
- `TaskManager`：用 `plan_id` 让 Planner 和 Actor 拿到同一个 `Plan` 实例。

源码对应：

```text
CoSight.py
app/cosight/task/task_manager.py
app/cosight/agent/planner/task_plannr_agent.py
app/cosight/agent/actor/task_actor_agent.py
```

## 五、工具是怎么注册和调用的？

### Skill / SkillFunction 或 Python function 怎么变成 LLM tool schema？

核心源码：

```text
app/cosight/agent/base/base_agent.py
app/cosight/agent/base/skill_to_tool.py
app/agent_dispatcher/infrastructure/entity/Skill.py
app/agent_dispatcher/infrastructure/entity/SkillFunction.py
```

两类工具来源：

1. Agent template 中的 `Skill` / `SkillFunction`。
2. Planner/Actor 构造时传入的 Python function dict。

`BaseAgent.__init__()` 中：

```python
self.mcp_tools = get_mcp_tools(self.agent_instance.template.skills)
for skill in self.agent_instance.template.skills:
    self.tools.extend(convert_skill_to_tool(skill.model_dump(), "en"))
self.tools.extend(convert_mcp_tools(self.mcp_tools))
self.functions = functions
```

`convert_skill_to_tool()` 会把 `Skill` 转成 OpenAI function tool schema：

```python
{
    "type": "function",
    "function": {
        "name": skill["skill_name"],
        "description": skill["description_en"],
        "parameters": parameters
    }
}
```

本地 Python function 本身不自动生成 schema，它需要和 agent template 的 skill schema 或手工注册工具名对齐。Actor/Planner 的内置工具就是通过 `all_functions` 暴露给 `BaseAgent` 执行。

### `BaseAgent.execute()` 如何处理 tool calls？

源码：

```text
app/cosight/agent/base/base_agent.py
BaseAgent.execute()
```

流程：

1. 调用：

```python
response = self.llm.create_with_tools(messages, self.tools)
```

2. `_process_response()` 判断 LLM 是否返回 `tool_calls`。
3. 如果没有 tool call，直接返回 response content。
4. 如果有 tool call，把 assistant message 加入 history。
5. 调用 `_execute_tool_calls(response.tool_calls, step_index)`。
6. 工具结果以 role=`tool` 的消息形式追加回 messages。
7. 如果工具名是 `terminate` 或 `mark_step`，认为当前循环可结束。
8. 否则继续下一轮 LLM/tool loop，直到达到 max iteration。

`ChatLLM.create_with_tools()` 位于：

```text
app/cosight/llm/chat_llm.py
```

它负责真正调用 OpenAI-compatible chat completions，并传入：

```python
tools=tools
tool_choice="auto"
```

同时还处理：

- Langfuse trace/session；
- thinking mode；
- 上下文压缩；
- tool call 参数 JSON 修复；
- 重试和超时。

### 本地工具和 MCP 工具怎么区分？

在 `BaseAgent._execute_tool_calls()` 中：

```python
if function_name in self.functions:
    self._execute_tool_call(...)
else:
    self._execute_mcp_tool_call(...)
```

也就是说：

- 工具名在 `self.functions` 中：本地 Python 工具；
- 工具名不在 `self.functions` 中：尝试按 MCP 工具执行。

MCP 相关代码：

```text
app/cosight/agent/base/skill_to_tool.py
app/agent_dispatcher/domain/plan/action/skill/mcp/engine.py
```

`_execute_mcp_tool_call()` 会调用：

```python
MCPEngine.invoke_mcp_tool(...)
```

### 工具参数如何解析和归一化？

源码：

```text
app/cosight/agent/base/base_agent.py
BaseAgent._execute_tool_call()
BaseAgent._normalize_tool_args()
BaseAgent._filter_mcp_tool_args()
```

本地工具参数处理：

1. 从 LLM tool call 取 `function.arguments`。
2. 清理 markdown code fence、`null`、`None`、空字符串。
3. 尝试 `json.loads()`。
4. 失败后尝试修复单引号、尾逗号、缺 `{}` 等。
5. 对 `mark_step` 自动补充 `step_index`。
6. `_normalize_tool_args()` 根据 Python 函数签名、tool schema、`FUNCTION_ARG_MAPPING` 做参数名对齐。
7. 移除函数不需要的多余字段。

MCP 工具参数处理：

- `_filter_mcp_tool_args()` 会根据 MCP tool schema，只保留工具定义中存在的参数。

这套机制已经解决了很多 LLM tool call 参数不稳定的问题，Contest Solver 没必要重复写一个工具参数 runtime。

### 工具执行结果在哪里保存？

工具执行结果有三处保存或传递：

1. 作为 LLM 对话里的 tool message：

```python
{
    "role": "tool",
    "name": function_name,
    "content": str(result),
    "tool_call_id": tool_call_id
}
```

2. 写入 `Plan.step_tool_calls`：

```python
self.plan.add_tool_call(step_index, function_name, function_args, str(result))
```

3. 通过 `tool_event` 发布给事件系统：

```python
self._push_tool_event("tool_complete", ...)
```

源码位置：

```text
app/cosight/agent/base/base_agent.py
app/cosight/task/todolist.py
app/cosight/task/plan_report_manager.py
```

## 六、Trace / Report 是怎么生成的？

### `tool_event` 在哪里发布？

源码：

```text
app/cosight/agent/base/base_agent.py
BaseAgent._push_tool_event()
```

工具执行开始、完成、失败都会发布：

```python
self._push_tool_event("tool_start", ...)
self._push_tool_event("tool_complete", ...)
self._push_tool_event("tool_error", ...)
```

最终进入：

```python
plan_report_event_manager.publish("tool_event", self.plan_id, event_data)
```

事件管理器源码：

```text
app/cosight/task/plan_report_manager.py
class EventManager
```

### `step_tool_calls` 在哪里记录？

记录位置：

```text
app/cosight/task/todolist.py
Plan.add_tool_call()
```

调用位置：

```text
app/cosight/agent/base/base_agent.py
BaseAgent._execute_tool_call()
BaseAgent._execute_mcp_tool_call()
```

本地工具写入当前 step：

```python
self.plan.add_tool_call(step_index, function_name, function_args, str(result))
```

MCP 工具没有具体 step 时使用 `step_index=-1`，`Plan.add_tool_call()` 会写入：

```text
__global_tools__
```

### plan/report 数据在哪里保存？

运行时内存：

```text
app/cosight/task/task_manager.py
TaskManager.plans[plan_id]
```

Plan 内部字段：

```text
Plan.title
Plan.steps
Plan.dependencies
Plan.step_statuses
Plan.step_notes
Plan.step_files
Plan.step_tool_calls
Plan.result
```

服务模式下日志文件：

```text
work_space/plans/{plan_id}.log
work_space/plans/{plan_id}.final.json
```

写入逻辑在：

```text
cosight_server/deep_research/routers/search.py
append_create_plan_local()
```

它订阅 `plan_created`、`plan_updated`、`plan_process`、`plan_result`、`tool_event`，然后把 Plan 或 tool event 序列化写入日志，并推给前端队列。

### 前端或报告如何读取这些结果？

前端实时展示依赖事件流：

- `plan_report_event_manager.publish(...)`
- `search.py` 里的 `plan_queue`
- WebSocket / streaming response
- 前端读取 plan、step 状态、tool event、`step_tool_calls`

静态回放依赖日志：

```text
work_space/plans/{plan_id}.log
work_space/plans/{plan_id}.final.json
```

如果任务已经完成，`search.py` 会优先读取 final/log 文件做 replay。

报告生成工具在 Actor 中也已注册：

```text
app/cosight/agent/actor/task_actor_agent.py
create_html_report
```

底层实现：

```text
app/cosight/tool/html_visualization_toolkit.py
```

比赛侧不一定要用 HTML 报告，但可以复用 plan/tool event 作为 `reasoning_traces.json` 的事实来源。

## 七、对我比赛项目的启发

### Contest Solver 哪些模块可以保留为 adapter？

建议保留这些“输入输出/评测层”能力：

| 模块/能力 | 保留原因 | 新定位 |
| --- | --- | --- |
| 数据集导入 | 官方题、样题、public_eval 都需要统一读取。 | 把题目转成 Co-Sight task prompt。 |
| 批量运行脚本 | 比赛需要一批题自动跑。 | 循环调用 `CoSight(...).execute(question)` 或服务 API。 |
| 输出解析器 | Co-Sight 输出是 plan/result/log，需要转成比赛格式。 | 抽取 `final_answer`、`reasoning_trace`。 |
| submission exporter | 比赛交付需要固定 JSON。 | 导出 `final_answers.json`、`reasoning_traces.json`。 |
| local evaluator | 本地验证仍然有价值。 | 只评估输出，不驱动工具补丁。 |
| public_eval importer | 泛化测试有价值。 | 作为补充测试集，不替代官方赛题。 |
| report exporter | 交付说明需要可读报告。 | 消费 Co-Sight plan/tool events。 |

一句话：Contest Solver 只做 adapter，不做 agent runtime。

### 哪些自定义模块不该继续扩展？

这些模块应冻结或降级：

| 模块 | 为什么不该继续扩展 | 替代方向 |
| --- | --- | --- |
| `contest_solver/core/tool_executor.py` | 和 `BaseAgent.execute()` 重复。 | 改成 Co-Sight execution adapter。 |
| `contest_solver/tools/calculator_tool.py` 样题分支 | 容易变成 Q002/Q005/Q007 题库映射器。 | 交给 Co-Sight `execute_code` 或未来通用 skill。 |
| `contest_solver/core/task_planner.py` 样题分支 | 和 `TaskPlannerAgent` / `PlanToolkit` 重复。 | 直接用 Co-Sight Planner。 |
| `contest_solver/core/tool_router.py` 静态工具路由 | Co-Sight 已有 LLM tool call 和 skill schema。 | 只保留为 prompt/能力约束 adapter。 |
| `contest_solver/core/trace_recorder.py` 独立事实源 | 与 `Plan.step_tool_calls`、`tool_event` 双轨。 | 改为 trace view adapter。 |
| 针对 10 道样题的特殊补丁 | 对比赛泛化帮助小，还会污染工具体系。 | 停止新增。 |

### 如何用 Co-Sight 原生 Planner / Tool / Trace 替代自研 ToolExecutor？

现在的自研 ToolExecutor 做了三件事：

1. 根据 router 结果选择工具。
2. 调用自定义工具。
3. 收集 `tool_results` / `executed_tools` / `failed_tools`。

用 Co-Sight 替代后：

1. Planner：`TaskPlannerAgent` 负责拆任务，`PlanToolkit` 写入 DAG。
2. Tool selection：LLM 在 `BaseAgent.execute()` 中基于 `self.tools` 自动生成 tool calls。
3. Tool execution：`BaseAgent._execute_tool_call()` / `_execute_mcp_tool_call()` 执行工具。
4. Trace：`Plan.step_tool_calls` + `tool_event` 记录工具执行。
5. Final answer：`TaskPlannerAgent.finalize_plan()` 写入 `Plan.result`。

Contest Solver 新 adapter 只需要：

- 构造 prompt；
- 调用 Co-Sight；
- 从 `Plan` 或服务日志中抽取结果；
- 导出比赛 JSON。

### 最小可交付比赛作品应该怎么做？

MVP 路线：

1. 保留现有 Co-Sight 核心，不改 planner/actor/base agent。
2. 新增一个批处理入口，读取比赛题目。
3. 每道题生成稳定 prompt，例如：

```text
请解答下面比赛题目，并在最后输出“最终答案：...”。
要求保留关键推理步骤，必要时使用工具。
题目：...
```

4. 调用：

```python
CoSight(
    llm_for_plan,
    llm_for_act,
    llm_for_tool,
    llm_for_vision,
    work_space_path=...,
    message_uuid=...
).execute(question)
```

5. 从 `Plan.result` 或 `CoSight.execute()` 返回文本抽取 `final_answer`。
6. 从 `Plan.steps`、`Plan.dependencies`、`Plan.step_statuses`、`Plan.step_notes`、`Plan.step_tool_calls` 抽取 `reasoning_trace`。
7. 导出：

```text
final_answers.json
reasoning_traces.json
运行说明文档
源码包
```

8. 用本地 10 道题和 public_eval 小样本做 smoke test，但不再为了分数继续写样题工具分支。

## 文本流程图

```text
User Task
→ CoSight.execute()
→ TaskPlannerAgent
→ Plan / DAG
→ TaskActorAgent
→ BaseAgent tool calls
→ Tool results / step_tool_calls
→ Report / final answer
→ Contest submission exporter
```

更贴近源码的版本：

```text
Web 输入或批处理题目
→ cosight_server/deep_research/routers/search.py::search()
  或直接 Python 调用 CoSight.py::CoSight.execute()
→ TaskPlannerAgent.create_plan()
→ PlanToolkit.create_plan()
→ todolist.py::Plan.update()
→ CoSight.execute() 调度 Plan.get_ready_steps()
→ TaskActorAgent.act()
→ BaseAgent.execute()
→ ChatLLM.create_with_tools()
→ BaseAgent._execute_tool_call() / _execute_mcp_tool_call()
→ Plan.add_tool_call()
→ plan_report_event_manager.publish()
→ TaskPlannerAgent.finalize_plan()
→ Plan.result / work_space/plans/{plan_id}.final.json
→ Contest Solver adapter 导出 final_answers.json / reasoning_traces.json
```
