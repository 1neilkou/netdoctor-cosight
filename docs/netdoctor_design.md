# NetDoctor-CoSight 设计文档

## TODO

- [ ] 定义运维场景与用例（告警分析、KPI 异常检测、故障根因定位）
- [ ] 实现 `netdoctor/tools/alarm_toolkit.py`
- [ ] 实现 `netdoctor/tools/topology_toolkit.py`
- [ ] 实现 `netdoctor/tools/kpi_toolkit.py`
- [ ] 实现 `netdoctor/tools/fault_diagnosis_toolkit.py`
- [ ] 实现 `netdoctor/core/` 领域模型（告警、网元、KPI 数据结构）
- [ ] 准备 `netdoctor/data/` 模拟数据（告警样本、拓扑、KPI 时序）
- [ ] 在 `app/cosight/tool/netdoctor_toolkit.py` 实现桥接层
- [ ] 在 `TaskActorAgent.all_functions` 注册 NetDoctor 工具
- [ ] 编写端到端 Demo：用户输入运维任务 → CoSight 自动规划并调用工具
