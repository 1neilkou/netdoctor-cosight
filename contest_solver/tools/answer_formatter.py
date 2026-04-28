import json


class AnswerFormatter:
    """将解题结果格式化为统一输出结构。"""

    def format(
        self,
        question_id: str,
        level: str,
        final_answer: str,
        reasoning_trace: list[dict],
    ) -> dict:
        return {
            "question_id": question_id,
            "level": level,
            "final_answer": final_answer,
            "reasoning_trace": reasoning_trace,
        }

    def to_json(self, result: dict, indent: int = 2) -> str:
        return json.dumps(result, ensure_ascii=False, indent=indent)

    def print_result(self, result: dict) -> None:
        sep = "=" * 60
        print(sep)
        print(f"  题目 ID : {result['question_id']}  |  难度: {result['level']}")
        print(sep)
        print("\n【解题轨迹】")
        for step in result["reasoning_trace"]:
            print(f"  Step {step['step']} [{step['step_type']}] {step['step_name']}")
            content = step["content"]
            if isinstance(content, list):
                for item in content:
                    print(f"    - {item}")
            elif isinstance(content, dict):
                for k, v in content.items():
                    print(f"    {k}: {v}")
            else:
                print(f"    {content}")
        print(f"\n【最终答案】\n  {result['final_answer']}")
        print()
