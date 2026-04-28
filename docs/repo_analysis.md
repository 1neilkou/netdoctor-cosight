# Co-Sight 项目结构分析

> 目标：为"面向 5G/算力网络的通信网络智能运维 Agent"（NetDoctor）找到最佳集成位置。

---

## 1. 项目概述

Co-Sight 是中兴开源的类 Manus 智能 Agent 系统，核心能力是**自动将用户问题拆解为计划，并并行执行各步骤后生成研究报告**。

- 论文：arXiv:2510.21557（Co-Sight: Conflict-Aware Meta-Verification）
- License：Apache 2.0
- Python >= 3.11，主框架 FastAPI + OpenAI-compatible API + WebSocket + MCP

---

## 2. 目录结构速览

```
netdoctor-cosight/
├── CoSight.py                      # 顶层 Agent 编排类（入口之一）
├── llm.py                          # 全局 LLM 实例化（plan/act/tool/vision）
├── config/
│   ├── config.py                   # 从 .env 读取所有模型配置
│   └── mcp_server_config.json      # MCP 外部工具配置（当前为空 []）
├── cosight_server/
│   └── deep_research/
│       ├── main.py                 # ★ FastAPI 服务启动入口（端口 7788）
│       ├── service.py              # 辅助服务（实体提取、iCenter 查询）
│       └── routers/
│           ├── search.py           # ★ 核心 POST /deep-research/search（触发 CoSight）
│           ├── websocket_manager.py# WebSocket 消息中转层
│           ├── chat_manager.py
│           ├── common.py
│           ├── feedback.py
│           └── user_manager.py
└── app/
    ├── cosight/
    │   ├── agent/
    │   │   ├── planner/            # Planner Agent（制定计划）
    │   │   │   └── task_plannr_agent.py  # TaskPlannerAgent
    │   │   ├── actor/              # Actor Agent（执行步骤）
    │   │   │   └── task_actor_agent.py   # ★ TaskActorAgent（工具注册处）
    │   │   └── base/               # BaseAgent（LLM 调用循环）
    │   ├── task/
    │   │   ├── todolist.py         # Plan 数据结构（步骤、状态、依赖）
    │   │   ├── task_manager.py     # 全局 Plan 注册表
    │   │   └── plan_report_manager.py  # 事件总线（plan_created/process/result）
    │   ├── llm/
    │   │   └── chat_llm.py         # ChatLLM 包装（支持 Langfuse）
    │   └── tool/                   # ★ 所有内置工具包
    │       ├── act_toolkit.py      # 标记步骤状态
    │       ├── search_toolkit.py   # Google/Wiki/Tavily 搜索
    │       ├── code_toolkit.py     # Python 代码执行
    │       ├── file_toolkit.py     # 文件读写
    │       ├── html_visualization_toolkit.py
    │       ├── deep_search/        # 深度搜索子模块
    │       └── ...（共 ~20 个 toolkit）
    └── agent_dispatcher/
        └── domain/plan/action/skill/mcp/
            └── engine.py           # MCP 工具调用引擎（Stdio/SSE）
```

---

## 3. 系统启动入口

| 文件 | 作用 |
|------|------|
| `cosight_server/deep_research/main.py` | **唯一启动入口**，uvicorn 运行 FastAPI，端口 7788，`python main.py` |
| `CoSight.py` | 也可 `python CoSight.py` 单独调试，不启动 Web 服务 |

启动后访问：`http://localhost:7788/cosight/`

---

## 4. 请求执行全链路

```
浏览器 WebSocket
    ↓ (ws /robot/wss/messages)
websocket_manager.py → HTTP POST /deep-research/search
    ↓
search.py::search()
    ├── 创建工作区目录 work_space/work_space_时间戳/
    ├── 订阅 plan_report_event_manager 事件
    ├── 启动子线程 → CoSight(plan_llm, act_llm, tool_llm, vision_llm).execute(query)
    │       ↓
    │   CoSight.execute()
    │       ├── TaskPlannerAgent.create_plan()  → LLM 生成 Plan（JSON）
    │       ├── Plan.get_ready_steps()           → 找出可并行的步骤
    │       └── 多线程并发 _execute_single_step()
    │               ↓
    │           TaskActorAgent.act(question, step_index)
    │               └── BaseAgent.execute()     → LLM 工具调用循环（ReAct）
    │                       └── 调用 all_functions 中的任意工具
    └── 通过 plan_report_event_manager 将进度推入 asyncio.Queue → StreamingResponse → 前端
```

---

## 5. 工具注册机制

所有可供 Actor LLM 调用的工具统一注册在：

**`app/cosight/agent/actor/task_actor_agent.py`，第 98–126 行**

```python
all_functions = {
    "mark_step":        act_toolkit.mark_step,
    "search_google":    search_toolkit.search_google,
    "tavily_search":    search_toolkit.tavily_search,
    "execute_code":     code_toolkit.execute_code,
    "file_saver":       file_toolkit.file_saver,
    "file_read":        file_toolkit.file_read,
    ...
}
if functions:          # 允许外部传入额外工具（扩展点）
    all_functions.update(functions)
```

> 新增工具只需：① 实现 toolkit 类/函数；② 在此 dict 里添加一行。

---

