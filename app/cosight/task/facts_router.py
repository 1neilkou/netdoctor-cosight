"""Rule-based router for facts quality evaluation.

在 Actor 输出 facts 之后、Planner 决定下一步之前，
用规则快速判断该做什么，只有规则判断不了的情况才让 Planner 调 LLM。
"""

import re
from typing import Any

from app.common.logger_util import logger


# ============================================================
# 第一部分：关键词提取函数
# ============================================================

def extract_keywords(text: str) -> set:
    """从文本中提取关键词（去掉停用词）。
    
    停用词列表：常见英文停用词 + 短词（<3字符）
    这样可以减少噪音，提高 relevance 计算的准确性。
    """
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were",
        "how", "what", "when", "where", "who", "which",
        "many", "much", "did", "do", "does", "by", "in",
        "of", "to", "and", "or", "for", "with", "from",
        "that", "this", "it", "on", "at", "as", "be"
    }
    # 只保留 3 字符以上的英文单词
    words = re.findall(r'[a-zA-Z]{3,}', text.lower())
    return {w for w in words if w not in stopwords}


# ============================================================
# 第二部分：evaluate_facts_quality()
# ============================================================

def evaluate_facts_quality(
    plan,
    step_index: int,
    question: str
) -> dict:
    """评估当前 step 的 facts 质量。
    
    用规则判断 facts 的：
    - fill_rate: 当前 step 有 facts 的比例
    - relevance_rate: facts 和 question 关键词重叠的比例
    - sourced_rate: source 不是 unknown/fallback 的比例
    - has_final_answer: 是否有 key==final_answer 的 fact
    
    返回诊断结果和修复建议。
    """
    # ---- 1. 获取当前 step 的 facts ----
    step_facts = _get_step_facts(plan, step_index)
    
    # ---- 2. 计算 fill_rate ----
    # fill_rate = 已有 facts 的 step 数量 / plan 总 step 数量
    # 这样可以反映整个 plan 的 facts 填充程度，而不是单个 step 的二值结果
    all_steps = _safe_list(getattr(plan, "steps", []))
    total_steps = len(all_steps)
    if total_steps > 0:
        filled_steps = 0
        for idx, step in enumerate(all_steps):
            step_facts_list = _safe_list(getattr(plan, "step_facts", {}).get(step, []))
            if step_facts_list:
                filled_steps += 1
        fill_rate = filled_steps / total_steps
    else:
        fill_rate = 0.0
    
    # ---- 3. 计算 relevance_rate ----
    # relevance_rate = facts 里和 question 关键词重叠的比例
    # 只有当有 facts 且 question 有关键词时才计算
    question_keywords = extract_keywords(question)
    if question_keywords and step_facts:
        # 统计有多少 fact 的 key 或 value 包含 question 的关键词
        relevant_count = 0
        for fact in step_facts:
            fact_text = f"{fact.get('key', '')} {fact.get('value', '')}".lower()
            fact_keywords = extract_keywords(fact_text)
            # 如果有交集就算相关
            if question_keywords & fact_keywords:
                relevant_count += 1
        relevance_rate = relevant_count / len(step_facts) if step_facts else 0.0
    else:
        relevance_rate = 0.0
    
    # ---- 4. 计算 sourced_rate ----
    # sourced_rate = source 不是 unknown/fallback 的比例
    # unknown/fallback 说明来源不可信，可能是编的
    if step_facts:
        sourced_count = 0
        for fact in step_facts:
            source = str(fact.get("source", "")).lower()
            # 排除 unknown、fallback、auto_summary 等不可信来源
            if source not in {"unknown", "fallback", "auto_summary", ""}:
                sourced_count += 1
        sourced_rate = sourced_count / len(step_facts)
    else:
        sourced_rate = 0.0
    
    # ---- 5. 检查是否有 final_answer ----
    # 有 final_answer 说明 step 已经产出最终答案
    has_final_answer = any(
        str(fact.get("key", "")).lower() == "final_answer"
        for fact in step_facts
    )
    
    # ---- 6. 生成 diagnosis ----
    # 按顺序判断，第一个命中就返回
    diagnosis = _compute_diagnosis(step_facts, fill_rate, relevance_rate, sourced_rate)
    
    # ---- 7. 生成 recommendation ----
    # 根据 diagnosis 和工具调用情况生成建议
    recommendation = _compute_recommendation(
        plan, step_index, diagnosis, has_final_answer
    )
    
    return {
        "fill_rate": fill_rate,
        "relevance_rate": relevance_rate,
        "sourced_rate": sourced_rate,
        "has_final_answer": has_final_answer,
        "diagnosis": diagnosis,
        "recommendation": recommendation
    }


