class CalculatorTool:
    """基础数值计算工具。"""

    def add(self, values: list[float]) -> float:
        return round(sum(values), 6)

    def average(self, values: list[float], ndigits: int = 2) -> float:
        if not values:
            raise ValueError("values 不能为空")
        return round(sum(values) / len(values), ndigits)

    def diff(self, a: float, b: float) -> float:
        return round(a - b, 6)

    def sort_pairs(
        self, pairs: list[tuple[str, float]], ascending: bool = True
    ) -> list[tuple[str, float]]:
        """对 (label, value) 列表排序，返回排序后的列表。"""
        return sorted(pairs, key=lambda x: x[1], reverse=not ascending)
