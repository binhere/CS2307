"""Endpoints that let the settings page list providers and verify a user's own API key."""

from __future__ import annotations

from fastapi import APIRouter, Request
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from loguru import logger

from src.api.base.http_exception import credential_error_response
from src.services.llm.credentials import (
    LlmCredentialError,
    credential_error_from_exception,
    credentials_from_headers,
    provider_catalog,
)

router = APIRouter()

# One tool call is the smallest request that proves the key works AND that the model
# supports the tool calling the chat agent depends on.
CHECK_TIMEOUT_SECONDS = 30
CHECK_PROMPT = "Gọi tool ping với message là \"ok\". Không trả lời gì khác."


@tool
def ping(message: str) -> str:
    """Connectivity check tool; the model is asked to call it once with message="ok"."""
    return message


@router.get("/providers", summary="List the LLM providers a user can bring a key for")
async def list_providers():
    """Return the provider catalog shown on the API key settings page.

    Returns:
        dict: API envelope whose data is {"providers": [...], "allow_custom_base_url": bool}.
    """
    return {"data": provider_catalog(), "errors": [], "status": "success"}


@router.post("/check", summary="Verify the API key sent in the X-LLM-* headers")
async def check_credentials(request: Request):
    """Make one small tool-calling request with the caller's key.

    Args:
        request: Carries X-LLM-Provider, X-LLM-Api-Key and optional X-LLM-Model /
            X-LLM-Base-Url. The key is used for this call only and never logged.

    Returns:
        dict | JSONResponse: 200 {"provider", "model", "supports_tools": True} when the
        model answered with a tool call; 400 with an LLM_* error code otherwise.
    """
    try:
        credentials = credentials_from_headers(request.headers)
        if credentials is None:
            raise LlmCredentialError("LLM_KEY_REQUIRED", "Chưa nhập API key.")
        model = ChatOpenAI(
            model=credentials.model,
            base_url=credentials.base_url,
            api_key=credentials.api_key,
            temperature=0,
            timeout=CHECK_TIMEOUT_SECONDS,
            max_retries=0,
        ).bind_tools([ping])
        try:
            response = await model.ainvoke([HumanMessage(CHECK_PROMPT)])
        except Exception as error:  # noqa: BLE001 - translated into a user-facing code below
            translated = credential_error_from_exception(error)
            if translated is not None:
                raise translated from error
            logger.warning("LLM key check failed for provider {}: {}", credentials.provider, type(error).__name__)
            raise LlmCredentialError(
                "LLM_PROVIDER_ERROR", "Nhà cung cấp từ chối yêu cầu. Vui lòng kiểm tra lại model và key."
            ) from error
        if not getattr(response, "tool_calls", None):
            raise LlmCredentialError(
                "LLM_MODEL_NO_TOOLS",
                "Key hợp lệ nhưng model này không gọi được tool, nên không thể dùng cho Tư vấn AI. Hãy chọn model khác.",
            )
    except LlmCredentialError as error:
        return credential_error_response(error)
    return {
        "data": {"provider": credentials.provider, "model": credentials.model, "supports_tools": True},
        "errors": [],
        "status": "success",
    }