def _get_step_facts(plan, step_index: int) -> list:
    """安全获取 step 的 facts。"""
    steps = _safe_list(getattr(plan, "steps", []))
    if 0 <= step_index < len(steps):
        step = steps[step_index]
        return _safe_list(getattr(plan, "step_facts", {}).get(step, []))
    return []


def _safe_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return []


def _compute_diagnosis(
    step_facts: list,
    fill_rate: float,
    relevance_rate: float,
    sourced_rate: float
) -> str:
    """根据 facts 情况计算 diagnosis。
    
    判断顺序（优先级从高到低）：
    1. empty → facts 为空
    2. process_only → 只有过程记录，没有结果
    3. relevant_but_unsourced → 有关联但来源不可信
    4. good → 以上都不符合
    """
    # ---- 诊断 1: empty ----
    # 如果 facts 为空列表，说明 Actor 什么都没留下
    # 可能是工具没跑成功，或者 Actor 没理解要输出 facts
    if not step_facts or fill_rate == 0:
        return "empty"
    
    # ---- 诊断 2: process_only ----
    # 检查所有 fact 的 key 是否都包含过程关键词
    # 如果都是 visited、fetched、searched、done 等，说明只记录了过程
    # 这类 facts 对下游没有实际价值
    process_keywords = {
        "visited", "fetched", "searched", "done", "started",
        "tried", "called", "executed", "ran", "attempted",
        "processed", "loading", "downloading", "reading"
    }
    all_keys = [str(fact.get("key", "")).lower() for fact in step_facts]
    if all(any(pk in key for pk in process_keywords) for key in all_keys):
        return "process_only"
    
    # ---- 诊断 3: relevant_but_unsourced ----
    # 有关联（relevance_rate >= 0.2）但来源不可信（sourced_rate < 0.3）
    # 这类 facts 可能是 Actor 编的，需要验证
    if relevance_rate >= 0.2 and sourced_rate < 0.3:
        return "relevant_but_unsourced"
    
    # ---- 诊断 4: good ----
    # 以上都不符合，说明 facts 质量OK
    return "good"


def _compute_recommendation(
    plan,
    step_index: int,
    diagnosis: str,
    has_final_answer: bool
) -> str:
    """根据 diagnosis 和工具调用情况生成建议。
    
    recommendation 取值：
    - fix_tools → diagnosis == "empty" 且有工具失败
    - fix_actor_prompt → diagnosis == "process_only" 或 "empty" 且无工具失败
    - fix_final_compile → diagnosis == "good" 且有 final_answer
    - ok → diagnosis == "good" 且答案已正确（外部传入）
    """
    # ---- 获取当前 step 的工具调用情况 ----
    step_tool_calls = _get_step_tool_calls(plan, step_index)
    
    # 统计失败的工具
    failed_tools = {
        c.get("tool") for c in step_tool_calls
        if c.get("status") == "failed"
    }
    all_tools = {
        c.get("tool") for c in step_tool_calls
    }
    
    # ---- recommendation: fix_tools ----
    # 如果 facts 为空，但有工具失败，说明是工具问题不是 prompt 问题
    # 建议修复工具配置或工具本身
    if diagnosis == "empty" and failed_tools:
        return "fix_tools"
    
    # ---- recommendation: fix_actor_prompt ----
    # 如果是 process_only（只记录过程）或 empty（无facts）且没有工具失败
    # 说明是 Actor prompt 没有要求输出结构化 facts
    if diagnosis in {"process_only", "empty"}:
        return "fix_actor_prompt"
    
    # ---- recommendation: fix_final_compile ----
    # 如果 diagnosis == "good" 且有 final_answer
    # 说明当前 step 已经产出答案，可能需要最终整理
    if diagnosis == "good" and has_final_answer:
        return "fix_final_compile"
    
    # ---- recommendation: ok ----
    # 如果 diagnosis == "good" 且没有 final_answer
    # 说明 facts 正常，继续流程即可
    if diagnosis == "good":
        return "ok"
    
    # ---- 默认: ok ----
    # 其他情况默认继续
    return "ok"


def _get_step_tool_calls(plan, step_index: int) -> list:
    """安全获取 step 的工具调用记录。"""
    steps = _safe_list(getattr(plan, "steps", []))
    if 0 <= step_index < len(steps):
        step = steps[step_index]
        return _safe_list(getattr(plan, "step_tool_calls", {}).get(step, []))
    return []


# ============================================================
# 第三部分：route() - 路由决策
# ============================================================

