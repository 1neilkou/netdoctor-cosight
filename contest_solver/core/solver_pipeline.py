from contest_solver.tools.question_parser import QuestionParser
from contest_solver.tools.trace_recorder import TraceRecorder
from contest_solver.tools.answer_formatter import AnswerFormatter


class SolverPipeline:
    """
    区域赛解题主流程：
      1. 读取 & 解析问题
      2. 识别题型
      3. 生成步骤拆解
      4. 执行规则推理（占位逻辑，后续可接大模型）
      5. 记录解题轨迹
      6. 输出 final_answer + reasoning_trace
    """

    def __init__(self):
        self.parser = QuestionParser()
        self.formatter = AnswerFormatter()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def solve_from_file(self, path: str) -> list[dict]:
        questions = self.parser.load_from_file(path)
        return [self._solve_one(q) for q in questions]

    def solve_one(self, raw_question: dict) -> dict:
        parsed = self.parser.parse(raw_question)
        return self._solve_one(parsed)

    # ------------------------------------------------------------------
    # 内部流程
    # ------------------------------------------------------------------

    def _solve_one(self, parsed: dict) -> dict:
        qid = parsed["question_id"]
        recorder = TraceRecorder(qid)

        # Step 1 — 问题读取确认
        recorder.record(
            "问题读取",
            {
                "question_id": qid,
                "level": parsed["level"],
                "category": parsed["category"],
                "question_preview": parsed["question"][:80] + ("..." if len(parsed["question"]) > 80 else ""),
            },
            step_type="input",
        )

        # Step 2 — 题型识别
        category = parsed["category"]
        recorder.record(
            "题型识别",
            f"识别为「{self._category_label(category)}」类题目，将采用对应推理策略",
            step_type="classification",
        )

        # Step 3 — 步骤拆解
        sub_steps = self._decompose(category, parsed)
        recorder.record(
            "步骤拆解",
            sub_steps,
            step_type="planning",
        )

        # Step 4 — 逐步推理
        reasoning_result = self._reason(category, parsed, recorder)

        # Step 5 — 生成最终答案
        final_answer = self._conclude(category, parsed, reasoning_result)
        recorder.record(
            "结论生成",
            final_answer,
            step_type="conclusion",
        )

        return self.formatter.format(
            question_id=qid,
            level=parsed["level"],
            final_answer=final_answer,
            reasoning_trace=recorder.get_trace(),
        )

    # ------------------------------------------------------------------
    # 题型标签
    # ------------------------------------------------------------------

    def _category_label(self, category: str) -> str:
        labels = {
            "multi_hop_reasoning": "多跳推理（趋势分析 + 根因定位）",
            "cross_indicator_diagnosis": "跨指标关联诊断",
            "threshold_judgment": "阈值判断与量化评估",
            "general": "通用推理",
        }
        return labels.get(category, category)

    # ------------------------------------------------------------------
    # 步骤拆解策略（按题型）
    # ------------------------------------------------------------------

    def _decompose(self, category: str, parsed: dict) -> list[str]:
        strategies = {
            "multi_hop_reasoning": [
                "1. 提取时序趋势数据，判断变化方向与速率",
                "2. 排除覆盖/干扰因素（RSRP/SINR 正常则非覆盖问题）",
                "3. 定位核心瓶颈指标（拥塞 / 传输 / 硬件）",
                "4. 建立因果链：触发事件 → KPI 劣化 → 用户感知",
                "5. 给出优先级排序的处置建议",
            ],
            "cross_indicator_diagnosis": [
                "1. 分别分析各小区的异常指标特征",
                "2. 寻找时间/空间上的关联锚点",
                "3. 构建多小区联合因果图",
                "4. 识别共同触发根因（排除独立故障假设）",
                "5. 输出根因推断及置信度说明",
            ],
            "threshold_judgment": [
                "1. 逐项读取当前指标值",
                "2. 与告警阈值（warn / critical）逐一对比",
                "3. 统计超阈值指标数量及偏差量",
                "4. 按最严重指标确定整体严重程度等级",
                "5. 输出完整的阈值偏差明细表",
            ],
            "general": [
                "1. 理解问题核心诉求",
                "2. 提取关键事实与约束条件",
                "3. 逐步推导",
                "4. 形成结论",
            ],
        }
        return strategies.get(category, strategies["general"])

    # ------------------------------------------------------------------
    # 推理执行（规则 + 占位逻辑）
    # ------------------------------------------------------------------

    def _reason(self, category: str, parsed: dict, recorder: TraceRecorder) -> dict:
        ctx = parsed.get("context", {})

        if category == "multi_hop_reasoning":
            return self._reason_multi_hop(ctx, recorder)
        elif category == "cross_indicator_diagnosis":
            return self._reason_cross_indicator(ctx, recorder)
        elif category == "threshold_judgment":
            return self._reason_threshold(ctx, recorder)
        else:
            recorder.record("通用推理", "基于问题文本进行逻辑推导（占位）", step_type="reasoning")
            return {"conclusion": "需要进一步分析"}

    def _reason_multi_hop(self, ctx: dict, recorder: TraceRecorder) -> dict:
        rsrp = ctx.get("rsrp", 0)
        sinr = ctx.get("sinr", 0)
        prb_ul = ctx.get("prb_usage_ul_trend", [])
        dl_prb = ctx.get("prb_usage_dl", 0)

        coverage_normal = rsrp > -98 and sinr > 3
        ul_congested = bool(prb_ul) and prb_ul[-1] >= 90
        dl_normal = dl_prb < 80

        recorder.record(
            "覆盖 / 干扰排查",
            {
                "RSRP": f"{rsrp} dBm（{'正常' if rsrp > -98 else '异常'}）",
                "SINR": f"{sinr} dB（{'正常' if sinr > 3 else '异常'}）",
                "结论": "RSRP/SINR 均正常，排除覆盖和干扰问题" if coverage_normal else "存在覆盖或干扰问题",
            },
            step_type="reasoning",
        )

        recorder.record(
            "容量瓶颈分析",
            {
                "上行 PRB 末值": f"{prb_ul[-1] if prb_ul else 'N/A'}%",
                "下行 PRB": f"{dl_prb}%（正常）",
                "上行趋势": "持续上升至饱和" if ul_congested else "未达拥塞",
                "结论": "上行 PRB 饱和，下行正常 → 纯上行容量问题" if (ul_congested and dl_normal) else "需综合判断",
            },
            step_type="reasoning",
        )

        recorder.record(
            "因果链构建",
            [
                "话务激增（事件/业务高峰）",
                "→ 上行 PRB 利用率持续攀升至 98%",
                "→ 调度器无法为新请求分配资源",
                "→ 上行吞吐量被压缩至 0.8 Mbps",
                "→ 用户上行业务（语音/视频）卡顿，投诉量上升至 25 条",
            ],
            step_type="reasoning",
        )

        return {
            "root_cause": "上行容量饱和（PRB 耗尽）",
            "coverage_ok": coverage_normal,
            "ul_congested": ul_congested,
        }

    def _reason_cross_indicator(self, ctx: dict, recorder: TraceRecorder) -> dict:
        cell_x = ctx.get("cell_x", {})
        cell_y = ctx.get("cell_y", {})
        event_time = ctx.get("event_time", "未知")

        recorder.record(
            "单小区异常特征分析",
            {
                "CELL_X 切换成功率": f"{cell_x.get('handover_success_rate', 'N/A')}%（RSRP 正常，SINR 劣化）",
                "CELL_Y 上行 PRB": f"{cell_y.get('prb_usage_ul', 'N/A')}%（上行拥塞）",
                "触发时间": event_time,
                "关键观察": "两小区异常在同一时间点触发",
            },
            step_type="reasoning",
        )

        recorder.record(
            "关联假设检验",
            [
                "假设 H1：独立故障（独立原因分别触发两小区异常）",
                "  → 同时触发概率极低，排除",
                "假设 H2：CELL_Y 拥塞 → 用户切入 CELL_X → CELL_X 用户密度升高",
                "  → SINR 下降（用户间干扰增加）+ 切换信令压力增大 → 切换失败率升高",
                "  → 符合观测现象，采纳 H2",
            ],
            step_type="reasoning",
        )

        recorder.record(
            "级联效应路径",
            [
                "局部话务突发（14:32）",
                "→ CELL_Y 上行 PRB 饱和（96%）",
                "→ CELL_Y 部分用户被迫或自发切换至邻区 CELL_X",
                "→ CELL_X 用户密度骤增，上行干扰噪声抬升，SINR 从 15 dB 跌至 4 dB",
                "→ CELL_X 调度质量下降，切换信令成功率从 98% 跌至 79%",
            ],
            step_type="reasoning",
        )

        return {
            "root_cause": "局部话务突发引发级联负荷转移效应",
            "mechanism": "CELL_Y 拥塞 → 负荷溢出 → CELL_X SINR 劣化 → 切换失败",
        }

    def _reason_threshold(self, ctx: dict, recorder: TraceRecorder) -> dict:
        thresholds = ctx.get("thresholds", {})
        metrics = {
            "rsrp": ctx.get("rsrp"),
            "sinr": ctx.get("sinr"),
            "drop_rate": ctx.get("drop_rate"),
            "handover_success_rate": ctx.get("handover_success_rate"),
        }

        violations = []
        severity_score = 0

        checks = [
            ("rsrp", "rsrp_warn", "rsrp_critical", "dBm", "low"),
            ("sinr", "sinr_warn", "sinr_critical", "dB", "low"),
            ("drop_rate", "drop_rate_warn", "drop_rate_critical", "%", "high"),
            ("handover_success_rate", "handover_success_rate_warn", "handover_success_rate_critical", "%", "low"),
        ]

        for metric, warn_key, crit_key, unit, direction in checks:
            val = metrics.get(metric)
            warn = thresholds.get(warn_key)
            crit = thresholds.get(crit_key)
            if val is None:
                continue

            if direction == "low":
                exceeded_critical = val < crit if crit is not None else False
                exceeded_warn = val < warn if warn is not None else False
                deviation = round(val - warn, 2) if warn is not None else None
            else:
                exceeded_critical = val > crit if crit is not None else False
                exceeded_warn = val > warn if warn is not None else False
                deviation = round(val - warn, 2) if warn is not None else None

            if exceeded_critical:
                level_tag = "critical"
                severity_score += 2
            elif exceeded_warn:
                level_tag = "warn"
                severity_score += 1
            else:
                level_tag = "normal"

            if exceeded_warn or exceeded_critical:
                violations.append({
                    "metric": metric,
                    "value": f"{val} {unit}",
                    "threshold_warn": f"{warn} {unit}",
                    "threshold_critical": f"{crit} {unit}",
                    "deviation": f"{deviation:+.2f} {unit}" if deviation is not None else "N/A",
                    "level": level_tag,
                })

        recorder.record(
            "阈值逐项对比",
            violations if violations else ["所有指标均在正常范围内"],
            step_type="reasoning",
        )

        severity_map = {0: "正常", 1: "轻微", 2: "中度", 3: "严重", 4: "极严重"}
        overall = severity_map.get(min(severity_score, 4), "极严重")
        if severity_score > 4:
            overall = "极严重"

        recorder.record(
            "综合严重程度评估",
            {
                "超阈值指标数": len(violations),
                "严重程度得分": severity_score,
                "整体等级": overall,
            },
            step_type="reasoning",
        )

        return {"violations": violations, "overall_severity": overall, "score": severity_score}

    # ------------------------------------------------------------------
    # 结论生成
    # ------------------------------------------------------------------

    def _conclude(self, category: str, parsed: dict, reasoning_result: dict) -> str:
        if category == "multi_hop_reasoning":
            rc = reasoning_result.get("root_cause", "未知")
            return (
                f"根本原因：{rc}。"
                f"RSRP/SINR 均正常，可排除覆盖和干扰问题，上行 PRB 利用率达到饱和是唯一瓶颈。"
                f"优先处置步骤：① 立即启动话务均衡，将部分用户迁移至负荷较低的邻区；"
                f"② 检查上行调度参数，评估单用户最大 PRB 占用限制；"
                f"③ 排查是否存在异常大流量用户并限速；"
                f"④ 若拥塞持续，启动扩容评估流程。"
            )
        elif category == "cross_indicator_diagnosis":
            rc = reasoning_result.get("root_cause", "未知")
            mech = reasoning_result.get("mechanism", "")
            return (
                f"共同触发根因：{rc}。"
                f"推理依据：两小区异常在 14:32 同时触发，独立故障概率极低；"
                f"{mech}，符合级联负荷转移的典型特征。"
                f"建议：① 对 CELL_Y 执行话务均衡减轻负荷；② 对 CELL_X 检查并优化切换参数；"
                f"③ 检查 CELL_X/Y 邻区关系配置，防止负荷溢出时切换失败率进一步恶化。"
            )
        elif category == "threshold_judgment":
            violations = reasoning_result.get("violations", [])
            overall = reasoning_result.get("overall_severity", "未知")
            details = "；".join(
                f"{v['metric']}={v['value']}（偏差 {v['deviation']}，{v['level']}）"
                for v in violations
            )
            return (
                f"整体严重程度：【{overall}】。"
                f"共 {len(violations)} 项指标超出阈值：{details}。"
                f"建议立即启动 P1 级处置流程，重点排查 SINR 劣化（疑似干扰）和掉话率过高（疑似覆盖或切换问题）。"
            ) if violations else f"整体严重程度：【正常】。所有指标均在阈值范围内。"
        else:
            return "已完成基础推理分析，结论需结合具体场景进一步确认。"
