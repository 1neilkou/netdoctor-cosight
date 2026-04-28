"""
llm_answerer.py — LLM 答案生成模块

公开接口：
    generate_llm_answer(
        question_item, parsed_result, routed_tools, tool_results=None
    ) -> dict

输出结构：
    final_answer   : str   — 生成的最终答案（失败时为空串）
    answer_source  : str   — "llm" 或 "fallback"
    confidence     : float — 答案置信度（失败时为 0.0）
    answer_reason  : str   — 作答依据说明
    debug          : dict  — 诊断信息
        api_key_loaded      : bool
        http_status         : int | None
        json_parse_success  : bool
        fallback_reason     : str | None

约束：
    - LLM 输入不包含 expected_answer
    - API_KEY 缺失 / 请求失败 / 非 200 / JSON 解析失败时，均 fallback，不报错
    - 环境变量同 semantic_parser：API_KEY / API_BASE_URL / MODEL_NAME / MAX_TOKENS / TEMPERATURE
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

# .env 加载（容错，dotenv 不可用时跳过）
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env", override=False)
except ImportError:
    pass

# ---------------------------------------------------------------------------
# 内部异常：携带诊断信息，供上层捕获转为 fallback
# ---------------------------------------------------------------------------

class _AnswerError(Exception):
    def __init__(self, fallback_reason: str, debug: dict):
        super().__init__(fallback_reason)
        self.fallback_reason = fallback_reason
        self.debug = debug

# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def generate_llm_answer(
    question_item: dict,
    parsed_result: dict,
    routed_tools: list,
    tool_results: dict | None = None,
) -> dict:
    """
    调用 DeepSeek LLM 为单道题目生成答案。

    Args:
        question_item:  原始题目记录（只读取 question / level / question_type，不读取 expected_answer）
        parsed_result:  parse_question() 的输出，包含 rule_parse / semantic_parse / merged
        routed_tools:   tool_router 选出的工具列表
        tool_results:   已执行工具的结果（如 rule_evaluator 的输出）

    Returns:
        包含 final_answer / answer_source / confidence / answer_reason / debug 的字典。
    """
    api_key = os.environ.get("API_KEY", "").strip()

    base_debug: dict = {
        "api_key_loaded":     bool(api_key),
        "http_status":        None,
        "json_parse_success": False,
        "fallback_reason":    None,
    }

    if not api_key:
        base_debug["fallback_reason"] = "missing_api_key"
        return _make_fallback(base_debug)

    base_url  = os.environ.get("API_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    model     = os.environ.get("MODEL_NAME",   "deepseek-chat")
    max_tok   = int(os.environ.get("MAX_TOKENS",   "1024"))
    temp      = float(os.environ.get("TEMPERATURE", "0.3"))

    try:
        return _call_llm(
            question_item, parsed_result, routed_tools, tool_results,
            api_key, base_url, model, max_tok, temp, base_debug,
        )
    except _AnswerError as e:
        return _make_fallback(e.debug)
    except Exception as e:
        base_debug["fallback_reason"] = f"unexpected_error:{type(e).__name__}"
        return _make_fallback(base_debug)


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------

def _make_fallback(debug: dict) -> dict:
    """构造 fallback 返回结果。"""
    return {
        "final_answer":  "",
        "answer_source": "fallback",
        "confidence":    0.0,
        "answer_reason": "",
        "debug":         debug,
    }


def _build_prompt(
    question_item: dict,
    parsed_result: dict,
    routed_tools: list,
    tool_results: dict | None,
) -> str:
    """从解析结果构造 LLM Prompt，严格不包含 expected_answer。"""
    qtype = question_item.get("question_type", "")
    level = question_item.get("level", 1)
    text  = question_item.get("question", "")

    sem   = parsed_result.get("semantic_parse", {})
    mrg   = parsed_result.get("merged", {})
    rp    = parsed_result.get("rule_parse", {})

    goal         = sem.get("question_goal", "")
    ans_fmt      = sem.get("answer_format", "")
    capabilities = sem.get("required_capabilities", [])
    impl_constr  = mrg.get("constraints", [])
    thresholds   = rp.get("threshold_values", [])

    # 拼装各部分
    capabilities_str = "、".join(capabilities) if capabilities else "（无）"
    constr_str       = "；".join(impl_constr[:5]) if impl_constr else "（无）"
    thresholds_str   = "  ".join(thresholds[:6]) if thresholds else "（无）"
    tools_str        = "、".join(routed_tools) if routed_tools else "（无）"

    # 规则评估结果（如有）
    rule_section = ""
    if tool_results and "rule_result" in tool_results:
        rr = tool_results["rule_result"]
        findings = rr.get("rule_findings", [])
        triggered = rr.get("triggered_rules", [])
        if findings or triggered:
            rule_section = (
                "\n【规则评估结果】\n"
                + (f"  识别规则：{chr(10).join(f'  - {f}' for f in findings[:4])}\n" if findings else "")
                + (f"  触发规则：{', '.join(triggered)}\n" if triggered else "")
            )

    prompt = (
        "你是一个5G网络运维题目解题专家。请根据以下题目信息和分析结果生成答案。"
        "仅输出 JSON，不要任何解释文字。\n\n"
        f"【题目信息】\n"
        f"题目类型：{qtype}\n"
        f"难度级别：L{level}\n"
        f"题目原文：\n{text}\n\n"
        f"【语义分析】\n"
        f"解题目标：{goal}\n"
        f"期望答案格式：{ans_fmt}\n"
        f"所需能力：{capabilities_str}\n"
        f"隐含约束：{constr_str}\n"
        f"已识别阈值/数值：{thresholds_str}\n"
        f"选用工具：{tools_str}\n"
        f"{rule_section}\n"
        "请输出如下 JSON（不要添加其他字段）：\n"
        "{\n"
        '  "final_answer": "完整的答案内容",\n'
        '  "confidence": 0.85,\n'
        '  "answer_reason": "作答依据说明（20字以内）"\n'
        "}"
    )
    return prompt


def _call_llm(
    question_item: dict,
    parsed_result: dict,
    routed_tools: list,
    tool_results: dict | None,
    api_key: str,
    base_url: str,
    model: str,
    max_tokens: int,
    temperature: float,
    debug: dict,
) -> dict:
    """构造 prompt → HTTP POST → 解析 JSON → 返回完整结果。"""
    import urllib.error
    import urllib.request

    prompt = _build_prompt(question_item, parsed_result, routed_tools, tool_results)

    payload = json.dumps({
        "model":       model,
        "messages":    [{"role": "user", "content": prompt}],
        "max_tokens":  max_tokens,
        "temperature": temperature,
    }).encode("utf-8")

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
        with urllib.request.urlopen(req, timeout=30) as resp:
            status   = resp.getcode()
            raw_body = resp.read()
    except urllib.error.HTTPError as e:
        status   = e.code
        raw_body = e.read() or b""
        debug["http_status"]     = status
        debug["fallback_reason"] = f"http_error_{status}"
        raise _AnswerError(f"http_error_{status}", debug) from e
    except Exception as e:
        debug["fallback_reason"] = f"api_request_failed:{type(e).__name__}"
        raise _AnswerError(debug["fallback_reason"], debug) from e

    debug["http_status"] = status
    raw_str = raw_body.decode("utf-8", errors="replace")

    if status != 200:
        debug["fallback_reason"] = f"http_error_{status}"
        raise _AnswerError(debug["fallback_reason"], debug)

    if not raw_str.strip():
        debug["fallback_reason"] = "empty_response"
        raise _AnswerError("empty_response", debug)

    # ---- 解析响应体 -------------------------------------------------------
    try:
        body    = json.loads(raw_str)
        content = body["choices"][0]["message"]["content"].strip()
    except Exception as e:
        debug["fallback_reason"] = "invalid_json_response"
        raise _AnswerError("invalid_json_response", debug) from e

    if not content:
        debug["fallback_reason"] = "empty_response"
        raise _AnswerError("empty_response", debug)

    # ---- 从 content 提取 JSON 对象 ----------------------------------------
    parsed = _extract_json(content)
    if parsed is None:
        debug["fallback_reason"] = "invalid_json_response"
        raise _AnswerError("invalid_json_response", debug)

    debug["json_parse_success"] = True

    # ---- 字段提取与修正 ---------------------------------------------------
    final_answer  = str(parsed.get("final_answer", "")).strip()
    answer_reason = str(parsed.get("answer_reason", "")).strip()
    try:
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.8))))
    except (TypeError, ValueError):
        confidence = 0.8

    if not final_answer:
        debug["fallback_reason"] = "empty_final_answer"
        raise _AnswerError("empty_final_answer", debug)

    return {
        "final_answer":  final_answer,
        "answer_source": "llm",
        "confidence":    confidence,
        "answer_reason": answer_reason,
        "debug":         debug,
    }


def _extract_json(text: str) -> dict | None:
    """
    从 LLM 返回文本中提取第一个 JSON 对象，支持三种形式：
      1. 纯 JSON
      2. ```json ... ``` 代码块
      3. 文本中第一个 { ... } 块（贪婪匹配）
    与 semantic_parser._extract_json 逻辑一致。
    """
    # 情况 1：整体是合法 JSON
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # 情况 2：```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    # 情况 3：文本中第一个完整 { ... } 块
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            obj = json.loads(m.group())
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    return None
