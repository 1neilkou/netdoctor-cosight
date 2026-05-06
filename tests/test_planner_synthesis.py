"""
离线测试脚本：Planner Synthesis

读取已有的 JSONL 输出文件，调一次 Planner LLM，看能不能推出正确答案。
不启动 Agent，不跑完整 pipeline。
"""

import argparse
import json
import os
import sys
import types
from pathlib import Path

# 将父目录添加到 Python path，以便 import llm 模块
_parent_dir = Path(__file__).parent.parent
sys.path.insert(0, str(_parent_dir))

# Create tiny in-memory MCP modules before importing CoSight.py.
# The local environment may not have the optional "mcp" package.
mcp_stub = types.ModuleType("mcp")


class _DummyMCPTool:
    name = "dummy_mcp_tool"
    description = "Dummy MCP tool used only for import-time compatibility."
    inputSchema = {"type": "object", "properties": {}, "required": []}


class _DummyClientSession:
    def __init__(self, *args, **kwargs):
        pass


class _DummyStdioServerParameters:
    def __init__(self, *args, **kwargs):
        pass


def _dummy_stdio_client(*args, **kwargs):
    raise RuntimeError("MCP stdio client is not available")


# 注册 stub 模块
sys.modules["mcp"] = mcp_stub
sys.modules["mcp.server"] = types.ModuleType("mcp.server")
sys.modules["mcp.server.stdio"] = types.ModuleType("mcp.server.stdio")
sys.modules["mcp.server.stdio.stdio_server"] = types.ModuleType("mcp.server.stdio.stdio_server")

import httpx
from openai import OpenAI

from app.cosight.llm.chat_llm import ChatLLM


# ============================================================
# LLM 初始化函数（从 test_single.py 复制）
# ============================================================

def _env(name: str, default: str = None) -> str | None:
    return os.environ.get(name, default)


