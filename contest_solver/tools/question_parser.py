"""
question_parser.py — 题目解析工具

公开接口：
    parse_question(question_item: dict) -> dict

输出结构：
    question_id, level, question_type, question,
    keywords,
    raw_numbers, metric_values, date_values, id_values, threshold_values,
    constraints
"""
import json
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# 配置表
# ---------------------------------------------------------------------------

# 纯大写英文缩写 → 需要全词匹配（防止 SON 命中 JSON）
_ASCII_ABBREVS = [
    "PRB", "RSRP", "SINR", "PDCCH", "CIO", "PCI",
    "TDD", "FDD", "KPI", "NR", "LTE", "RRU", "DAS", "SON", "ANR",
    "MAJOR", "CRITICAL", "WARNING", "MINOR",
]
# 含汉字或数字的术语 → 子串匹配即可
_MIXED_TERMS = [
    "5G", "PRB利用率", "上行PRB", "下行PRB",
    "切换成功率", "掉话率", "上行吞吐量", "下行吞吐量",
    "覆盖", "干扰", "拥塞", "告警", "时延", "重传率",
    "小区", "基站", "邻区", "扇区", "宏站", "室内分布",
    "天线", "下倾角", "方位角",
]

_ACTION_TERMS = ["计算", "排序", "提取", "转换", "判断", "分析", "规划", "输出"]

_CONSTRAINT_SIGNALS = [
    # 比较运算符
    ">", "<", "≥", "≤", ">=", "<=",
    # 逻辑/条件
    "且", "须", "需", "必须", "如果", "若",
    # 操作性约束
    "不超过", "不低于", "不得", "限制", "保留",
    # 业务流程
    "持续", "触发", "告警级别", "关闭工单", "申请许可", "观察验证",
    # 规则/阈值关键词
    "规则", "阈值",
]

# 带业务语义的单位（用于 metric_values）
_METRIC_UNITS = r'%|dBm|dB|Mbps|Gbps|MHz|kHz|条|倍'

# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------

def parse_question(question_item: dict) -> dict:
    """
    解析单道题目，输出：
        question_id, level, question_type, question,
        keywords,
        raw_numbers, metric_values, date_values, id_values, threshold_values,
        constraints
    """
    text  = question_item.get("question", "")
    qtype = question_item.get("question_type", "")
    nums  = _classify_numbers(text)

    return {
        "question_id":      question_item.get("question_id", "UNKNOWN"),
        "level":            question_item.get("level", 0),
        "question_type":    qtype,
        "question":         text,          # 原始题目文本，供 rule_evaluator 使用
        "keywords":         _extract_keywords(text),
        "raw_numbers":      nums["raw_numbers"],
        "metric_values":    nums["metric_values"],
        "date_values":      nums["date_values"],
        "id_values":        nums["id_values"],
        "threshold_values": nums["threshold_values"],
        "constraints":      _extract_constraints(text),
    }

# ---------------------------------------------------------------------------
# 数字分类
# ---------------------------------------------------------------------------

def _classify_numbers(text: str) -> dict[str, list[str]]:
    """将题目文本中的数字分为 5 类。"""

    # ---- id_values：网元/题目标识符 --------------------------------
    id_values: list[str] = []
    for m in re.finditer(r'\b(?:CELL|DAS|TKT)[-_]\w+', text):
        tok = m.group()
        if tok not in id_values:
            id_values.append(tok)
    for m in re.finditer(r'\bQ\d{3,}\b', text):
        tok = m.group()
        if tok not in id_values:
            id_values.append(tok)

    # ---- date_values：年份 / 月 / 日 --------------------------------
    date_values: list[str] = []
    for m in re.finditer(r'20\d{2}年?|[0-9]{1,2}月|[0-9]{1,2}日', text):
        tok = m.group().strip()
        if tok not in date_values:
            date_values.append(tok)

    # ---- threshold_values：比较运算表达式 --------------------------
    # 匹配 ">= / <= / > / < / ≥ / ≤" + 可选空格 + 数字 + 可选单位
    threshold_values: list[str] = []
    for m in re.finditer(
        r'(?:>=|<=|[><=≥≤])\s*-?\d+(?:\.\d+)?\s*(?:' + _METRIC_UNITS + r'|个)?',
        text
    ):
        tok = m.group().strip()
        if tok not in threshold_values:
            threshold_values.append(tok)

    # ---- metric_values：带业务单位的数值 ---------------------------
    metric_values: list[str] = []
    for m in re.finditer(
        r'-?\d+(?:\.\d+)?\s*(?:' + _METRIC_UNITS + r')',
        text
    ):
        tok = m.group().strip()
        if tok not in metric_values:
            metric_values.append(tok)

    # ---- raw_numbers：所有原始数字（保留出现顺序，去重）-----------
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
# 关键词提取
# ---------------------------------------------------------------------------

def _extract_keywords(text: str) -> list[str]:
    found: list[str] = []

    # 1. 纯大写英文缩写（全词匹配）
    for term in _ASCII_ABBREVS:
        if term in text:
            pat = r'(?<![A-Za-z0-9])' + re.escape(term) + r'(?![A-Za-z0-9])'
            if re.search(pat, text) and term not in found:
                found.append(term)

    # 2. 含汉字/数字的混合术语（子串匹配）
    for term in _MIXED_TERMS:
        if term in text and term not in found:
            found.append(term)

    # 3. 网元标识符
    for m in re.finditer(r'\b(?:CELL|DAS|TKT)[-_]\w+', text):
        tok = m.group()
        if tok not in found:
            found.append(tok)

    # 4. 任务动作词
    for term in _ACTION_TERMS:
        if term in text and term not in found:
            found.append(term)

    return found

# ---------------------------------------------------------------------------
# 约束提取
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
# 向后兼容：QuestionParser 类（供 solver_pipeline 使用）
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
