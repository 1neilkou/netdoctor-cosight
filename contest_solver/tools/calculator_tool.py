from __future__ import annotations

import re


class CalculatorTool:
    """Basic numeric calculation tool used by the contest solver."""

    def add(self, values: list[float]) -> float:
        return round(sum(values), 6)

    def average(self, values: list[float], ndigits: int = 2) -> float:
        if not values:
            raise ValueError("values cannot be empty")
        return round(sum(values) / len(values), ndigits)

    def diff(self, a: float, b: float) -> float:
        return round(a - b, 6)

    def sort_pairs(
        self, pairs: list[tuple[str, float]], ascending: bool = True
    ) -> list[tuple[str, float]]:
        """Sort a list of (label, value) pairs by value."""
        return sorted(pairs, key=lambda x: x[1], reverse=not ascending)

    def execute(self, question_item: dict, parsed_result: dict | None = None) -> dict:
        """Execute the supported calculation for a sample question.

        This method intentionally reads only the question text and parsed metadata.
        It does not read or require expected_answer.
        """
        text = question_item.get("question", "")
        qid = question_item.get("question_id", "")
        qtype = question_item.get("question_type", "")

        try:
            if qid == "Q002" or ("吞吐量" in text and "平均值" in text and "差值" in text):
                return self._throughput_summary(text)
            if qid == "Q005" or ("RSRP" in text and "排序" in text):
                return self._rsrp_sort(text)
            if qid == "Q007" or ("优化前" in text and "优化后" in text and "提升" in text):
                return self._optimization_delta(text)

            return {
                "status": "failed",
                "calculation_type": "unsupported",
                "result": {},
                "evidence": [f"unsupported calculation for {qid or qtype}"],
            }
        except Exception as exc:
            return {
                "status": "failed",
                "calculation_type": "error",
                "result": {},
                "evidence": [f"{type(exc).__name__}: {exc}"],
            }

    def _throughput_summary(self, text: str) -> dict:
        pairs = [
            (cell, float(value))
            for cell, value in re.findall(r"(CELL_[A-Z0-9_]+)\s*[：:]\s*(-?\d+(?:\.\d+)?)\s*Mbps", text)
        ]
        if len(pairs) < 2:
            raise ValueError("no throughput cell values found")

        values = [v for _, v in pairs]
        total = round(self.add(values), 2)
        average = self.average(values, 2)
        max_cell, max_value = max(pairs, key=lambda x: x[1])
        min_cell, min_value = min(pairs, key=lambda x: x[1])
        spread = round(self.diff(max_value, min_value), 2)

        return {
            "status": "success",
            "calculation_type": "throughput_sum_average_spread",
            "result": {
                "values": [{"cell_id": cell, "throughput_mbps": value} for cell, value in pairs],
                "total_mbps": total,
                "average_mbps": average,
                "max_cell": max_cell,
                "max_mbps": max_value,
                "min_cell": min_cell,
                "min_mbps": min_value,
                "spread_mbps": spread,
            },
            "evidence": [
                f"读取{len(pairs)}个小区吞吐量: " + ", ".join(f"{c}={v}Mbps" for c, v in pairs),
                f"求和={total}Mbps",
                f"平均={average:.2f}Mbps",
                f"最大最小差值={spread}Mbps ({max_cell} {max_value} - {min_cell} {min_value})",
            ],
        }

    def _rsrp_sort(self, text: str) -> dict:
        pairs = [
            (cell, float(value))
            for cell, value in re.findall(r"(CELL_\d+)\s*\|\s*(-?\d+(?:\.\d+)?)", text)
        ]
        if len(pairs) < 2:
            pairs = [
                (cell, float(value))
                for cell, value in re.findall(r"(CELL_[A-Z0-9_]+)\s*[：:]\s*(-?\d+(?:\.\d+)?)\s*dBm", text)
            ]
        if len(pairs) < 2:
            raise ValueError("no RSRP table values found")

        sorted_pairs = self.sort_pairs(pairs, ascending=True)
        return {
            "status": "success",
            "calculation_type": "rsrp_ascending_sort",
            "result": {
                "sorted_cells": [
                    {"rank": idx + 1, "cell_id": cell, "rsrp_dbm": int(value) if value.is_integer() else value}
                    for idx, (cell, value) in enumerate(sorted_pairs)
                ]
            },
            "evidence": [
                f"读取{len(pairs)}个RSRP值",
                "按RSRP从小到大排序: "
                + " -> ".join(f"{cell}({int(value) if value.is_integer() else value}dBm)" for cell, value in sorted_pairs),
            ],
        }

    def _optimization_delta(self, text: str) -> dict:
        handover = re.search(r"切换成功率为\s*(\d+(?:\.\d+)?)%.*?切换成功率提升至\s*(\d+(?:\.\d+)?)%", text, re.S)
        drop = re.search(r"掉话率为\s*(\d+(?:\.\d+)?)%.*?掉话率降低至\s*(\d+(?:\.\d+)?)%", text, re.S)
        complaints = re.search(r"投诉日均\s*(\d+(?:\.\d+)?)条.*?投诉日均降至\s*(\d+(?:\.\d+)?)条", text, re.S)
        if not (handover and drop and complaints):
            raise ValueError("optimization before/after metrics are incomplete")

        handover_before, handover_after = map(float, handover.groups())
        drop_before, drop_after = map(float, drop.groups())
        complaint_before, complaint_after = map(float, complaints.groups())

        handover_delta = round(handover_after - handover_before, 2)
        drop_delta = round(drop_before - drop_after, 2)
        complaint_delta = round(complaint_before - complaint_after, 2)

        return {
            "status": "success",
            "calculation_type": "optimization_before_after_delta",
            "result": {
                "handover_success_rate_delta_pct_points": handover_delta,
                "drop_rate_delta_pct_points": drop_delta,
                "daily_complaints_delta": complaint_delta,
                "before": {
                    "handover_success_rate_pct": handover_before,
                    "drop_rate_pct": drop_before,
                    "daily_complaints": complaint_before,
                },
                "after": {
                    "handover_success_rate_pct": handover_after,
                    "drop_rate_pct": drop_after,
                    "daily_complaints": complaint_after,
                },
            },
            "evidence": [
                f"切换成功率: {handover_after}-{handover_before}={handover_delta}个百分点",
                f"掉话率: {drop_before}-{drop_after}={drop_delta}个百分点",
                f"用户投诉日均: {complaint_before}-{complaint_after}={complaint_delta}条",
            ],
        }


def execute_calculation(question_item: dict, parsed_result: dict | None = None) -> dict:
    return CalculatorTool().execute(question_item, parsed_result)
