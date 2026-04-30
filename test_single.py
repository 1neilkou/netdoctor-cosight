"""Minimal single-task Co-Sight smoke test.

This script intentionally reads LLM settings from process environment variables
only. It does not hardcode API keys and does not modify Co-Sight core files.
"""

from __future__ import annotations

import os
import sys
import traceback
import types
from pathlib import Path
from pprint import pprint

import httpx
from openai import OpenAI

from app.cosight.llm.chat_llm import ChatLLM


# Create tiny in-memory MCP modules before importing CoSight.py. The local
# environment used for this smoke test does not have the optional "mcp" package,
# and this specific test question does not require MCP tools.
mcp_stub = types.ModuleType("mcp")


class _DummyMCPTool:
    # Provide the attributes that Co-Sight reads when converting MCP tools.
    name = "dummy_mcp_tool"
    description = "Dummy MCP tool used only for import-time compatibility."
    inputSchema = {"type": "object", "properties": {}, "required": []}


class _DummyClientSession:
    # Accept arbitrary constructor args to satisfy MCPServer import-time typing.
    def __init__(self, *args, **kwargs):
        pass


class _DummyStdioServerParameters:
    # Accept arbitrary constructor args to satisfy MCPServerStdio.
    def __init__(self, *args, **kwargs):
        pass


def _dummy_stdio_client(*args, **kwargs):
    # Fail clearly if an MCP stdio tool is actually invoked in this smoke test.
    raise RuntimeError("MCP stdio client is not available in test_single.py")


mcp_stub.Tool = _DummyMCPTool
mcp_stub.ClientSession = _DummyClientSession
mcp_stub.StdioServerParameters = _DummyStdioServerParameters
mcp_stub.stdio_client = _dummy_stdio_client
sys.modules.setdefault("mcp", mcp_stub)

mcp_client_stub = types.ModuleType("mcp.client")
mcp_client_sse_stub = types.ModuleType("mcp.client.sse")


def _dummy_sse_client(*args, **kwargs):
    # Fail clearly if an MCP SSE tool is actually invoked in this smoke test.
    raise RuntimeError("MCP SSE client is not available in test_single.py")


mcp_client_sse_stub.sse_client = _dummy_sse_client
sys.modules.setdefault("mcp.client", mcp_client_stub)
sys.modules.setdefault("mcp.client.sse", mcp_client_sse_stub)

mcp_types_stub = types.ModuleType("mcp.types")
mcp_types_stub.CallToolResult = object
mcp_types_stub.JSONRPCMessage = object
sys.modules.setdefault("mcp.types", mcp_types_stub)


# Create a tiny lagent.schema stub for optional deep_search imports. The smoke
# test does not call deep_search, but TaskActorAgent imports its module.
lagent_stub = types.ModuleType("lagent")
lagent_schema_stub = types.ModuleType("lagent.schema")
lagent_actions_stub = types.ModuleType("lagent.actions")


class _DummyModelStatusCode:
    # Provide status constants referenced by the deep_search LLM client.
    SERVER_ERR = "SERVER_ERR"
    STREAM_ING = "STREAM_ING"
    SESSION_INVALID_ARG = "SESSION_INVALID_ARG"
    END = "END"


class _DummyActionExecutor:
    # Accept tool lists during import-time setup; not used by this smoke test.
    def __init__(self, *args, **kwargs):
        pass


def _dummy_tool_api(func=None, *args, **kwargs):
    # Behave as a no-op decorator for optional deep_search action imports.
    if func is None:
        return lambda wrapped: wrapped
    return func


lagent_stub.tool_api = _dummy_tool_api
lagent_schema_stub.ModelStatusCode = _DummyModelStatusCode
lagent_actions_stub.ActionExecutor = _DummyActionExecutor
sys.modules.setdefault("lagent", lagent_stub)
sys.modules.setdefault("lagent.schema", lagent_schema_stub)
sys.modules.setdefault("lagent.actions", lagent_actions_stub)