def build_llm(prefix: str = "") -> ChatLLM:
    """从环境变量构建 LLM。"""
    prefix_name = f"{prefix}_" if prefix else ""
    api_key = _env(f"{prefix_name}API_KEY") or _env("API_KEY")
    base_url = _env(f"{prefix_name}API_BASE_URL") or _env("API_BASE_URL")
    model = _env(f"{prefix_name}MODEL_NAME") or _env("MODEL_NAME")
    max_tokens_raw = _env(f"{prefix_name}MAX_TOKENS") or _env("MAX_TOKENS")
    temperature_raw = _env(f"{prefix_name}TEMPERATURE") or _env("TEMPERATURE")
    proxy = _env(f"{prefix_name}PROXY") or _env("PROXY")

    missing = [
        name for name, value in {
            "API_KEY": api_key,
            "API_BASE_URL": base_url,
            "MODEL_NAME": model,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required LLM environment variables: {', '.join(missing)}")

    max_tokens = int(max_tokens_raw) if max_tokens_raw else 8192
    temperature = float(temperature_raw) if temperature_raw else 0.0

    http_client_kwargs = {
        "headers": {"Content-Type": "application/json", "Authorization": api_key},
        "verify": False,
        "trust_env": False,
        "timeout": httpx.Timeout(connect=30.0, read=float(_env("LLM_TIMEOUT", "180")), write=30.0, pool=10.0),
    }
    if proxy:
        http_client_kwargs["proxy"] = proxy

    client = OpenAI(
        base_url=base_url,
        api_key=api_key,
        http_client=httpx.Client(**http_client_kwargs),
    )

    return ChatLLM(
        base_url=base_url,
        api_key=api_key,
        model=model,
        client=client,
        max_tokens=max_tokens,
        temperature=temperature,
    )


# ============================================================
# 核心逻辑
# ============================================================


def format_all_facts(step_facts: dict) -> str:
    """收集所有 step 的 facts，格式化为字符串。"""
    lines = []
    for step_title, facts in step_facts.items():
        if not facts:
            continue
        lines.append(f"[{step_title}]")
        for f in facts:
            key = f.get("key", "?")
            value = f.get("value", "?")
            line = f"  {key} = {value}"
            source = f.get("source")
            if source and source != "unknown":
                line += f"  (来源: {source})"
            confidence = f.get("confidence")
            if confidence:
                line += f"  (置信度: {confidence})"
            lines.append(line)
    return "\n".join(lines) if lines else "（无 facts）"


def synthesize_answer(llm, question: str, facts_summary: str) -> str:
    """调一次 Planner LLM 合成答案。"""
    prompt = f"""你是一个任务推理助手。根据以下已收集的信息，回答原始问题。

原始问题：
{question}

已收集的信息：
{facts_summary}

要求：
- 直接给出最终答案
- 答案尽量简洁，只输出答案本身
- 如果信息不足以回答问题，输出：INSUFFICIENT

只输出答案，不要解释。
"""
    messages = [{"role": "user", "content": prompt}]
    response = llm.chat_to_llm(messages)
    # chat_to_llm 返回的是 ChatCompletion 对象，需要取 content
    if hasattr(response, "content"):
        return response.content
    return str(response)


def load_jsonl(path: Path) -> list[dict]:
    """加载 JSONL 文件。"""
    items = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def normalize_answer(value: str) -> str:
    """标准化答案用于比较。"""
    import re
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t\r\n\"'`.,:;")


def main():
    parser = argparse.ArgumentParser(description="Planner Synthesis 离线测试")
    parser.add_argument(
        "--input",
        type=str,
        default="outputs/l1l2l3_sample3_router_opt.jsonl",
        help="optimized 模式的 JSONL 文件路径",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"错误：输入文件不存在: {input_path}")
        return

    # 加载 Planner LLM
    print("初始化 Planner LLM...")
    plan_llm = llm_for_plan()
    print(f"  model: {plan_llm.model}")
    print(f"  base_url: {plan_llm.base_url}")

    # 加载数据
    print(f"\n加载数据: {input_path}")
    tasks = load_jsonl(input_path)
    print(f"  共 {len(tasks)} 道题\n")

    # 结果统计
    original_correct_count = 0
    synthesis_correct_count = 0
    new_correct_tasks = []
    new_wrong_tasks = []

    # 打印表头
    print("-" * 120)
    print(f"{'task_id':<12} {'level':<6} {'question':<80} {'gold':<20} {'orig':<6} {'synth':<20} {'synth_ok':<8} {'facts':<6}")
    print("-" * 120)

    for task in tasks:
        task_id = task.get("task_id", "")
        question = task.get("question", "")
        gold_answer = task.get("gold_answer", "")
        level = task.get("level", "")
        step_facts = task.get("step_facts", {})

        # 第一步：收集所有 facts
        facts_summary = format_all_facts(step_facts)
        facts_count = sum(len(facts) for facts in step_facts.values())

        # 第二步：调 Planner LLM
        try:
            synthesis_prediction = synthesize_answer(plan_llm, question, facts_summary)
        except Exception as e:
            print(f"  [错误] LLM 调用失败: {e}")
            synthesis_prediction = "ERROR"

        # 第三步：对比结果
        original_correct = task.get("exact_match", False)
        synthesis_ok = normalize_answer(synthesis_prediction) == normalize_answer(gold_answer)

        # 统计
        if original_correct:
            original_correct_count += 1
        if synthesis_ok:
            synthesis_correct_count += 1

        # 检查新增对错
        if not original_correct and synthesis_ok:
            new_correct_tasks.append(task_id)
        elif original_correct and not synthesis_ok:
            new_wrong_tasks.append(task_id)

        # 打印每道题结果
        task_id_short = task_id[:8] if task_id else "?"
        question_short = question[:77] + "..." if len(question) > 80 else question
        gold_short = gold_answer[:17] + "..." if len(gold_answer) > 20 else gold_answer
        synth_short = synthesis_prediction[:17] + "..." if len(synthesis_prediction) > 20 else synthesis_prediction

        print(
            f"{task_id_short:<12} {level:<6} {question_short:<80} "
            f"{gold_short:<20} {'✓' if original_correct else '✗':<6} "
            f"{synth_short:<20} {'✓' if synthesis_ok else '✗':<8} {facts_count:<6}"
        )

    # 打印汇总
    total = len(tasks)
    print("-" * 120)
    print(f"\n汇总：")
    print(f"  原始准确率：{original_correct_count}/{total} ({100*original_correct_count/total:.1f}%)")
    print(f"  synthesis 准确率：{synthesis_correct_count}/{total} ({100*synthesis_correct_count/total:.1f}%)")
    print(f"  新增答对（原来错、synthesis 对）：{new_correct_tasks}")
    print(f"  新增答错（原来对、synthesis 错）：{new_wrong_tasks}")


if __name__ == "__main__":
    main()