## 6. 配置文件说明

| 文件 | 作用 |
|------|------|
| `.env`（不入库）| 所有密钥、模型地址、API Key |
| `config/config.py` | 读取 .env，提供 `get_model_config()` 等函数 |
| `config/mcp_server_config.json` | 注册 MCP 外部工具服务（当前为空，可直接添加） |

LLM 角色分工（可独立配置，缺省回退到默认）：

| 角色 | 环境变量前缀 | 用途 |
|------|------------|------|
| plan | `PLAN_*` | TaskPlannerAgent，制定计划 |
| act | `ACT_*` | TaskActorAgent，执行步骤 |
| tool | `TOOL_*` | DeepSearch、HtmlToolkit 等 |
| vision | `VISION_*` | 图像/音频/视频分析 |

---

## 7. MCP 扩展机制

`config/mcp_server_config.json` 支持注册外部 MCP Server（stdio 或 SSE），引擎在 `app/agent_dispatcher/domain/plan/action/skill/mcp/engine.py`。

```json
[
  {
    "skill_name": "netdoctor_mcp",
    "skill_type": "local_mcp",
    "mcp_server_config": {
      "command": "python",
      "args": ["netdoctor/mcp_server.py"]
    }
  }
]
```

---

## 8. NetDoctor 最佳集成位置

### 推荐方案：独立域模块 + Toolkit 注册

```
netdoctor-cosight/
├── netdoctor/                        # ★ 新建：NetDoctor 核心域
│   ├── __init__.py
│   ├── tools/                        # 运维工具实现
│   │   ├── alarm_toolkit.py          # 告警查询与分析
│   │   ├── topology_toolkit.py       # 网元拓扑查询
│   │   ├── log_toolkit.py            # 日志解析与检索
│   │   ├── kpi_toolkit.py            # KPI 指标查询（5G NR/SA）
│   │   └── fault_diagnosis_toolkit.py# 故障根因分析
│   ├── data/                         # 模拟/真实数据
│   │   ├── sample_alarms.json
│   │   └── sample_topology.json
│   ├── prompts/                      # 运维专用 Prompt
│   │   └── netdoctor_prompt.py
│   └── mcp_server.py                 # （可选）封装为 MCP Server
├── app/cosight/tool/
│   └── netdoctor_toolkit.py          # ★ 桥接层：将 netdoctor/ 暴露给 Actor
└── cosight_server/deep_research/routers/
    └── netdoctor_router.py           # （可选）运维专用 REST API
```

### 集成步骤（仅需改 2 处现有文件）

**步骤 1：在 `app/cosight/tool/netdoctor_toolkit.py` 实现工具**

```python
from netdoctor.tools.alarm_toolkit import AlarmToolkit
from netdoctor.tools.topology_toolkit import TopologyToolkit
# ...
```

**步骤 2：在 `app/cosight/agent/actor/task_actor_agent.py:98` 追加工具注册**

```python
from app.cosight.tool.netdoctor_toolkit import NetDoctorToolkit

netdoctor = NetDoctorToolkit()
all_functions = {
    # 原有工具不变 ...
    "query_alarms":      netdoctor.query_alarms,
    "get_topology":      netdoctor.get_topology,
    "analyze_kpi":       netdoctor.analyze_kpi,
    "diagnose_fault":    netdoctor.diagnose_fault,
}
```

### 为什么选这个方案

| 原则 | 说明 |
|------|------|
| 不破坏原逻辑 | 仅在 `all_functions` 追加 key，不修改 CoSight/Planner/Actor 核心 |
| 跟随现有模式 | 所有内置工具都走同样路径，无需学习新机制 |
| 可独立测试 | `netdoctor/` 目录完全独立，可单独运行 |
| 支持 MCP | 随时可将 `netdoctor/mcp_server.py` 注册到 `mcp_server_config.json` |
| Demo 友好 | 用户在 Web 界面输入"分析 gNB 基站 001 的告警"，Planner 自动拆步，Actor 调用 NetDoctor 工具 |

---

## 9. 关键文件一览（开发时重点关注）

| 文件 | 修改频率 | 原因 |
|------|---------|------|
| `app/cosight/agent/actor/task_actor_agent.py` | 高 | 注册新工具 |
| `netdoctor/tools/*.py` | 高 | 实现运维能力 |
| `app/cosight/agent/actor/prompt/actor_prompt.py` | 中 | 可加入运维背景知识 |
| `app/cosight/agent/planner/prompt/planner_prompt.py` | 中 | 引导 Planner 生成运维友好的计划 |
| `config/mcp_server_config.json` | 低 | 注册 MCP 工具 |
| `cosight_server/deep_research/main.py` | 低 | 挂载新 router |

---

## 10. 依赖库现状（与运维相关）

- `requests`、`aiohttp`：HTTP 调用网管 API
- `mcp==1.18.0`：MCP 协议支持
- `plotly`、`seaborn`：KPI 可视化
- `execute_code`（subprocess）：可运行 Python 脚本分析日志
- `pdfplumber`、`PyMuPDF`：解析运维手册/告警规范 PDF

运维专项可能需要额外添加（待定）：`paramiko`（SSH）、`pysnmp`（SNMP）、`influxdb-client`（时序数据库）。
