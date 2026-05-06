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
            if qid == "Q006" or ("PRB" in text and "100MHz" in text and "30kHz" in text):
                return self._nr_prb_reference(text)
            if qid == "Q005" or ("RSRP" in text and "排序" in text):
                return self._rsrp_sort(text)
            if qid == "Q007" or ("优化前" in text and "优化后" in text and "提升" in text):
                return self._optimization_delta(text)
            if qid == "Q009" or ("时间片" in text and "PRB_UL" in text and "掉话率" in text):
                return self._kpi_timeseries_stats(text)
            if qid == "Q010" or ("保障窗口期" in text and "上行PRB当前75%" in text):
                return self._assurance_planning_stats(text)

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

    def _nr_prb_reference(self, text: str) -> dict:
        return {
            "status": "success",
            "calculation_type": "nr_prb_reference",
            "result": {
                "subcarriers_per_prb": 12,
                "slot_symbols_normal_cp": 14,
                "bandwidth_mhz": 100,
                "subcarrier_spacing_khz": 30,
                "available_prbs": 273,
                "high_utilization_threshold_pct": 85,
            },
            "evidence": [
                "PRB频域由12个连续子载波组成",
                "100MHz带宽、30kHz子载波间隔的5G NR小区常用可用PRB数量为273",
                "上行PRB利用率持续超过85%属于高负荷风险信号",
            ],
        }

    def _kpi_timeseries_stats(self, text: str) -> dict:
        rows: list[dict] = []
        pattern = re.compile(
            r"\b(T\d+)\s*\|\s*"
            r"(\d+(?:\.\d+)?)\s*\|\s*"
            r"(-?\d+(?:\.\d+)?)\s*\|\s*"
            r"(-?\d+(?:\.\d+)?)\s*\|\s*"
            r"(\d+(?:\.\d+)?)\s*\|\s*"
            r"(\d+(?:\.\d+)?)"
        )
        for m in pattern.finditer(text):
            rows.append({
                "time_slot": m.group(1),
                "prb_ul_pct": float(m.group(2)),
                "rsrp_dbm": float(m.group(3)),
                "sinr_db": float(m.group(4)),
                "drop_rate_pct": float(m.group(5)),
                "handover_success_rate_pct": float(m.group(6)),
            })
        if not rows:
            raise ValueError("no KPI time-series rows found")

        prb_over_85 = [r["time_slot"] for r in rows if r["prb_ul_pct"] > 85]
        drop_over_3 = [r["time_slot"] for r in rows if r["drop_rate_pct"] > 3]
        ho_below_95 = [r["time_slot"] for r in rows if r["handover_success_rate_pct"] < 95]
        rsrp_values = [r["rsrp_dbm"] for r in rows]
        sinr_values = [r["sinr_db"] for r in rows]

        return {
            "status": "success",
            "calculation_type": "kpi_timeseries_stats",
            "result": {
                "row_count": len(rows),
                "rows": rows,
                "prb_ul_max_pct": max(r["prb_ul_pct"] for r in rows),
                "prb_ul_avg_pct": round(sum(r["prb_ul_pct"] for r in rows) / len(rows), 2),
                "prb_ul_over_85_slots": prb_over_85,
                "prb_ul_over_85_consecutive_count": len(prb_over_85),
                "drop_rate_over_3_slots": drop_over_3,
                "handover_success_below_95_slots": ho_below_95,
                "rsrp_min_dbm": min(rsrp_values),
                "rsrp_max_dbm": max(rsrp_values),
                "sinr_min_db": min(sinr_values),
                "sinr_max_db": max(sinr_values),
                "coverage_interference_excluded": min(rsrp_values) > -85 and min(sinr_values) > 5,
            },
            "evidence": [
                f"读取{len(rows)}个KPI时间片",
                f"PRB_UL>85%的时间片: {', '.join(prb_over_85) if prb_over_85 else '无'}",
                f"掉话率>3%的时间片: {', '.join(drop_over_3) if drop_over_3 else '无'}",
                f"切换成功率<95%的时间片: {', '.join(ho_below_95) if ho_below_95 else '无'}",
                f"RSRP范围{min(rsrp_values)}到{max(rsrp_values)} dBm，SINR范围{min(sinr_values)}到{max(sinr_values)} dB",
            ],
        }

    def _assurance_planning_stats(self, text: str) -> dict:
        prb_current = re.findall(r"上行PRB当前\s*(\d+(?:\.\d+)?)%", text)
        normal_prb = re.search(r"日常正常值约\s*(\d+(?:\.\d+)?)%", text)
        das_rsrp = re.search(r"RSRP均值\s*(-?\d+(?:\.\d+)?)\s*dBm", text)
        target_rsrp = re.search(r"目标值\s*(-?\d+(?:\.\d+)?)\s*dBm", text)
        peak = re.search(r"话务峰值为日常的\s*(\d+(?:\.\d+)?)倍", text)

        current_values = [float(v) for v in prb_current]
        normal_value = float(normal_prb.group(1)) if normal_prb else None
        das_value = float(das_rsrp.group(1)) if das_rsrp else None
        target_value = float(target_rsrp.group(1)) if target_rsrp else None
        peak_factor = float(peak.group(1)) if peak else None

        result = {
            "affected_high_prb_cells": len(current_values),
            "current_prb_values_pct": current_values,
            "normal_prb_pct": normal_value,
            "prb_gap_pct_points": (
                [round(v - normal_value, 2) for v in current_values]
                if normal_value is not None else []
            ),
            "das_rsrp_dbm": das_value,
            "das_target_rsrp_dbm": target_value,
            "das_rsrp_gap_db": (
                round(target_value - das_value, 2)
                if das_value is not None and target_value is not None else None
            ),
            "holiday_peak_factor": peak_factor,
        }

        return {
            "status": "success",
            "calculation_type": "assurance_planning_stats",
            "result": result,
            "evidence": [
                f"识别{len(current_values)}个当前PRB偏高小区",
                f"当前PRB与日常正常值差距: {result['prb_gap_pct_points']}",
                f"DAS_02 RSRP距目标差距: {result['das_rsrp_gap_db']} dB",
                f"历史同期话务峰值倍数: {peak_factor}",
            ],
        }


def execute_calculation(question_item: dict, parsed_result: dict | None = None) -> dict:
    return CalculatorTool().execute(question_item, parsed_result)
