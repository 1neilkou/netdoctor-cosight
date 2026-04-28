"""
question_parser.py — Hybrid 题目解析工具（规则 + 可选 LLM 语义解析）

公开接口：
    parse_question(question_item, use_llm=False) -> dict
    rule_based_parse(question_item)              -> dict
    merge_parse_results(rule_result, semantic_result) -> dict

parse_question 输出结构：
    question_id, level, question_type, question,
    # 规则解析（顶层平铺，向后兼容 rule_evaluator / tool_router）
    keywords, raw_numbers, metric_values, date_values, id_values,
    threshold_values, constraints,
    # 嵌套结构（新增）
    rule_parse    : 同上字段的副本
    semantic_parse: LLM 或 fallback 的语义解析结果
    merged        : 融合后的统一视图
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# 配置表
# ---------------------------------------------------------------------------

# 纯大写英文缩写 → 全词匹配（防止 SON 命中 JSON）
_ASCII_ABBREVS = [
    "PRB", "RSRP", "SINR", "PDCCH", "CIO", "PCI",
    "TDD", "FDD", "KPI", "NR", "LTE", "RRU", "DAS", "SON", "ANR",
    "MAJOR", "CRITICAL", "WARNING", "MINOR",
]
# 含汉字或数字的术语 → 子串匹配
_MIXED_TERMS = [
    "5G", "PRB利用率", "上行PRB", "下行PRB",
    "切换成功率", "掉话率", "上行吞吐量", "下行吞吐量",
    "覆盖", "干扰", "拥塞", "告警", "时延", "重传率",
    "小区", "基站", "邻区", "扇区", "宏站", "室内分布",
    "天线", "下倾角", "方位角",
]

_ACTION_TERMS = ["计算", "排序", "提取", "转换", "判断", "分析", "规划", "输出"]

_CONSTRAINT_SIGNALS = [
    ">", "<", "≥", "≤", ">=", "<=",
    "且", "须", "需", "必须", "如果", "若",
    "不超过", "不低于", "不得", "限制", "保留",
    "持续", "触发", "告警级别", "关闭工单", "申请许可", "观察验证",
    "规则", "阈值",
]

_METRIC_UNITS = r'%|dBm|dB|Mbps|Gbps|MHz|kHz|条|倍'

# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def rule_based_parse(question_item: dict) -> dict:
    """
    纯规则解析：抽取关键词、数值、约束。
    返回结果供 rule_evaluator 和 merge_parse_results 使用。
    """
    text = question_item.get("question", "")
    nums = _classify_numbers(text)
    return {
        "keywords":         _extract_keywords(text),
        "raw_numbers":      nums["raw_numbers"],
        "metric_values":    nums["metric_values"],
        "date_values":      nums["date_values"],
        "id_values":        nums["id_values"],
        "threshold_values": nums["threshold_values"],
        "constraints":      _extract_constraints(text),
    }


def merge_parse_results(
    rule_result: dict,
    semantic_result: dict | None,
) -> dict:
    """
    融合规则解析和语义解析结果，生成统一视图。

    merged 字段：
        keywords             : 规则解析关键词
        numbers              : metric_values + threshold_values（去重）
        constraints          : 规则约束 + 语义隐含约束（去重）
        required_capabilities: 来自语义解析
        routed_tools         : 占位空列表，由 ToolRouter 填充
    """
    keywords    = list(rule_result.get("keywords", []))
    metric_vals = list(rule_result.get("metric_values", []))
    thresh_vals = list(rule_result.get("threshold_values", []))
    constraints = list(rule_result.get("constraints", []))

    # 合并数值（去重保序）
    seen_nums: set[str] = set()
    numbers: list[str] = []
    for n in metric_vals + thresh_vals:
        if n not in seen_nums:
            seen_nums.add(n)
            numbers.append(n)

    # 补充语义隐含约束
    required_capabilities: list[str] = []
    if semantic_result:
        for ic in semantic_result.get("implicit_constraints", []):
            if ic not in constraints:
                constraints.append(ic)
        required_capabilities = list(semantic_result.get("required_capabilities", []))

    return {
        "keywords":             keywords,
        "numbers":              numbers,
        "constraints":          constraints,
        "required_capabilities": required_capabilities,
        "routed_tools":         [],   # 由 ToolRouter.select_tools() 填充
    }


def parse_question(question_item: dict, use_llm: bool = False) -> dict:
    """
    Hybrid 解析：规则解析 + 可选 LLM 语义解析 + 融合。

    Args:
        question_item: sample_questions.json 单条记录
        use_llm:       True 时尝试调用 LLM；False 时使用规则 fallback

    Returns:
        包含 rule_parse / semantic_parse / merged 的完整解析结果，
        同时在顶层平铺规则解析字段（向后兼容 rule_evaluator / tool_router）。
    """
    text  = question_item.get("question", "")
    qtype = question_item.get("question_type", "")

    # ---- 规则解析 ---------------------------------------------------
    rule_parse = rule_based_parse(question_item)

    # ---- 语义解析（LLM 或 fallback）--------------------------------
    from contest_solver.tools.semantic_parser import (
        semantic_parse_question,
        fallback_semantic_parse,
    )
    if use_llm:
        semantic_parse = semantic_parse_question(question_item, rule_parse)
    else:
        semantic_parse = fallback_semantic_parse(question_item)

    # ---- 融合 -------------------------------------------------------
    merged = merge_parse_results(rule_parse, semantic_parse)

    return {
        # 基础标识
        "question_id":   question_item.get("question_id", "UNKNOWN"),
        "level":         question_item.get("level", 0),
        "question_type": qtype,
        "question":      text,
        # 规则解析字段顶层平铺（向后兼容）
        "keywords":         rule_parse["keywords"],
        "raw_numbers":      rule_parse["raw_numbers"],
        "metric_values":    rule_parse["metric_values"],
        "date_values":      rule_parse["date_values"],
        "id_values":        rule_parse["id_values"],
        "threshold_values": rule_parse["threshold_values"],
        "constraints":      rule_parse["constraints"],
        # 新增嵌套结构
        "rule_parse":    rule_parse,
        "semantic_parse": semantic_parse,
        "merged":        merged,
    }

# ---------------------------------------------------------------------------
# 数字分类（内部）
# ---------------------------------------------------------------------------

def _classify_numbers(text: str) -> dict[str, list[str]]:
    """将题目文本中的数字分为 5 类。"""

    # id_values
    id_values: list[str] = []
    for m in re.finditer(r'\b(?:CELL|DAS|TKT)[-_]\w+', text):
        tok = m.group()
        if tok not in id_values:
            id_values.append(tok)
    for m in re.finditer(r'\bQ\d{3,}\b', text):
        tok = m.group()
        if tok not in id_values:
            id_values.append(tok)

    # date_values
    date_values: list[str] = []
    for m in re.finditer(r'20\d{2}年?|[0-9]{1,2}月|[0-9]{1,2}日', text):
        tok = m.group().strip()
        if tok not in date_values:
            date_values.append(tok)

    # threshold_values
    threshold_values: list[str] = []
    for m in re.finditer(
        r'(?:>=|<=|[><=≥≤])\s*-?\d+(?:\.\d+)?\s*(?:' + _METRIC_UNITS + r'|个)?',
        text
    ):
        tok = m.group().strip()
        if tok not in threshold_values:
            threshold_values.append(tok)

    # metric_values
    metric_values: list[str] = []
    for m in re.finditer(
        r'-?\d+(?:\.\d+)?\s*(?:' + _METRIC_UNITS + r')',
        text
    ):
        tok = m.group().strip()
        if tok not in metric_values:
            metric_values.append(tok)

    # raw_numbers
    raw_numbers: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'-?\d+(?:\.\d+)?(?:\s*(?:' + _METRIC_UNITS + r'|ms|米|度|分钟|小时|个|片))?',
        text
    ):
        tok = m.group().strip()
        if tok and tok not in seen:
            seen.add(tok)
            raw_numbers.append(tok)

    return {
        "raw_numbers":      raw_numbers,
        "metric_values":    metric_values,
        "date_values":      date_values,
        "id_values":        id_values,
        "threshold_values": threshold_values,
    }

# ---------------------------------------------------------------------------
# 关键词提取（内部）
# ---------------------------------------------------------------------------

def _extract_keywords(text: str) -> list[str]:
    found: list[str] = []

    # 纯大写英文缩写（全词匹配）
    for term in _ASCII_ABBREVS:
        if term in text:
            pat = r'(?<![A-Za-z0-9])' + re.escape(term) + r'(?![A-Za-z0-9])'
            if re.search(pat, text) and term not in found:
                found.append(term)

    # 含汉字/数字的混合术语（子串匹配）
    for term in _MIXED_TERMS:
        if term in text and term not in found:
            found.append(term)

    # 网元标识符
    for m in re.finditer(r'\b(?:CELL|DAS|TKT)[-_]\w+', text):
        tok = m.group()
        if tok not in found:
            found.append(tok)

    # 任务动作词
    for term in _ACTION_TERMS:
        if term in text and term not in found:
            found.append(term)

    return found

# ---------------------------------------------------------------------------
# 约束提取（内部）
# ---------------------------------------------------------------------------

def _extract_constraints(text: str) -> list[str]:
    """按标点和换行切句，保留含约束信号词的子句（最多 8 条）。"""
    clauses = re.split(r'[，。；\n]', text)
    constraints: list[str] = []
    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        if any(sig in clause for sig in _CONSTRAINT_SIGNALS):
            display = clause if len(clause) <= 50 else clause[:47] + "..."
            if display not in constraints:
                constraints.append(display)
    return constraints[:8]

# ---------------------------------------------------------------------------
# 向后兼容：QuestionParser 类
# ---------------------------------------------------------------------------

class QuestionParser:
    """向后兼容包装器，内部委托给 parse_question()。"""

    def load_from_file(self, path: str) -> list[dict]:
        fp = Path(path)
        if not fp.exists():
            raise FileNotFoundError(f"题目文件不存在: {path}")
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)
        return [self.parse(q) for q in data]

    def parse(self, raw: dict) -> dict:
        parsed = parse_question(raw)
        return {
            "question_id":  parsed["question_id"],
            "level":        parsed["level"],
            "category":     parsed["question_type"],
            "question":     parsed["question"],
            "context":      raw.get("context", {}),
            "key_entities": parsed["keywords"],
        }