# Stub optional deep_search modules imported by TaskActorAgent. The actor's
# deep_search function is commented out in the current all_functions registry,
# so this smoke test does not need the real deep_search dependency chain.
deep_search_stub = types.ModuleType("app.cosight.tool.deep_search.deep_search")


class _DummyDeepSearchToolkit:
    # Accept constructor arguments so TaskActorAgent can instantiate it.
    def __init__(self, *args, **kwargs):
        pass

    # Fail clearly if a commented-out deep_search function is ever enabled.
    def deep_search(self, *args, **kwargs):
        raise RuntimeError("DeepSearchToolkit is stubbed in test_single.py")


deep_search_stub.DeepSearchToolkit = _DummyDeepSearchToolkit
sys.modules.setdefault("app.cosight.tool.deep_search.deep_search", deep_search_stub)

tavily_searcher_stub = types.ModuleType("app.cosight.tool.deep_search.searchers.tavily_search")


class _DummyTavilySearch:
    # Accept constructor arguments so TaskActorAgent can instantiate it.
    def __init__(self, *args, **kwargs):
        pass

    # Fail clearly if the optional image_search code path is ever enabled.
    def search(self, *args, **kwargs):
        raise RuntimeError("TavilySearch is stubbed in test_single.py")


tavily_searcher_stub.TavilySearch = _DummyTavilySearch
sys.modules.setdefault("app.cosight.tool.deep_search.searchers.tavily_search", tavily_searcher_stub)

web_util_stub = types.ModuleType("app.cosight.tool.web_util")


class _DummyWebToolkit:
    # Accept constructor arguments so TaskActorAgent can instantiate it.
    def __init__(self, *args, **kwargs):
        pass

    # Fail clearly if the commented-out browser_use path is ever enabled.
    def browser_use(self, *args, **kwargs):
        raise RuntimeError("WebToolkit is stubbed in test_single.py")


web_util_stub.WebToolkit = _DummyWebToolkit
sys.modules.setdefault("app.cosight.tool.web_util", web_util_stub)

image_tool_stub = types.ModuleType("app.cosight.tool.image_analysis_toolkit")
audio_tool_stub = types.ModuleType("app.cosight.tool.audio_toolkit")
video_tool_stub = types.ModuleType("app.cosight.tool.video_analysis_toolkit")


class _DummyVisionTool:
    # Accept model config so TaskActorAgent can instantiate it.
    def __init__(self, *args, **kwargs):
        pass

    # Fail clearly if image QA is actually invoked.
    def ask_question_about_image(self, *args, **kwargs):
        raise RuntimeError("VisionTool is stubbed in test_single.py")


class _DummyAudioTool:
    # Accept model config so TaskActorAgent can instantiate it.
    def __init__(self, *args, **kwargs):
        pass

    # Fail clearly if audio recognition is actually invoked.
    def speech_to_text(self, *args, **kwargs):
        raise RuntimeError("AudioTool is stubbed in test_single.py")


class _DummyVideoTool:
    # Accept model config so TaskActorAgent can instantiate it.
    def __init__(self, *args, **kwargs):
        pass

    # Fail clearly if video QA is actually invoked.
    def ask_question_about_video(self, *args, **kwargs):
        raise RuntimeError("VideoTool is stubbed in test_single.py")


image_tool_stub.VisionTool = _DummyVisionTool
audio_tool_stub.AudioTool = _DummyAudioTool
video_tool_stub.VideoTool = _DummyVideoTool
sys.modules.setdefault("app.cosight.tool.image_analysis_toolkit", image_tool_stub)
sys.modules.setdefault("app.cosight.tool.audio_toolkit", audio_tool_stub)
sys.modules.setdefault("app.cosight.tool.video_analysis_toolkit", video_tool_stub)

doc_tool_stub = types.ModuleType("app.cosight.tool.document_processing_toolkit")


