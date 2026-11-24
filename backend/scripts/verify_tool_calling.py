"""Verify that the configured OpenAI-compatible endpoint emits native tool calls.

Run inside the backend image after configuring BACKEND_LLM_*:
    python scripts/verify_tool_calling.py
"""

from __future__ import annotations

import asyncio

from langchain.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from src.services.llm.llm_model import LLModel
from src.settings.config import config_value


@tool
def echo_tool(value: str) -> str:
    """Echo the provided value exactly."""
    return value


async def main() -> None:
    # Resolve the endpoint exactly as the chat agent does (provider defaults,
    # Gemini's OpenAI-compatible URL), so the smoke test checks the same server.
    base_url, model_name, api_key = LLModel().chat_completions_endpoint()
    model = ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=0,
        timeout=int(config_value("CHAT_MODEL_TIMEOUT_SECONDS", "60")),
        max_retries=0,
    )
    first = await model.bind_tools([echo_tool], parallel_tool_calls=False).ainvoke(
        [
            SystemMessage("Gọi đúng một tool echo_tool với value='smoke-test'. Không trả lời bằng văn bản."),
            HumanMessage("Hãy kiểm tra tool calling."),
        ]
    )
    calls = first.tool_calls
    if len(calls) != 1 or calls[0].get("name") != "echo_tool":
        raise RuntimeError(f"Endpoint did not return the expected native tool call: {calls!r}")
    call = calls[0]
    tool_result = await echo_tool.ainvoke(call.get("args", {}))
    final = await model.ainvoke(
        [
            SystemMessage("Xác nhận ngắn gọn bằng tiếng Việt rằng tool đã hoạt động."),
            HumanMessage("Hãy kiểm tra tool calling."),
            first,
            ToolMessage(content=tool_result, tool_call_id=call["id"]),
        ]
    )
    if not str(final.content or "").strip():
        raise RuntimeError("Endpoint returned an empty final answer after ToolMessage")
    print("Tool calling smoke test passed")


if __name__ == "__main__":
    asyncio.run(main())
