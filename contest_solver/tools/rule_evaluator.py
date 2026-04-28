"""
rule_evaluator.py — 规则识别与评估工具

公开接口：
    evaluate_rules(parsed_question: dict) -> dict

输出结构：
    rule_findings   : list[str]  — 识别到的规则模式描述
    triggered_rules : list[str]  — 被触发的规则名称
    rule_evidence   : dict       — 支持触发结论的关键证据
"""
import re


def evaluate_rules(parsed_question: dict) -> dict:
    """
    对 parse_question() 的输出进行规则识别与评估。

    支持以下规则模式：
    1. 多级阈值判断（如 <97%提示 / <95%告警 / <90%严重）
    2. PRB_UL 连续拥塞规则（PRB_UL > X% 且持续 ≥ N 个时间片）
    3. 掉话率 / 切换成功率绝对阈值规则
    4. 命名规则块（规则A/B/C 或 规则1/2/3）
    """
    text             = parsed_question.get("question", "")
    threshold_values = parsed_question.get("threshold_values", [])

    findings: list[str] = []
    triggered: list[str] = []
    evidence: dict = {}

    # ----------------------------------------------------------------
    # 规则 1：多级阈值判断（如切换成功率告警级别）
    # 匹配形如 "<97%提示" "<95%告警" "<90%严重" 的三级阈值定义
    # ----------------------------------------------------------------
    level_thresholds = re.findall(
        r'<\s*(\d+(?:\.\d+)?)\s*%\s*(提示|预警|告警|严重|warning|critical)',
        text
    )
    if level_thresholds:
        threshold_pct_set = {float(v) for v, _ in level_thresholds}
        # 找测试值：排除已识别的阈值本身
        test_vals = [
            float(m)
            for m in re.findall(r'\b(\d+(?:\.\d+)?)\s*%', text)
            if float(m) not in threshold_pct_set
        ]
        if test_vals:
            test_val = test_vals[0]
            # 从最严重（最小阈值）向上匹配，找到第一个触发的级别
            sorted_th = sorted(level_thresholds, key=lambda x: float(x[0]))
            conclusion_label = None
            for tval_str, label in sorted_th:
                if test_val < float(tval_str):
                    conclusion_label = label
                    break  # 第一个触发的就是最严重级别

            if conclusion_label:
                findings.append(
                    f"多级阈值判断：测试值 {test_val}%，"
                    f"触发级别「{conclusion_label}」"
                    f"（阈值: {'  '.join(f'<{v}%→{l}' for v, l in sorted_th)}）"
                )
                triggered.append(conclusion_label)
                evidence["multi_level_threshold"] = {
                    "test_value": f"{test_val}%",
                    "thresholds": {l: f"<{v}%" for v, l in level_thresholds},
                    "conclusion": conclusion_label,
                }

    # ----------------------------------------------------------------
    # 规则 2：PRB_UL 连续拥塞规则
    # 匹配 "PRB_UL > X% 且持续 ≥ N 个时间片"
    # ----------------------------------------------------------------
    prb_rule = re.search(
        r'PRB[_\s]*UL\s*[>＞]\s*(\d+)\s*%.*?持续\s*[≥>=]+\s*(\d+)',
        text,
        re.DOTALL,
    )
    if prb_rule:
        thr = prb_rule.group(1)
        cnt = prb_rule.group(2)
        findings.append(
            f"连续拥塞规则：PRB_UL > {thr}% 且持续 ≥ {cnt} 个时间片"
        )
        triggered.append("上行PRB连续拥塞规则")
        evidence["prb_consecutive_rule"] = {
            "prb_threshold": f"{thr}%",
            "min_consecutive_slots": cnt,
        }

    # ----------------------------------------------------------------
    # 规则 3：掉话率 / 切换成功率 绝对阈值规则
    # ----------------------------------------------------------------
    for m in re.finditer(
        r'(掉话率|切换成功率)\s*[><=≥≤]+\s*(\d+(?:\.\d+)?)\s*%',
        text,
    ):
        desc = f"阈值规则：{m.group(0).strip()}"
        if desc not in findings:
            findings.append(desc)
            triggered.append(f"{m.group(1)}阈值规则")

    # ----------------------------------------------------------------
    # 规则 4：命名规则块（规则A/B/C 或 规则1/2/3）
    # ----------------------------------------------------------------
    named_rules = re.findall(r'规则\s*([A-Z一二三四五六七八九1-9])', text)
    if named_rules:
        rule_list = "、".join(f"规则{r}" for r in dict.fromkeys(named_rules))
        findings.append(f"题目包含命名规则块：{rule_list}，需逐条条件验证")

    return {
        "rule_findings":   findings,
        "triggered_rules": list(dict.fromkeys(triggered)),   # 去重保序
        "rule_evidence":   evidence,
    }