class _DummyDocumentProcessingToolkit:
    # Accept constructor arguments so TaskActorAgent can instantiate it.
    def __init__(self, *args, **kwargs):
        pass

    # Fail clearly if document extraction is actually invoked.
    def extract_document_content(self, *args, **kwargs):
        raise RuntimeError("DocumentProcessingToolkit is stubbed in test_single.py")


doc_tool_stub.DocumentProcessingToolkit = _DummyDocumentProcessingToolkit
sys.modules.setdefault("app.cosight.tool.document_processing_toolkit", doc_tool_stub)

search_util_stub = types.ModuleType("app.cosight.tool.search_util")


def _dummy_search_baidu(*args, **kwargs):
    # Fail clearly if the commented-out Baidu search path is ever enabled.
    raise RuntimeError("search_baidu is stubbed in test_single.py")


search_util_stub.search_baidu = _dummy_search_baidu
sys.modules.setdefault("app.cosight.tool.search_util", search_util_stub)

scrape_tool_stub = types.ModuleType("app.cosight.tool.scrape_website_toolkit")


def _dummy_fetch_website_content(*args, **kwargs):
    # Fail clearly if webpage fetch is actually invoked.
    raise RuntimeError("fetch_website_content is stubbed in test_single.py")


scrape_tool_stub.fetch_website_content = _dummy_fetch_website_content
scrape_tool_stub.fetch_website_content_with_images = _dummy_fetch_website_content
scrape_tool_stub.fetch_website_images_only = _dummy_fetch_website_content
sys.modules.setdefault("app.cosight.tool.scrape_website_toolkit", scrape_tool_stub)

html_tool_stub = types.ModuleType("app.cosight.tool.html_visualization_toolkit")


class _DummyHtmlVisualizationToolkit:
    # Accept constructor arguments so TaskActorAgent can instantiate it.
    def __init__(self, *args, **kwargs):
        pass

    # Fail clearly if HTML report generation is actually invoked.
    def create_html_report(self, *args, **kwargs):
        raise RuntimeError("HtmlVisualizationToolkit is stubbed in test_single.py")


html_tool_stub.HtmlVisualizationToolkit = _DummyHtmlVisualizationToolkit
sys.modules.setdefault("app.cosight.tool.html_visualization_toolkit", html_tool_stub)


# Create a tiny in-memory module named "llm" before importing CoSight.py.
# CoSight.py imports llm_for_plan/act/tool/vision at module import time, but
# this script constructs those objects explicitly from environment variables.
llm_stub = types.ModuleType("llm")
llm_stub.llm_for_plan = None
llm_stub.llm_for_act = None
llm_stub.llm_for_tool = None
llm_stub.llm_for_vision = None
sys.modules.setdefault("llm", llm_stub)

from CoSight import CoSight  # noqa: E402


if hasattr(sys.stdout, "reconfigure"):
    # Force UTF-8 console output so Chinese text and status symbols print on Windows.
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    # Force UTF-8 error output so tracebacks remain readable on Windows.
    sys.stderr.reconfigure(encoding="utf-8")


def _env(name: str, default: str | None = None) -> str | None:
    # Read one environment variable without touching any .env file.
    value = os.environ.get(name, default)
    # Normalize empty strings to None so fallback logic is predictable.
    return value if value and value.strip() else None


