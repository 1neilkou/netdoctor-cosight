# 区域赛 Contest Solver — 实验设计文档

## 1. 实验目标

验证基于 Co-Sight 的智能体框架在区域赛评审场景下的综合能力，具体包括：

| 评估维度 | 说明 |
|---------|------|
| 答案准确性 | 最终答案与期望答案的匹配程度 |
| 解题轨迹质量 | reasoning_trace 覆盖 expected_trace_points 的比例 |
| 工具调用能力 | 实际调用工具与 expected_tools 的吻合率 |
| 稳定性 | 多次运行结果的一致性 |

---

## 2. 题目设计

### 2.1 题目分布

| 难度级别 | 数量 | 题型 |
|---------|------|------|
| Level 1（基础） | 7 道 | 文本信息抽取、简单计算、条件判断、JSON格式转换、表格排序、通信常识问答、材料问答 |
| Level 2（进阶） | 2 道 | 多跳问答、数据与规则综合 |
| Level 3（挑战） | 1 道 | 复杂规划 |

### 2.2 题型说明

**文本信息抽取**：从结构化/半结构化文本中提取指定字段，考察信息定位和格式输出能力。

**简单计算**：对给定数值执行基本算术（求和、均值、差值），考察 calculator_tool 调用准确性。

**条件判断**：给定规则集合和当前状态，判断哪些规则被触发，考察逻辑推理能力。

**JSON格式转换**：将自然语言描述转换为标准 JSON，考察格式规范化能力。

**表格排序**：对小型数据表按指定字段排序，考察数据操作能力。

**通信常识问答**：5G/网络基础知识问答，考察领域知识储备。

**材料问答**：基于给定段落回答问题，考察阅读理解和信息检索能力。

**多跳问答**：需要跨多个信息源（实时KPI、历史工单、告警记录）进行多步推理，考察关联分析能力。

**数据与规则综合**：对时序KPI数据应用诊断规则，综合判断故障类型和根因，考察系统分析能力。

**复杂规划**：在多约束条件下制定分阶段操作计划，考察时间推理和任务编排能力。

---

## 3. 评分标准

### 3.1 答案准确性（满分 60 分，按题分配）

| 级别 | 单题分值 |
|------|---------|
| Level 1 | 4 分/题 × 7 题 = 28 分 |
| Level 2 | 10 分/题 × 2 题 = 20 分 |
| Level 3 | 12 分/题 × 1 题 = 12 分 |

评分方式：
- 完全正确：满分
- 关键要素覆盖率 ≥ 80%：70% 分值
- 关键要素覆盖率 50%~79%：40% 分值
- 关键要素覆盖率 < 50%：0 分

### 3.2 解题轨迹质量（满分 25 分）

- 每题 expected_trace_points 中被覆盖的要点占比作为得分依据
- 轨迹步骤逻辑顺序正确额外加分

### 3.3 工具调用准确性（满分 10 分）

- 实际调用工具与 expected_tools 完全匹配：满分
- 调用了无关工具（hallucination）：扣分
- 应调用但未调用：0 分

### 3.4 稳定性（满分 5 分）

- 同题连续运行 3 次，结果一致性达 100%：满分
- 3 次中 2 次一致：3 分
- 3 次结果各不同：0 分

---

## 4. 测试流程

```
1. 数据准备
   └── 确认 contest_solver/data/sample_questions.json 包含 10 道题

2. 题目预览（验证数据正确性）
   └── python scripts/run_contest_solver_demo.py

3. 批量求解（待 SolverPipeline 集成后启用）
   └── python scripts/run_contest_solver_demo.py --solve

4. 评估
   └── contest_solver/eval/evaluate_solver.py

5. 结果输出
   └── contest_solver/outputs/result_<timestamp>.json
```

---

## 5. 目录结构

```
contest_solver/
├── __init__.py
├── data/
│   └── sample_questions.json       # 10 道样题
├── core/
│   ├── solver_pipeline.py          # 主解题流程
│   ├── task_planner.py             # 子任务拆解
│   └── tool_router.py              # 工具路由
├── tools/
│   ├── question_parser.py          # 题目解析
│   ├── calculator_tool.py          # 数值计算
│   ├── trace_recorder.py           # 轨迹记录
│   ├── answer_verifier.py          # 答案验证
│   └── answer_formatter.py         # 统一输出格式
├── eval/
│   └── evaluate_solver.py          # 批量评估
└── outputs/                        # 运行结果存放目录
```

---

## 6. 后续扩展计划

| 优先级 | 任务 |
|-------|------|
| P1 | 在 SolverPipeline 中集成 TaskPlanner 和 ToolRouter |
| P1 | 实现 evaluate_solver.py 的实际评分逻辑 |
| P2 | 接入 Co-Sight LLM 替换占位推理逻辑 |
| P2 | 扩展题库至 30 道（覆盖更多难度和题型组合） |
| P3 | 添加多次运行稳定性测试脚本 |
| P3 | 结果可视化（各题型准确率分布图） |