def route(plan, step_index: int, question: str) -> str:
    """根据 facts 质量做路由决策。
    
    返回值：
    - next_step → 正常继续，调度下一个 step
    - retry → 重置当前 step，换工具重跑
    - verify → 当前 step 结果不可信，需要验证
    - replan → 规则判断不了，交给 Planner LLM
    
    决策规则（按优先级）：
    1. 熔断：retry_count >= 2 或 replan_count >= 1 → next_step
    2. diagnosis == good → next_step
    3. diagnosis == empty → 根据工具失败情况判断 retry/replan/next_step
    4. diagnosis == process_only → next_step（记录警告）
    5. diagnosis == relevant_but_unsourced → 下游步骤>=2 则 verify，否则 next_step
    """
    # ---- 1. 熔断：防止无限循环 ----
    # 如果同一个 step 已经重试过 2 次了，放弃重试，继续走
    retry_count = getattr(plan, "retry_count", {}) or {}
    if retry_count.get(step_index, 0) >= 2:
        logger.info(f"[router] step {step_index}: retry_count >= 2, 熔断返回 next_step")
        return "next_step"
    
    # 如果已经 replan 过一次了，不再 replan
    replan_count = getattr(plan, "replan_count", 0) or 0
    if replan_count >= 1:
        logger.info(f"[router] step {step_index}: replan_count >= 1, 熔断返回 next_step")
        return "next_step"
    
    # ---- 2. 调用 evaluate_facts_quality ----
    quality = evaluate_facts_quality(plan, step_index, question)
    logger.info(f"[router] step {step_index} quality: {quality}")
    
    # ---- 3. 根据 diagnosis 做路由决策 ----
    diagnosis = quality["diagnosis"]
    
    # ---- 3.1 diagnosis == good：直接继续 ----
    if diagnosis == "good":
        return "next_step"
    
    # ---- 3.2 diagnosis == empty：看能不能重试 ----
    if diagnosis == "empty":
        return _route_empty(plan, step_index)
    
    # ---- 3.3 diagnosis == process_only：继续走，但记录警告 ----
    if diagnosis == "process_only":
        logger.warning(
            f"[router] step {step_index}: facts 只有过程记录，"
            f"建议改 Actor prompt"
        )
        return "next_step"
    
    # ---- 3.4 diagnosis == relevant_but_unsourced ----
    if diagnosis == "relevant_but_unsourced":
        return _route_relevant_but_unsourced(plan, step_index)
    
    # ---- 4. 默认继续 ----
    return "next_step"


def _route_empty(plan, step_index: int) -> str:
    """处理 diagnosis == empty 的情况。
    
    决策逻辑：
    - 如果有工具失败，且不是所有工具都失败 → retry
    - 如果所有工具都失败 → replan（规则处理不了）
    - 如果没有工具调用记录 → next_step（Actor 根本没跑）
    """
    # 获取工具调用记录
    step_tool_calls = _get_step_tool_calls(plan, step_index)
    
    failed_tools = {
        c.get("tool") for c in step_tool_calls
        if c.get("status") == "failed"
    }
    all_tools = {
        c.get("tool") for c in step_tool_calls
    }
    
    # 情况 1: 有工具失败，且不是所有工具都失败 → retry
    if failed_tools and failed_tools != all_tools:
        logger.info(
            f"[router] step {step_index}: 部分工具失败 {failed_tools}，"
            f"尝试 retry"
        )
        return "retry"
    
    # 情况 2: 所有工具都失败 → replan
    if len(failed_tools) > 0 and len(all_tools) > 0 and failed_tools == all_tools:
        logger.info(
            f"[router] step {step_index}: 所有工具都失败 {failed_tools}，"
            f"交给 Planner replan"
        )
        return "replan"
    
    # 情况 3: 没有工具调用记录 → next_step
    # （Actor 根本没跑，可能是依赖没满足）
    logger.info(
        f"[router] step {step_index}: 无工具调用记录，"
        f"继续下一步"
    )
    return "next_step"


def _route_relevant_but_unsourced(plan, step_index: int) -> str:
    """处理 diagnosis == relevant_but_unsourced 的情况。
    
    决策逻辑：
    - 只有下游步骤多（>=2）才值得 verify
    - 否则继续走
    """
    downstream = _get_downstream_steps(plan, step_index)
    
    if len(downstream) >= 2:
        logger.info(
            f"[router] step {step_index}: facts 有关联但来源不可信，"
            f"下游步骤 {downstream}，需要 verify"
        )
        return "verify"
    
    logger.info(
        f"[router] step {step_index}: facts 有关联但来源不可信，"
        f"下游步骤少（{len(downstream)}），跳过 verify"
    )
    return "next_step"


def _get_downstream_steps(plan, step_index: int) -> list:
    """获取下游依赖步骤。"""
    from app.cosight.task.fact_supervisor import get_downstream_steps
    return get_downstream_steps(plan, step_index)