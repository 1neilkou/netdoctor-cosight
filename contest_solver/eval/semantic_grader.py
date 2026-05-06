"""
semantic_grader.py — 答案语义评测模块

公开接口：
    grade_answer(answer_result: dict, question_item: dict) -> dict

输出结构：
    exact_match      : bool      — 与参考答案完全一致
    normalized_match : bool      — 标准化/语义层面基本匹配
    semantic_score   : float     — 语义相似度 [0, 1]
    matched_points   : list[str] — 命中的要点
    missing_points   : list[str] — 缺失的要点
    grading_method   : str       — 评分策略名称

评分策略（按题型）：
    JSON格式转换 / 文本信息抽取          → json_field_coverage
    简单计算 / 表格排序 / 材料问答        → numeric_match
    条件判断 / 多跳问答 / 数据与规则综合  → keyword_coverage
    复杂规划                             → plan_coverage
    通信常识问答 / 其他                   → token_overlap
"""
from __future__ import annotations

import re
from .answer_normalizer import normalize_answer

# ---------------------------------------------------------------------------
# 容差常量
# ---------------------------------------------------------------------------
_TOL_REL = 0.01   # 相对容差 1%
_TOL_ABS = 0.05   # 绝对容差（近零值用）

# ---------------------------------------------------------------------------
# 题型 → 策略集合
# ---------------------------------------------------------------------------
_JSON_TYPES    = {"JSON格式转换", "文本信息抽取"}
_NUMERIC_TYPES = {"简单计算", "表格排序", "材料问答"}
_KEYWORD_TYPES = {"条件判断", "多跳问答", "数据与规则综合"}
_PLAN_TYPES    = {"复杂规划"}

# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------


def grade_answer(answer_result: dict, question_item: dict) -> dict:
    """
    对单道题目的答案进行多维度语义评测。

    Args:
        answer_result: solve_question() 的输出（含 final_answer 字段）
        question_item: 原始题目记录（含 expected_answer / question_type）

    Returns:
        含 exact_match / normalized_match / semantic_score /
        matched_points / missing_points / grading_method 的字典。
    """
    final_answer    = answer_result.get("final_answer", "")
    expected_answer = question_item.get("expected_answer", "")
    question_type   = question_item.get("question_type", "")

    if not expected_answer:
        return _make_result(False, False, 0.0, [], [], "no_reference")

    exact_match = final_answer.strip() == expected_answer.strip()
    pred_norm   = normalize_answer(final_answer, question_type)
    ref_norm    = normalize_answer(expected_answer, question_type)

    if question_type in _JSON_TYPES:
        return _grade_json(pred_norm, ref_norm, exact_match)
    if question_type in _NUMERIC_TYPES:
        return _grade_numeric(pred_norm, ref_norm, exact_match)
    if question_type in _KEYWORD_TYPES:
        return _grade_keyword(pred_norm, ref_norm, exact_match)
    if question_type in _PLAN_TYPES:
        return _grade_plan(pred_norm, ref_norm, exact_match, question_item)
    return _grade_token_overlap(pred_norm, ref_norm, exact_match)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _make_result(
    exact_match: bool,
    normalized_match: bool,
    semantic_score: float,
    matched_points: list,
    missing_points: list,
    grading_method: str,
) -> dict:
    return {
        "exact_match":      exact_match,
        "normalized_match": normalized_match,
        "semantic_score":   round(max(0.0, min(1.0, semantic_score)), 3),
        "matched_points":   matched_points,
        "missing_points":   missing_points,
        "grading_method":   grading_method,
    }


def _values_match(ref_val, pred_val) -> bool:
    """数值或字符串宽松比较（含 1% 相对容差）。"""
    if ref_val == pred_val:
        return True
    try:
        rf, pf = float(ref_val), float(pred_val)
        if abs(rf) < 1e-9:
            return abs(pf) < _TOL_ABS
        return abs(rf - pf) / abs(rf) <= _TOL_REL
    except (TypeError, ValueError):
        pass
    try:
        return str(ref_val).strip().lower() == str(pred_val).strip().lower()
    except Exception:
        return False


def _num_close(a: float, b: float) -> bool:
    denom = max(abs(a), abs(b), 1e-9)
    return (abs(a - b) / denom <= _TOL_REL) or (abs(a - b) <= _TOL_ABS)


# ---------------------------------------------------------------------------
# 策略：JSON 字段覆盖率
# ---------------------------------------------------------------------------


def _grade_json(pred_norm: dict, ref_norm: dict, exact_match: bool) -> dict:
    ref_json  = ref_norm["normalized_json"]
    pred_json = pred_norm["normalized_json"]

    if ref_json is None:
        return _grade_token_overlap(pred_norm, ref_norm, exact_match, method="json→token_overlap")

    matched: list[str] = []
    missing: list[str] = []

    for key, ref_val in ref_json.items():
        if pred_json is not None and key in pred_json:
            if _values_match(ref_val, pred_json[key]):
                matched.append(key)
            else:
                missing.append(f"{key}(期望={ref_val}, 实际={pred_json[key]})")
        else:
            ref_str = str(ref_val).strip()
            if ref_str and ref_str in pred_norm["normalized_text"]:
                matched.append(f"{key}(文本命中)")
            else:
                missing.append(key)

    total = len(ref_json)
    score = len(matched) / total if total > 0 else 0.0
    return _make_result(exact_match, score >= 0.8, score, matched, missing, "json_field_coverage")


