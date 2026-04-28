class TaskPlanner:
    """将一道题目拆解为有序的子任务列表（占位实现）。"""

    def plan(self, question: dict) -> list[dict]:
        """返回子任务列表，每项含 task_id、description、required_tool。"""
        return [
            {
                "task_id": "T1",
                "description": "解析题目，提取关键字段",
                "required_tool": "question_parser",
            },
            {
                "task_id": "T2",
                "description": "执行核心推理或计算",
                "required_tool": None,
            },
            {
                "task_id": "T3",
                "description": "格式化并输出最终答案",
                "required_tool": "answer_formatter",
            },
        ]
