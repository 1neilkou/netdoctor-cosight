"""
answer_verifier.py — 答案校验工具

公开接口（新）：
    verify_answer(answer_result: dict, question_item: dict) -> dict

向后兼容：
    AnswerVerifier 类（供旧版代码使用）
"""

_REQUIRED_FIELDS = {
    "question_id", "level", "question_type",
    "final_answer", "reasoning_trace", "used_tools", "status",
}
_VALID_STATUSES = {"success", "partial", "failed", "error"}


def verify_answer(answer_result: dict, question_item: dict) -> dict:
    """
    校验 format_answer() 输出结构的完整性与合规性。

    检查项：
        1. 必需字段是否完整
        2. final_answer 是否非空
        3. reasoning_trace 是否非空（步骤数 ≥ 1）
        4. used_tools 是否为 list
        5. status 是否为合法值

    返回：
        {
            "is_valid":       bool,
            "missing_fields": list[str],
            "warnings":       list[str],
            "score":          float,   # 0.0 ~ 1.0
        }
    """
    warnings: list[str] = []
    score: float = 1.0

    # 1. 必需字段
    missing = [f for f in _REQUIRED_FIELDS if f not in answer_result]
    score -= 0.15 * len(missing)

    # 2. final_answer 非空
    final = answer_result.get("final_answer", "")
    if not isinstance(final, str) or not final.strip():
        warnings.append("final_answer 为空或非字符串")
        score -= 0.3

    # 3. reasoning_trace 非空
    trace = answer_result.get("reasoning_trace", [])
    if not isinstance(trace, list) or len(trace) == 0:
        warnings.append("reasoning_trace 为空列表")
        score -= 0.2
    elif len(trace) < 2:
        warnings.append(f"reasoning_trace 步骤数偏少（{len(trace)} 步）")
        score -= 0.05

    # 4. used_tools 类型
    used_tools = answer_result.get("used_tools")
    if not isinstance(used_tools, list):
        warnings.append("used_tools 不是 list 类型")
        score -= 0.1

    # 5. status 合法性
    status = answer_result.get("status", "")
    if status not in _VALID_STATUSES:
        warnings.append(f"status 非法值: '{status}'，合法值={sorted(_VALID_STATUSES)}")
        score -= 0.1

    score = round(max(0.0, min(1.0, score)), 3)
    is_valid = len(missing) == 0 and len(warnings) == 0

    return {
        "is_valid":       is_valid,
        "missing_fields": missing,
        "warnings":       warnings,
        "score":          score,
    }


# ---------------------------------------------------------------------------
# 向后兼容：保留 AnswerVerifier 类
# ---------------------------------------------------------------------------

class AnswerVerifier:
    """向后兼容包装器。"""

    def verify(self, predicted: str, expected: str) -> dict:
        predicted_clean = predicted.strip()
        expected_clean  = expected.strip()

        if predicted_clean == expected_clean:
            return {"is_correct": True, "match_type": "exact", "note": "完全匹配"}

        tokens = [t for t in expected_clean.split() if len(t) > 1]
        hit    = sum(1 for t in tokens if t in predicted_clean)
        ratio  = hit / len(tokens) if tokens else 0.0

        if ratio >= 0.8:
            return {"is_correct": True,  "match_type": "fuzzy", "note": f"关键词匹配率 {ratio:.0%}"}
        return     {"is_correct": False, "match_type": "miss",  "note": f"关键词匹配率 {ratio:.0%}，需人工复核"}
