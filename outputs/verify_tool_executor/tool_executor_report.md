# Contest Solver ToolExecutor 验收报告

## 1. 总体结论

- total_questions: 10
- run_success_rate: 0.000
- schema_valid_rate: 0.000
- trace_complete_rate: 1.000
- tool_call_success_rate: 0.900
- expected_tools_coverage_rate: 1.000
- executed_tools_rate: 1.000
- llm_answer_success_rate: 0.000
- partial_rate: 1.000

## 2. 工具执行概览表

| question_id | question_type | routed_tools | executed_tools | failed_tools | tool_results_summary | status |
|---|---|---|---|---|---|---|
| Q001 | 文本信息抽取 | question_parser, rule_evaluator, answer_formatter | rule_evaluator, question_parser, answer_formatter | 无 | {"answer_formatter": {"final_answer_length": 0, "output_status": "partial", "status": "success"}, "question_parser": {"constraints_count": 4, "keywords_count": 9, "semantic_sour... | partial |
| Q002 | 简单计算 | calculator_tool | calculator_tool | 无 | {"calculator_tool": {"calculation_type": "throughput_sum_average_spread", "result": {"average_mbps": 85.6, "max_cell": "CELL_B", "max_mbps": 92.3, "min_cell": "CELL_C", "min_mbp... | partial |
| Q003 | 条件判断 | rule_evaluator, question_parser | rule_evaluator, question_parser | 无 | {"question_parser": {"constraints_count": 7, "keywords_count": 11, "semantic_source": "fallback", "status": "success"}, "rule_evaluator": {"findings_count": 3, "status": "succes... | partial |
| Q004 | JSON格式转换 | question_parser, answer_formatter | question_parser, answer_formatter | 无 | {"answer_formatter": {"final_answer_length": 0, "output_status": "partial", "status": "success"}, "question_parser": {"constraints_count": 0, "keywords_count": 5, "semantic_sour... | partial |
| Q005 | 表格排序 | calculator_tool | calculator_tool | 无 | {"calculator_tool": {"calculation_type": "rsrp_ascending_sort", "result": {"sorted_cells": [{"cell_id": "CELL_005", "rank": 1, "rsrp_dbm": -102}, {"cell_id": "CELL_002", "rank":... | partial |
| Q006 | 通信常识问答 | 无 | 无 | 无 | {} | partial |
| Q007 | 材料问答 | calculator_tool | calculator_tool | 无 | {"calculator_tool": {"calculation_type": "optimization_before_after_delta", "result": {"after": {"daily_complaints": 3.1, "drop_rate_pct": 0.9, "handover_success_rate_pct": 97.6... | partial |
| Q008 | 多跳问答 | rule_evaluator, trace_recorder, question_parser, answer_verifier | rule_evaluator, question_parser, trace_recorder, answer_verifier | 无 | {"answer_verifier": {"is_valid": false, "score": 0.7, "status": "warning"}, "question_parser": {"constraints_count": 5, "keywords_count": 14, "semantic_source": "fallback", "sta... | partial |
| Q009 | 数据与规则综合 | rule_evaluator, calculator_tool, trace_recorder, answer_verifier | rule_evaluator, calculator_tool, trace_recorder, answer_verifier | 无 | {"answer_verifier": {"is_valid": false, "score": 0.7, "status": "warning"}, "calculator_tool": {"calculation_type": "kpi_timeseries_stats", "result": {"coverage_interference_exc... | partial |
| Q010 | 复杂规划 | rule_evaluator, calculator_tool, task_planner, question_parser, trace_recorder, answer_formatter, answer_verifier | rule_evaluator, calculator_tool, task_planner, question_parser, trace_recorder, answer_formatter, answer_verifier | 无 | {"answer_formatter": {"final_answer_length": 0, "output_status": "partial", "status": "success"}, "answer_verifier": {"is_valid": false, "score": 0.7, "status": "warning"}, "cal... | partial |

## 3. 关键题目检查

### Q002 简单计算

calculator_tool 是否执行，是否输出总和、平均值、差值。

- 工具执行：是
- 结果摘要：`{"calculation_type": "throughput_sum_average_spread", "result": {"average_mbps": 85.6, "max_cell": "CELL_B", "max_mbps": 92.3, "min_cell": "CELL_C", "min_mbps": 78.9, "spread_mbps": 13.4, "total_mbps": 256.8, "values": [{"cell_id": "CELL_A", "throughput_mbps": 85.6}, {"cell_id": "CELL_B", "throughput_mbps": 92.3}, {"cell_id": "CELL_C", "throughput_mbps": 78.9}]}, "status": "success"}`
- 关键字段覆盖：完整

### Q005 表格排序

calculator_tool 是否执行，是否输出 RSRP 排序结果。

- 工具执行：是
- 结果摘要：`{"calculation_type": "rsrp_ascending_sort", "result": {"sorted_cells": [{"cell_id": "CELL_005", "rank": 1, "rsrp_dbm": -102}, {"cell_id": "CELL_002", "rank": 2, "rsrp_dbm": -95}, {"cell_id": "CELL_003", "rank": 3, "rsrp_dbm": -88}, {"cell_id": "CELL_004", "rank": 4, "rsrp_dbm": -81}, {"cell_id": "CELL_001", "rank": 5, "rsrp_dbm": -76}]}, "status": "success"}`
- 关键字段覆盖：完整

### Q007 材料问答

calculator_tool 是否执行，是否输出优化前后差值。

- 工具执行：是
- 结果摘要：`{"calculation_type": "optimization_before_after_delta", "result": {"after": {"daily_complaints": 3.1, "drop_rate_pct": 0.9, "handover_success_rate_pct": 97.6}, "before": {"daily_complaints": 15.2, "drop_rate_pct": 2.8, "handover_success_rate_pct": 91.3}, "daily_complaints_delta": 12.1, "drop_rate_delta_pct_points": 1.9, "handover_success_rate_delta_pct_points": 6.3}, "status": "success"}`
- 关键字段覆盖：完整

### Q009 数据与规则综合

rule_evaluator 是否执行，是否输出规则触发结果。

- 工具执行：是
- 结果摘要：`{"findings_count": 4, "status": "success", "triggered_rules": ["上行PRB连续拥塞规则", "掉话率阈值规则", "切换成功率阈值规则"]}`
- 关键字段覆盖：完整

### Q010 复杂规划

task_planner 是否执行，是否输出结构化阶段计划。

- 工具执行：是
- 结果摘要：`{"stage_count": 4, "stage_names": ["阶段一：许可与远程修复", "阶段二：现场与参数优化", "阶段三：验收与应急预案", "阶段四：节日期间值守"], "status": "success"}`
- 关键字段覆盖：完整

## 4. 失败工具列表

无

## 5. 当前问题与下一步建议

ToolExecutor 已初步生效，可进入 public_eval 或 submission exporter 阶段。