# ---------------------------------------------------------------------------
# 策略：数值匹配
# ---------------------------------------------------------------------------


def _grade_numeric(pred_norm: dict, ref_norm: dict, exact_match: bool) -> dict:
    ref_nums  = ref_norm["numbers"]
    pred_nums = pred_norm["numbers"]

    if not ref_nums:
        return _grade_token_overlap(pred_norm, ref_norm, exact_match, method="numeric→token_overlap")

    matched: list[str] = []
    missing: list[str] = []
    available = list(pred_nums)

    for rn in ref_nums:
        found = False
        for i, pn in enumerate(available):
            if _num_close(rn, pn):
                matched.append(str(rn))
                available.pop(i)
                found = True
                break
        if not found:
            missing.append(str(rn))

    score = len(matched) / len(ref_nums)
    return _make_result(exact_match, score >= 0.8, score, matched, missing, "numeric_match")


# ---------------------------------------------------------------------------
# 策略：关键词覆盖率
# ---------------------------------------------------------------------------


def _grade_keyword(pred_norm: dict, ref_norm: dict, exact_match: bool) -> dict:
    ref_kws   = ref_norm["keywords"]
    pred_text = pred_norm["normalized_text"]

    if not ref_kws:
        return _grade_token_overlap(pred_norm, ref_norm, exact_match, method="keyword→token_overlap")

    matched = [kw for kw in ref_kws if kw in pred_text]
    missing = [kw for kw in ref_kws if kw not in pred_text]
    score   = len(matched) / len(ref_kws)
    return _make_result(exact_match, score >= 0.6, score, matched, missing, "keyword_coverage")


# ---------------------------------------------------------------------------
# 策略：分阶段计划覆盖率
# ---------------------------------------------------------------------------


def _grade_plan(
    pred_norm: dict, ref_norm: dict, exact_match: bool, question_item: dict
) -> dict:
    trace_pts = question_item.get("expected_trace_points", [])
    pred_text = pred_norm["normalized_text"]
    ref_text  = ref_norm["normalized_text"]

    tp_matched: list[str] = []
    tp_missing: list[str] = []
    for pt in trace_pts:
        tokens = _key_tokens(pt)
        hit = sum(1 for t in tokens if t in pred_text)
        if tokens and hit / len(tokens) >= 0.5:
            tp_matched.append(pt[:30])
        else:
            tp_missing.append(pt[:30])

    tp_score = len(tp_matched) / len(trace_pts) if trace_pts else 1.0

    ref_phases = re.findall(r"阶段[一二三四]|\d{1,2}月\d{1,2}日", ref_text)
    if ref_phases:
        phase_hit   = sum(1 for p in ref_phases if p in pred_text)
        phase_score = phase_hit / len(ref_phases)
    else:
        phase_score = 1.0

    score = (tp_score + phase_score) / 2
    return _make_result(
        exact_match, score >= 0.5, score,
        tp_matched, tp_missing, "plan_coverage"
    )


def _key_tokens(text: str) -> list[str]:
    """从文本中提取数值、标识符、2-4 字中文片段。"""
    tokens: list[str] = []
    tokens.extend(re.findall(r"-?\d+(?:\.\d+)?", text))
    tokens.extend(re.findall(r"CELL_\w+|规则[A-Z\d]+|T[1-9]\d?", text))
    tokens.extend(re.findall(r"[一-鿿]{2,4}", text))
    return list(set(tokens))


# ---------------------------------------------------------------------------
# 策略：Token F1 重叠度（默认）
# ---------------------------------------------------------------------------


def _grade_token_overlap(
    pred_norm: dict, ref_norm: dict, exact_match: bool, method: str = "token_overlap"
) -> dict:
    ref_toks  = set(_tokenize(ref_norm["normalized_text"]))
    pred_toks = set(_tokenize(pred_norm["normalized_text"]))

    if not ref_toks:
        score = float(exact_match)
        return _make_result(exact_match, exact_match, score, [], [], method)

    inter     = ref_toks & pred_toks
    precision = len(inter) / len(pred_toks) if pred_toks else 0.0
    recall    = len(inter) / len(ref_toks)
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    matched = sorted(inter)[:10]
    missing = sorted(ref_toks - pred_toks)[:10]
    return _make_result(exact_match, f1 >= 0.5, f1, matched, missing, method)


def _tokenize(text: str) -> list[str]:
    """中文连续字符序列 + 英文/数字序列，过滤单字符英数。"""
    tokens: list[str] = []
    for m in re.finditer(r"[一-鿿]+|[a-zA-Z0-9_.%-]+", text):
        t = m.group().lower()
        if len(t) >= 2:
            tokens.append(t)
    return tokens
