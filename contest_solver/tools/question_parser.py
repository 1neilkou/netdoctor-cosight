import json
from pathlib import Path
from typing import Any


class QuestionParser:
    """从文件或列表中加载并解析题目，识别题型和关键字段。"""

    CATEGORY_KEYWORDS = {
        "multi_hop_reasoning": ["趋势", "持续", "过去", "变化", "根本原因", "分析"],
        "cross_indicator_diagnosis": ["相邻", "同时", "共同", "两个", "关联", "触发"],
        "threshold_judgment": ["判断", "阈值", "等级", "超出", "偏差", "严重程度"],
    }

    def load_from_file(self, path: str) -> list[dict]:
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"题目文件不存在: {path}")
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
        return [self.parse(q) for q in data]

    def parse(self, raw: dict) -> dict:
        question_text = raw.get("question", "")
        detected_category = raw.get("category") or self._detect_category(question_text)
        key_entities = self._extract_key_entities(raw.get("context", {}))

        return {
            "question_id": raw.get("question_id", "UNKNOWN"),
            "level": raw.get("level", "unknown"),
            "category": detected_category,
            "question": question_text,
            "context": raw.get("context", {}),
            "key_entities": key_entities,
        }

    def _detect_category(self, text: str) -> str:
        scores = {cat: 0 for cat in self.CATEGORY_KEYWORDS}
        for cat, keywords in self.CATEGORY_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    scores[cat] += 1
        best = max(scores, key=lambda c: scores[c])
        return best if scores[best] > 0 else "general"

    def _extract_key_entities(self, context: dict) -> list[str]:
        entities = []
        for key, value in context.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                entities.append(f"{key}={value}")
            elif isinstance(value, str):
                entities.append(f"{key}={value}")
        return entities[:10]  # 最多保留10个关键字段，避免上下文过长