def build_llm(prefix: str = "") -> ChatLLM:
    # Resolve prefixed variable names such as PLAN_API_KEY.
    prefix_name = f"{prefix}_" if prefix else ""
    # Read the role-specific API key, falling back to the base API_KEY.
    api_key = _env(f"{prefix_name}API_KEY") or _env("API_KEY")
    # Read the role-specific base URL, falling back to the base API_BASE_URL.
    base_url = _env(f"{prefix_name}API_BASE_URL") or _env("API_BASE_URL")
    # Read the role-specific model name, falling back to the base MODEL_NAME.
    model = _env(f"{prefix_name}MODEL_NAME") or _env("MODEL_NAME")
    # Read optional role-specific max token setting.
    max_tokens_raw = _env(f"{prefix_name}MAX_TOKENS") or _env("MAX_TOKENS")
    # Read optional role-specific temperature setting.
    temperature_raw = _env(f"{prefix_name}TEMPERATURE") or _env("TEMPERATURE")
    # Read optional role-specific proxy setting.
    proxy = _env(f"{prefix_name}PROXY") or _env("PROXY")
    # Fail early with variable names only; never print secret values.
    missing = [
        name
        for name, value in {
            "API_KEY": api_key,
            "API_BASE_URL": base_url,
            "MODEL_NAME": model,
        }.items()
        if not value
    ]
    # Stop if required LLM settings are absent.
    if missing:
        raise RuntimeError(f"Missing required LLM environment variables: {', '.join(missing)}")
    # Convert max token setting when present.
    max_tokens = int(max_tokens_raw) if max_tokens_raw else 8192
    # Convert temperature setting when present.
    temperature = float(temperature_raw) if temperature_raw else 0.0
    # Build HTTP client options for the OpenAI-compatible client.
    http_client_kwargs = {
        "headers": {"Content-Type": "application/json", "Authorization": api_key},
        "verify": False,
        "trust_env": False,
        "timeout": httpx.Timeout(connect=30.0, read=float(_env("LLM_TIMEOUT", "180")), write=30.0, pool=10.0),
    }
    # Add proxy only if configured.
    if proxy:
        http_client_kwargs["proxy"] = proxy
    # Construct an OpenAI-compatible client.
    client = OpenAI(
        base_url=base_url,
        api_key=api_key,
        http_client=httpx.Client(**http_client_kwargs),
    )
    # Wrap the OpenAI-compatible client in Co-Sight's ChatLLM abstraction.
    return ChatLLM(
        base_url=base_url,
        api_key=api_key,
        model=model,
        client=client,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def main() -> None:
    # Define the test question requested by the user.
    question = "法国的首都是哪里？请用中文回答，并说明该城市的人口大约是多少。"
    # Create a dedicated workspace so Co-Sight file tools do not write to repo root.
    workspace_path = Path("outputs/test_single_workspace").resolve()
    # Ensure the dedicated workspace exists before CoSight starts.
    workspace_path.mkdir(parents=True, exist_ok=True)
    # Set WORKSPACE_PATH for Co-Sight internals that read it indirectly.
    os.environ["WORKSPACE_PATH"] = str(workspace_path)
    # Create the planner LLM from PLAN_* variables or base variables.
    plan_llm = build_llm("PLAN")
    # Create the actor LLM from ACT_* variables or base variables.
    act_llm = build_llm("ACT")
    # Create the tool LLM from TOOL_* variables or base variables.
    tool_llm = build_llm("TOOL")
    # Create the vision LLM from VISION_* variables or base variables.
    vision_llm = build_llm("VISION")
    # Initialize CoSight using the real constructor signature.
    cosight = CoSight(
        plan_llm,
        act_llm,
        tool_llm,
        vision_llm,
        work_space_path=str(workspace_path),
        message_uuid="test_single_plan",
    )
    # Execute the single test question.
    cosight.execute(question)
    # Print the final answer saved in Plan.result.
    print("\n=== a) Plan.result ===")
    print(cosight.plan.get_plan_result())
    # Print the plan step descriptions.
    print("\n=== b) Plan.steps ===")
    pprint(cosight.plan.steps)
    # Print the status of each step.
    print("\n=== c) Plan.step_statuses ===")
    pprint(cosight.plan.step_statuses)
    # Print raw tool call records.
    print("\n=== d) Plan.step_tool_calls ===")
    pprint(cosight.plan.step_tool_calls)


if __name__ == "__main__":
    # Always show the full traceback if the smoke test fails.
    try:
        main()
    except Exception:
        traceback.print_exc()
