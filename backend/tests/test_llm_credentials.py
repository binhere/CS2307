"""Bring-your-own-key: header parsing, request binding, endpoint errors and key secrecy."""

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import openai

from src.services.llm import credentials as credential_module
from src.services.llm.credentials import (
    LlmCredentialError,
    active_credentials,
    active_llm_client,
    credentials_from_headers,
    use_credentials,
)

USER_KEY = "user-secret-key-123"


def headers(**values):
    """Build a header mapping the way the browser sends it (X-LLM-* names)."""
    names = {"provider": "x-llm-provider", "api_key": "x-llm-api-key", "model": "x-llm-model", "base_url": "x-llm-base-url"}
    return {names[key]: value for key, value in values.items()}


def provider_error(error_class, status_code):
    """Create an openai SDK error as the provider would raise it."""
    response = httpx.Response(status_code, request=httpx.Request("POST", "https://provider.test/v1/chat/completions"))
    return error_class("provider said no", response=response, body=None)


class CredentialHeaderTests(unittest.TestCase):
    def test_no_key_means_no_credentials(self):
        self.assertIsNone(credentials_from_headers(headers(provider="gemini")))

    def test_known_provider_uses_its_endpoint_and_default_model(self):
        credentials = credentials_from_headers(headers(provider="gemini", api_key=USER_KEY))
        self.assertEqual(credentials.base_url, "https://generativelanguage.googleapis.com/v1beta/openai/")
        self.assertEqual(credentials.model, "gemini-2.5-flash")
        self.assertEqual(credentials.api_key, USER_KEY)

    def test_user_model_overrides_the_default(self):
        credentials = credentials_from_headers(headers(provider="openai", api_key=USER_KEY, model="gpt-4.1-mini"))
        self.assertEqual(credentials.model, "gpt-4.1-mini")

    def test_unknown_provider_is_rejected(self):
        with self.assertRaises(LlmCredentialError) as raised:
            credentials_from_headers(headers(provider="evil", api_key=USER_KEY))
        self.assertEqual(raised.exception.code, "LLM_PROVIDER_INVALID")

    def test_custom_base_url_is_refused_unless_enabled(self):
        custom = headers(provider="custom", api_key=USER_KEY, model="m", base_url="http://10.0.0.1:8080/v1")
        with patch.dict(os.environ, {"ALLOW_CUSTOM_LLM_BASE_URL": "false"}):
            with self.assertRaises(LlmCredentialError) as raised:
                credentials_from_headers(custom)
        self.assertEqual(raised.exception.code, "LLM_PROVIDER_INVALID")
        with patch.dict(os.environ, {"ALLOW_CUSTOM_LLM_BASE_URL": "true"}):
            self.assertEqual(credentials_from_headers(custom).base_url, "http://10.0.0.1:8080/v1")

    def test_key_with_whitespace_is_invalid(self):
        with self.assertRaises(LlmCredentialError) as raised:
            credentials_from_headers(headers(provider="openai", api_key="sk-abc def"))
        self.assertEqual(raised.exception.code, "LLM_KEY_INVALID")

    def test_repr_never_contains_the_key(self):
        credentials = credentials_from_headers(headers(provider="openai", api_key=USER_KEY))
        self.assertNotIn(USER_KEY, repr(credentials))
        self.assertNotIn(USER_KEY, str(credentials))


class RequestBindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_credentials_are_bound_only_inside_the_request(self):
        server_client = object()
        credentials = credentials_from_headers(headers(provider="groq", api_key=USER_KEY))
        self.assertIsNone(active_credentials())
        async with use_credentials(credentials):
            self.assertEqual(active_credentials(), credentials)
            request_client = active_llm_client(server_client)
            self.assertIsNot(request_client, server_client)
            self.assertEqual(request_client.base_url, "https://api.groq.com/openai/v1")
            self.assertEqual(request_client.api_keys, [USER_KEY])
        self.assertIsNone(active_credentials())
        self.assertIs(active_llm_client(server_client), server_client)

    async def test_chat_agent_model_uses_the_bound_key(self):
        from src.services.agent.chat_graph import chat_agent_service

        credentials = credentials_from_headers(headers(provider="openai", api_key=USER_KEY, model="gpt-4o-mini"))
        async with use_credentials(credentials):
            model = chat_agent_service._model(0.0)
        self.assertEqual(model.model_name, "gpt-4o-mini")
        self.assertEqual(model.openai_api_base, "https://api.openai.com/v1")
        self.assertEqual(model.openai_api_key.get_secret_value(), USER_KEY)

    async def test_concurrent_requests_keep_their_own_keys(self):
        first = credentials_from_headers(headers(provider="openai", api_key="key-one"))
        second = credentials_from_headers(headers(provider="openai", api_key="key-two"))

        async def observed_key(credentials):
            async with use_credentials(credentials):
                await asyncio.sleep(0.01)
                return active_credentials().api_key

        self.assertEqual(await asyncio.gather(observed_key(first), observed_key(second)), ["key-one", "key-two"])

    def test_provider_errors_map_to_user_codes(self):
        mapping = {
            (openai.AuthenticationError, 401): "LLM_KEY_INVALID",
            (openai.PermissionDeniedError, 403): "LLM_KEY_INVALID",
            (openai.RateLimitError, 429): "LLM_PROVIDER_ERROR",
            (openai.NotFoundError, 404): "LLM_PROVIDER_ERROR",
        }
        for (error_class, status_code), code in mapping.items():
            translated = credential_module.credential_error_from_exception(provider_error(error_class, status_code))
            self.assertEqual(translated.code, code)
        self.assertIsNone(credential_module.credential_error_from_exception(ValueError("other")))

    def test_gemini_bad_key_400_is_an_invalid_key(self):
        response = httpx.Response(400, request=httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"))
        gemini_error = openai.BadRequestError("Error code: 400 - Please pass a valid API key", response=response, body=None)
        other_bad_request = openai.BadRequestError("Error code: 400 - max_tokens too large", response=response, body=None)
        self.assertEqual(credential_module.credential_error_from_exception(gemini_error).code, "LLM_KEY_INVALID")
        self.assertIsNone(credential_module.credential_error_from_exception(other_bad_request))


class EndpointTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, method, path, **kwargs):
        from src.main import app

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    async def test_chat_without_any_key_is_refused_before_touching_the_database(self):
        with patch.object(credential_module, "server_credentials", return_value=None), \
                patch("src.api.v1.endpoints.chat.view_chat.repository") as repository:
            response = await self.request("POST", "/v1/chat", json={"message": "căn hộ quận 7"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["errors"][0]["msg"], "LLM_KEY_REQUIRED")
        repository.create_conversation.assert_not_called()
        repository.add_message.assert_not_called()

    async def test_chat_reports_a_rejected_key(self):
        rejected = credential_module.credential_error_from_exception(provider_error(openai.AuthenticationError, 401))
        with patch("src.api.v1.endpoints.chat.view_chat.chat_agent_service.run", AsyncMock(side_effect=rejected)):
            response = await self.request("POST", "/v1/chat", json={"message": "căn hộ quận 7"},
                                          headers=headers(provider="openai", api_key=USER_KEY))
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["errors"][0]["msg"], "LLM_KEY_INVALID")
        self.assertNotIn(USER_KEY, response.text)

    async def test_providers_endpoint_lists_known_providers(self):
        response = await self.request("GET", "/v1/llm/providers")
        provider_ids = [provider["id"] for provider in response.json()["data"]["providers"]]
        self.assertEqual(provider_ids[:5], ["gemini", "openai", "groq", "openrouter", "xai"])
        self.assertNotIn("base_url", response.json()["data"]["providers"][0])

    async def test_check_accepts_a_key_whose_model_calls_the_tool(self):
        reply = SimpleNamespace(tool_calls=[{"name": "ping", "args": {"message": "ok"}, "id": "1"}])
        with patch("langchain_core.runnables.base.RunnableBinding.ainvoke", AsyncMock(return_value=reply)):
            response = await self.request("POST", "/v1/llm/check", headers=headers(provider="openai", api_key=USER_KEY))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["data"]["supports_tools"])

    async def test_check_rejects_a_model_without_tool_calls(self):
        with patch("langchain_core.runnables.base.RunnableBinding.ainvoke", AsyncMock(return_value=SimpleNamespace(tool_calls=[]))):
            response = await self.request("POST", "/v1/llm/check", headers=headers(provider="openai", api_key=USER_KEY))
        self.assertEqual(response.json()["errors"][0]["msg"], "LLM_MODEL_NO_TOOLS")

    async def test_check_maps_an_invalid_key(self):
        error = provider_error(openai.AuthenticationError, 401)
        with patch("langchain_core.runnables.base.RunnableBinding.ainvoke", AsyncMock(side_effect=error)):
            response = await self.request("POST", "/v1/llm/check", headers=headers(provider="openai", api_key=USER_KEY))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["errors"][0]["msg"], "LLM_KEY_INVALID")
        self.assertNotIn(USER_KEY, response.text)


if __name__ == "__main__":
    unittest.main()
