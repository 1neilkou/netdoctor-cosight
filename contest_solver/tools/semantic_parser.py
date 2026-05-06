"""
semantic_parser.py — LLM 语义解析模块

公开接口：
    semantic_parse_question(question_item, rule_result=None) -> dict
    fallback_semantic_parse(question_item, reason=None, debug=None) -> dict

所有返回结构均包含：
    question_goal / semantic_question_type / required_capabilities /
    implicit_constraints / suggested_tools / answer_format / confidence /
    source ("llm" | "fallback") / fallback_reason (None | str) /
    debug (dict)

环境变量（均从 .env 或系统环境读取，缺失时自动 fallback）：
    API_KEY       — DeepSeek API 密钥（仅检测是否存在，不输出原文）
    API_BASE_URL  — API 根地址，含 /v1（默认 https://api.deepseek.com/v1）
    MODEL_NAME    — 模型名称（默认 deepseek-chat）
    MAX_TOKENS    — 最大输出 token 数（默认 512）
    TEMPERATURE   — 温度参数（默认 0.2）
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

# .env 加载：文件路径为 项目根目录/.env
# semantic_parser.py 位于 <root>/contest_solver/tools/ → parents[2] = 项目根
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env", override=False)
except ImportError:
    pass  # python-dotenv 不可用时跳过，依赖系统环境变量

# ---------------------------------------------------------------------------
# 题型 → 能力 / 格式 / 建议工具 映射表
# ---------------------------------------------------------------------------

_TYPE_TO_CAPABILITIES: dict[str, list[str]] = {
    "文本信息抽取": ["text_extraction"],
    "简单计算":     ["calculation"],
    "条件判断":     ["rule_evaluation"],
    "JSON格式转换": ["format_conversion"],
    "表格排序":     ["calculation", "sorting"],
    "通信常识问答": ["domain_knowledge"],
    "材料问答":     ["reading_comprehension"],
    "多跳问答":     ["multi_hop_reasoning", "rule_evaluation"],
    "数据与规则综合": ["calculation", "rule_evaluation"],
    "复杂规划":     ["planning", "rule_evaluation", "calculation"],
}

_TYPE_TO_FORMAT: dict[str, str] = {
    "文本信息抽取": "JSON 对象（含 cell_id 等字段）",
    "简单计算":     "数值结果（含单位）",
    "条件判断":     "布尔判断 + 原因说明",
    "JSON格式转换": "JSON 对象",
    "表格排序":     "排序列表",
    "通信常识问答": "文字说明",
    "材料问答":     "文字分析",
    "多跳问答":     "分步推理结论",
    "数据与规则综合": "分步推理结论",
    "复杂规划":     "分阶段操作计划",
}

_TYPE_TO_TOOLS: dict[str, list[str]] = {
    "文本信息抽取": ["question_parser", "answer_formatter"],
    "简单计算":     ["calculator_tool"],
    "条件判断":     ["rule_evaluator", "question_parser"],
    "JSON格式转换": ["answer_formatter"],
    "表格排序":     ["calculator_tool"],
    "通信常识问答": [],
    "材料问答":     [],
    "多跳问答":     ["question_parser", "rule_evaluator", "trace_recorder"],
    "数据与规则综合": ["calculator_tool", "rule_evaluator", "trace_recorder"],
    "复杂规划":     ["task_planner", "trace_recorder", "answer_formatter"],
}

_VALID_TOOLS = {
    "question_parser", "calculator_tool", "rule_evaluator",
    "trace_recorder",  "answer_verifier", "task_planner", "answer_formatter",
}

# ---------------------------------------------------------------------------
# 内部异常：携带 debug 状态，供 semantic_parse_question 捕获
# ---------------------------------------------------------------------------

class _LLMError(Exception):
    def __init__(self, fallback_reason: str, debug: dict):
        super().__init__(fallback_reason)
        self.fallback_reason = fallback_reason
        self.debug = debug

# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def fallback_semantic_parse(
    question_item: dict,
    reason: str | None = None,
    debug: dict | None = None,
) -> dict:
    """规则推断语义解析结构，始终成功，不调用任何外部服务。"""
    qtype = question_item.get("question_type", "")
    level = question_item.get("level", 1)
    text  = question_item.get("question", "")

    capabilities = list(_TYPE_TO_CAPABILITIES.get(qtype, ["reading_comprehension"]))
    fmt          = _TYPE_TO_FORMAT.get(qtype, "文字说明")
    tools        = list(_TYPE_TO_TOOLS.get(qtype, []))

    implicit_constraints: list[str] = []
    if "%" in text:
        implicit_constraints.append("涉及百分比数值比较")
    if any(w in text for w in ["阈值", "规则", "告警"]):
        implicit_constraints.append("需要规则条件判断")
    if any(w in text for w in ["持续", "连续"]):
        implicit_constraints.append("含时间序列约束")
    if level >= 2:
        implicit_constraints.append("需要多步推理")

    goal = (text[:40].rstrip("，。") + "…") if len(text) > 40 else text

    return {
        "question_goal":          goal,
        "semantic_question_type": qtype,
        "required_capabilities":  capabilities,
        "implicit_constraints":   implicit_constraints,
        "suggested_tools":        tools,
        "answer_format":          fmt,
        "confidence":             0.6,
        "source":                 "fallback",
        "fallback_reason":        reason,
        "debug":                  debug or {},
    }


def semantic_parse_question(
    question_item: dict,
    rule_result: dict | None = None,
) -> dict:
    """
    调用 DeepSeek LLM 进行语义解析。

    返回结果始终包含 source / fallback_reason / debug 三个诊断字段。
    任何失败路径均返回 fallback，不抛出异常。
    """
    api_key  = os.environ.get("API_KEY", "").strip()
    base_url = os.environ.get("API_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    model    = os.environ.get("MODEL_NAME",   "deepseek-chat")

    base_debug: dict = {
        "api_key_loaded":      bool(api_key),
        "base_url_loaded":     bool(os.environ.get("API_BASE_URL")),
        "model_name":          model,
        "http_status":         None,
        "json_parse_success":  False,
        "raw_response_preview": "",
    }

    if not api_key:
        return fallback_semantic_parse(
            question_item,
            reason="missing_api_key",
            debug=base_debug,
        )

    max_tok = int(os.environ.get("MAX_TOKENS",   "512"))
    temp    = float(os.environ.get("TEMPERATURE", "0.2"))

    try:
        result = _call_llm(
            question_item, rule_result,
            api_key, base_url, model, max_tok, temp,
            base_debug,
        )
        return result
    except _LLMError as e:
        return fallback_semantic_parse(
            question_item,
            reason=e.fallback_reason,
            debug=e.debug,
        )
    except Exception as e:
        base_debug["raw_response_preview"] = f"unexpected: {type(e).__name__}: {e}"
        return fallback_semantic_parse(
            question_item,
            reason=f"unexpected_error:{type(e).__name__}",
            debug=base_debug,
        )


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------

def _call_llm(
    question_item: dict,
    rule_result: dict | None,
    api_key: str,
    base_url: str,
    model: str,
    max_tokens: int,
    temperature: float,
    debug: dict,
) -> dict:
    """
    构造 prompt → HTTP POST → 解析 JSON → 返回完整结果。
    失败时 raise _LLMError（携带 debug 状态），由上层转为 fallback。

    URL 构造规则：
        base_url 已包含 /v1（如 https://api.deepseek.com/v1），
        直接拼接 /chat/completions，不再额外添加 /v1。
    """
    import urllib.error
    import urllib.request

    qtype = question_item.get("question_type", "")
    level = question_item.get("level", 1)
    text  = question_item.get("question", "")

    rule_hint = ""
    if rule_result and rule_result.get("triggered_rules"):
        rule_hint = f"\n已识别的规则触发：{rule_result['triggered_rules']}"

    prompt = (
        "你是一个5G网络运维题目解析专家。对以下题目进行语义分析，"
        "仅输出 JSON，不要任何解释文字。\n\n"
        f"题目类型：{qtype}\n"
        f"难度级别：L{level}\n"
        f"题目内容：{text}"
        f"{rule_hint}\n\n"
        "请输出如下 JSON 结构（不要添加其他字段）：\n"
        "{\n"
        '  "question_goal": "一句话概括解题目标（20字以内）",\n'
        '  "semantic_question_type": "对题型的语义化描述",\n'
        '  "required_capabilities": ["能力1", "能力2"],\n'
        '  "implicit_constraints": ["隐含约束1", "隐含约束2"],\n'
        '  "suggested_tools": ["tool_name1"],\n'
        '  "answer_format": "期望的答案格式描述",\n'
        '  "confidence": 0.85\n'
        "}\n\n"
        "suggested_tools 只能从以下列表中选择：\n"
        "question_parser, calculator_tool, rule_evaluator, "
        "trace_recorder, answer_verifier, task_planner, answer_formatter"
    )

    payload = json.dumps({
        "model":       model,
        "messages":    [{"role": "user", "content": prompt}],
        "max_tokens":  max_tokens,
        "temperature": temperature,
    }).encode("utf-8")

    # base_url 已含 /v1（从 .env API_BASE_URL），直接加 /chat/completions
    url = f"{base_url}/chat/completions"

    req = urllib.request.Request(
        url,
        data    = payload,
        headers = {
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method = "POST",
    )

    # ---- HTTP 请求 --------------------------------------------------------
    raw_body = b""
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            status = resp.getcode()
            raw_body = resp.read()
    except urllib.error.HTTPError as e:
        status = e.code
        raw_body = e.read() or b""
        debug["http_status"] = status
        debug["raw_response_preview"] = raw_body.decode("utf-8", errors="replace")[:200]
        raise _LLMError(f"http_error_{status}", debug) from e
    except Exception as e:
        debug["raw_response_preview"] = f"{type(e).__name__}: {e}"
        raise _LLMError(f"api_request_failed:{type(e).__name__}", debug) from e

    debug["http_status"] = status
    raw_str = raw_body.decode("utf-8", errors="replace")
    debug["raw_response_preview"] = raw_str[:200]

    if status != 200:
        raise _LLMError(f"http_error_{status}", debug)

    if not raw_str.strip():
        raise _LLMError("empty_response", debug)

    # ---- 解析响应体 -------------------------------------------------------
    try:
        body = json.loads(raw_str)
        content = body["choices"][0]["message"]["content"].strip()
    except Exception as e:
        raise _LLMError("invalid_json_response", debug) from e

    if not content:
        raise _LLMError("empty_response", debug)

    # ---- 从 content 提取 JSON 对象 ----------------------------------------
    parsed = _extract_json(content)
    if parsed is None:
        debug["raw_response_preview"] = content[:200]
        raise _LLMError("invalid_json_response", debug)

    debug["json_parse_success"] = True

    # ---- 字段校验与修正 ---------------------------------------------------
    _required_fields = {
        "question_goal", "semantic_question_type", "required_capabilities",
        "implicit_constraints", "suggested_tools", "answer_format", "confidence",
    }
    missing = _required_fields - set(parsed.keys())
    if missing:
        raise _LLMError("invalid_json_response", debug)

    for field in ("required_capabilities", "implicit_constraints", "suggested_tools"):
        if not isinstance(parsed[field], list):
            parsed[field] = [str(parsed[field])]

    parsed["suggested_tools"] = [
        t for t in parsed["suggested_tools"] if t in _VALID_TOOLS
    ]

    try:
        parsed["confidence"] = max(0.0, min(1.0, float(parsed["confidence"])))
    except (TypeError, ValueError):
        parsed["confidence"] = 0.8

    parsed["source"]          = "llm"
    parsed["fallback_reason"] = None
    parsed["debug"]           = debug
    return parsed


def _extract_json(text: str) -> dict | None:
    """
    从 LLM 返回文本中提取第一个 JSON 对象，支持三种情况：
      1. 纯 JSON
      2. ```json ... ``` 包裹
      3. 文本中第一个 { ... } 块
    """
    # 情况 1：整体就是合法 JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 情况 2：```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 情况 3：贪婪匹配第一个完整 { ... } 块
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    return None
