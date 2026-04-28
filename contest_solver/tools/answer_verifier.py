class AnswerVerifier:
    """对比模型输出答案与期望答案（占位实现，返回宽松匹配结果）。"""

    def verify(self, predicted: str, expected: str) -> dict:
        """
        返回包含 is_correct、match_type、note 的字典。
        当前为占位逻辑：仅做简单字符串包含检查。
        """
        predicted_clean = predicted.strip()
        expected_clean = expected.strip()

        if predicted_clean == expected_clean:
            return {"is_correct": True, "match_type": "exact", "note": "完全匹配"}

        key_tokens = [t for t in expected_clean.split() if len(t) > 1]
        hit = sum(1 for t in key_tokens if t in predicted_clean)
        ratio = hit / len(key_tokens) if key_tokens else 0.0

        if ratio >= 0.8:
            return {"is_correct": True, "match_type": "fuzzy", "note": f"关键词匹配率 {ratio:.0%}"}
        return {
            "is_correct": False,
            "match_type": "miss",
            "note": f"关键词匹配率 {ratio:.0%}，需人工复核",
        }
