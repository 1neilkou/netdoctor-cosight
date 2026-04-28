"""
answer_normalizer.py — 答案标准化模块

公开接口：
    normalize_answer(answer: str, question_type: str = "") -> dict

输出结构：
    normalized_text : str          — 去除解释性前缀后的文本
    normalized_json : dict | None  — 若答案含 JSON 对象则解析，否则 None
    numbers         : list[float]  — 答案中提取的所有数值
    keywords        : list[str]    — 小区ID、规则名、告警级别等关键标识
    is_json         : bool         — 答案主体是否为完整 JSON 对象
"""
from __future__ import annotations

import json
import re

# ---------------------------------------------------------------------------
# 常见解释性前缀正则（去除后取正文）
# ---------------------------------------------------------------------------
_PREFIX_RE = re.compile(
    r"^(?:最终答案|答案|回答|结果|综合答案|解题结果)\s*[：:]\s*",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------


def normalize_answer(answer: str, question_type: str = "") -> dict:
    """
    对答案字符串进行标准化，提取结构化信息。

    Args:
        answer:        原始答案文本（可为空串或 None）
        question_type: 题型（预留字段，当前逻辑与题型无关）

    Returns:
        含 normalized_text / normalized_json / numbers / keywords / is_json 的字典。
    """
    if not isinstance(answer, str):
        answer = str(answer) if answer is not None else ""

    text = _PREFIX_RE.sub("", answer.strip()).strip()

    normalized_json, is_json = _try_parse_json(text)
    numbers  = _extract_numbers(text)
    keywords = _extract_keywords(text)

    return {
        "normalized_text": text,
        "normalized_json": normalized_json,
        "numbers":         numbers,
        "keywords":        keywords,
        "is_json":         is_json,
    }


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------


def _try_parse_json(text: str) -> tuple[dict | None, bool]:
    """尝试将文本解析为 JSON 对象；返回 (json_dict_or_None, is_json_主体)。"""
    # 情况 1：整体是合法 JSON
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj, True
    except (json.JSONDecodeError, ValueError):
        pass

    # 情况 2：```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj, True
        except (json.JSONDecodeError, ValueError):
            pass

    # 情况 3：文本中第一个完整 { ... } 块（嵌入文本，不算主体 JSON）
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            obj = json.loads(m.group())
            if isinstance(obj, dict):
                return obj, False
        except (json.JSONDecodeError, ValueError):
            pass

    return None, False


def _extract_numbers(text: str) -> list[float]:
    """提取文本中所有数值（含负号、小数点）。"""
    result: list[float] = []
    for s in re.findall(r"-?\d+(?:\.\d+)?", text):
        try:
            result.append(float(s))
        except ValueError:
            pass
    return result


def _extract_keywords(text: str) -> list[str]:
    """提取小区ID、工单号、规则名、告警级别、时间片标识等关键词（去重保序）。"""
    kws: list[str] = []

    kws.extend(re.findall(r"CELL_[A-Z0-9_]+", text))
    kws.extend(re.findall(r"DAS_[A-Z0-9_]+", text))
    kws.extend(re.findall(r"TKT-\d+", text))
    kws.extend(re.findall(r"\bQ\d{3}\b", text))
    kws.extend(re.findall(r"规则[A-Z\d]+", text))

    for level in ("MAJOR", "CRITICAL", "WARNING", "MINOR"):
        if level in text:
            kws.append(level)
    for level_cn in ("严重告警", "一般告警", "提示告警", "告警级别"):
        if level_cn in text:
            kws.append(level_cn)

    kws.extend(re.findall(r"\bT[1-9]\d?\b", text))
    kws.extend(re.findall(r"阶段[一二三四五]", text))
    kws.extend(re.findall(r"\d{1,2}月\d{1,2}日", text))

    seen: set[str] = set()
    unique: list[str] = []
    for kw in kws:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)
    return unique
