"""Per-request LLM credentials for bring-your-own-key deployments.

Each user supplies their own provider and API key from the browser (X-LLM-* headers).
The key is used only while the request runs: it is held in a ContextVar, never written
to the database, the LangGraph checkpoint or the logs. Embeddings are not affected —
they run on the server with one fixed model so query vectors match the catalog.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import AsyncIterator, Dict, Optional
from urllib.parse import urlparse

from src.settings.config import APPLICATION, config_value

HEADER_PROVIDER = "x-llm-provider"
HEADER_API_KEY = "x-llm-api-key"
HEADER_MODEL = "x-llm-model"
HEADER_BASE_URL = "x-llm-base-url"
CUSTOM_PROVIDER = "custom"

# Every listed endpoint speaks the OpenAI Chat Completions protocol with tool calling,
# which the chat agent needs. Gemini goes through Google's OpenAI-compatible endpoint.
PROVIDERS: Dict[str, Dict[str, str]] = {
    "gemini": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-2.5-flash",
        "key_url": "https://aistudio.google.com/apikey",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "key_url": "https://platform.openai.com/api-keys",
    },
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "key_url": "https://console.groq.com/keys",
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openai/gpt-4o-mini",
        "key_url": "https://openrouter.ai/keys",
    },
    "xai": {
        "label": "xAI Grok",
        "base_url": "https://api.x.ai/v1",
        "default_model": "grok-3-mini",
        "key_url": "https://console.x.ai",
    },
}

MAX_API_KEY_LENGTH = 512
MAX_MODEL_LENGTH = 128


class LlmCredentialError(Exception):
    """Credentials are missing, malformed or rejected; carries a user-facing message.

    Attributes:
        code: Stable machine-readable code (LLM_KEY_REQUIRED, LLM_KEY_INVALID,
            LLM_PROVIDER_INVALID, LLM_MODEL_NO_TOOLS, LLM_PROVIDER_ERROR).
        detail: Vietnamese message shown to the user; never contains the key.
    """

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class LlmCredentials:
    """Resolved endpoint and key for one request.

    Attributes:
        provider: Provider id from PROVIDERS, "custom", or "server" for the
            deployment's own configured key.
        base_url: OpenAI-compatible base URL.
        model: Model name sent to the provider.
        api_key: Secret key; excluded from repr so it never reaches logs.
    """

    provider: str
    base_url: str
    model: str
    api_key: str

    def __repr__(self) -> str:
        return f"LlmCredentials(provider={self.provider!r}, base_url={self.base_url!r}, model={self.model!r})"


_active_credentials: ContextVar[Optional[LlmCredentials]] = ContextVar("active_llm_credentials", default=None)
_active_client: ContextVar[Optional[object]] = ContextVar("active_llm_client", default=None)


def is_custom_base_url_allowed() -> bool:
    """Whether users may point requests at an arbitrary base URL.

    Off by default: a user-chosen URL makes the server send requests anywhere
    (server-side request forgery). Enable only for local development.

    Returns:
        bool: ALLOW_CUSTOM_LLM_BASE_URL parsed as a boolean.
    """
    return str(config_value("ALLOW_CUSTOM_LLM_BASE_URL", "false")).strip().lower() in {"1", "true", "yes"}


def provider_catalog() -> dict:
    """Describe the selectable providers for the settings page.

    Returns:
        dict: {"providers": [{id, label, default_model, key_url}], "allow_custom_base_url": bool}.
    """
    providers = [{"id": provider_id, **{key: value for key, value in info.items() if key != "base_url"}}
                 for provider_id, info in PROVIDERS.items()]
    allow_custom = is_custom_base_url_allowed()
    if allow_custom:
        providers.append({"id": CUSTOM_PROVIDER, "label": "Tùy chỉnh (tương thích OpenAI)",
                          "default_model": "", "key_url": ""})
    return {"providers": providers, "allow_custom_base_url": allow_custom}


def credentials_from_headers(headers) -> Optional[LlmCredentials]:
    """Read and validate the X-LLM-* headers of a request.

    Args:
        headers: Case-insensitive mapping of request headers (starlette Headers).

    Returns:
        LlmCredentials, or None when the request carries no API key.

    Raises:
        LlmCredentialError: LLM_PROVIDER_INVALID for an unknown provider or a
            disallowed/invalid custom base URL; LLM_KEY_INVALID for a malformed key.
    """
    api_key = (headers.get(HEADER_API_KEY) or "").strip()
    if not api_key:
        return None
    if len(api_key) > MAX_API_KEY_LENGTH or any(character.isspace() for character in api_key):
        raise LlmCredentialError("LLM_KEY_INVALID", "API key không hợp lệ.")
    provider = (headers.get(HEADER_PROVIDER) or "").strip().lower()
    model = (headers.get(HEADER_MODEL) or "").strip()[:MAX_MODEL_LENGTH]
    if provider == CUSTOM_PROVIDER:
        if not is_custom_base_url_allowed():
            raise LlmCredentialError("LLM_PROVIDER_INVALID", "Máy chủ này không cho phép dùng base URL tùy chỉnh.")
        base_url = (headers.get(HEADER_BASE_URL) or "").strip()
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise LlmCredentialError("LLM_PROVIDER_INVALID", "Base URL tùy chỉnh không hợp lệ.")
        if not model:
            raise LlmCredentialError("LLM_PROVIDER_INVALID", "Cần nhập tên model khi dùng base URL tùy chỉnh.")
        return LlmCredentials(CUSTOM_PROVIDER, base_url.rstrip("/"), model, api_key)
    info = PROVIDERS.get(provider)
    if info is None:
        raise LlmCredentialError("LLM_PROVIDER_INVALID", "Nhà cung cấp AI không được hỗ trợ.")
    return LlmCredentials(provider, info["base_url"], model or info["default_model"], api_key)


def server_credentials() -> Optional[LlmCredentials]:
    """The deployment's own LLM key, used only when a request brings none.

    A bring-your-own-key deployment leaves BACKEND_LLM_API_KEY / GEMINI_API_KEYS empty,
    so this returns None and requests without a key are refused.

    Returns:
        LlmCredentials with provider "server", or None when no key is configured.
    """
    from src.settings.event import llm_model

    if not llm_model.has_credentials():
        return None
    base_url, model, api_key = llm_model.chat_completions_endpoint()
    return LlmCredentials("server", base_url or PROVIDERS["openai"]["base_url"], model, api_key)


def resolve_credentials(headers) -> Optional[LlmCredentials]:
    """Pick the request's own credentials, falling back to the server's.

    Args:
        headers: Request headers.

    Returns:
        LlmCredentials, or None when neither the request nor the server has a key.

    Raises:
        LlmCredentialError: When the request's headers are present but invalid.
    """
    return credentials_from_headers(headers) or server_credentials()


def active_credentials() -> Optional[LlmCredentials]:
    """Credentials bound to the current request by use_credentials, if any."""
    return _active_credentials.get()


def active_llm_client(default):
    """The LLModel built for the current request's credentials.

    Args:
        default: Client to use outside use_credentials (the server-wide LLModel).

    Returns:
        LLModel bound to the request's key, or ``default``.
    """
    return _active_client.get() or default


@asynccontextmanager
async def use_credentials(credentials: Optional[LlmCredentials]) -> AsyncIterator[None]:
    """Bind credentials (and a client built from them) to the current request.

    Everything awaited inside the block — chat agent nodes, the hybrid search LLM
    answer, the LLM query parser — reads them through active_credentials() /
    active_llm_client(). The client's HTTP connections are closed on exit.

    Args:
        credentials: Credentials to bind; None leaves the server defaults in place.
    """
    if credentials is None:
        yield
        return
    from src.services.llm.llm_model import LLModel

    client = LLModel(provider="openai-compatible", base_url=credentials.base_url,
                     model_name=credentials.model, api_key=credentials.api_key, max_output_tokens=8192)
    client.start()
    credentials_token = _active_credentials.set(credentials)
    client_token = _active_client.set(client)
    try:
        yield
    finally:
        _active_client.reset(client_token)
        _active_credentials.reset(credentials_token)
        await client.stop()


def credential_error_from_exception(error: Exception) -> Optional[LlmCredentialError]:
    """Translate a provider SDK error into a user-facing credential error.

    Args:
        error: Exception raised by the openai SDK (directly or via LangChain).

    Returns:
        LlmCredentialError for authentication, permission, quota, unknown-model and
        connection failures; None for anything else.
    """
    import openai

    invalid_key = LlmCredentialError("LLM_KEY_INVALID", "API key không hợp lệ hoặc không có quyền dùng model này.")
    if isinstance(error, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return invalid_key
    # Gemini's OpenAI-compatible endpoint answers a bad key with HTTP 400
    # ("Please pass a valid API key" / API_KEY_INVALID) instead of 401.
    if isinstance(error, openai.BadRequestError) and "api key" in str(error).lower().replace("_", " "):
        return invalid_key
    if isinstance(error, openai.RateLimitError):
        return LlmCredentialError("LLM_PROVIDER_ERROR", "API key đã dùng hết hạn mức hoặc bị giới hạn tốc độ. Vui lòng thử lại sau.")
    if isinstance(error, openai.NotFoundError):
        return LlmCredentialError("LLM_PROVIDER_ERROR", "Nhà cung cấp không tìm thấy model này. Vui lòng kiểm tra lại tên model.")
    if isinstance(error, (openai.APIConnectionError, openai.APITimeoutError)):
        return LlmCredentialError("LLM_PROVIDER_ERROR", "Không kết nối được tới nhà cung cấp AI.")
    return None
