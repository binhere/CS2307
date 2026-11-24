from __future__ import annotations

from collections import OrderedDict
from threading import Lock
from typing import List, Optional

import numpy as np
import requests
from loguru import logger

from src.settings.config import config_value

# The search embeds the query inside this stage budget (see service.py). A longer
# HTTP timeout would only leave an abandoned request running after the stage gave up.
EMBEDDING_STAGE_TIMEOUT_SETTING = "SEARCH_EMBEDDING_STAGE_TIMEOUT_SECONDS"
DEFAULT_EMBEDDING_STAGE_TIMEOUT_SECONDS = 20.0

# One pooled session keeps the TCP/TLS connection to the embedding API alive
# between searches instead of reconnecting for every query.
http_session = requests.Session()


def embedding_stage_timeout_seconds() -> float:
    """Read the search stage budget for embedding one query.

    Returns:
        float: SEARCH_EMBEDDING_STAGE_TIMEOUT_SECONDS when it is a positive number,
        otherwise the 20 s default.
    """
    try:
        seconds = float(config_value(EMBEDDING_STAGE_TIMEOUT_SETTING, DEFAULT_EMBEDDING_STAGE_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_EMBEDDING_STAGE_TIMEOUT_SECONDS
    return seconds if seconds > 0 else DEFAULT_EMBEDDING_STAGE_TIMEOUT_SECONDS


class E5EmbeddingModel:
    """Embedding client backed by an OpenAI-compatible embeddings endpoint.

    The backend intentionally does not load a local Hugging Face model. This
    keeps model memory and ML runtime dependencies out of the API container;
    LM Studio/OpenRouter (or another compatible endpoint) owns embedding
    inference instead.
    """

    dimension = 1024

    def __init__(self):
        configured_provider = str(config_value("SEARCH_EMBEDDING_PROVIDER", "lmstudio")).strip().lower()
        if configured_provider != "lmstudio":
            logger.warning(
                "Only the OpenAI-compatible embedding endpoint is supported; "
                "using lmstudio API mode instead of provider={!r}",
                configured_provider,
            )
        self.provider = "lmstudio"
        self.model_name = config_value("SEARCH_EMBEDDING_MODEL", "qwen/qwen3-embedding-4b")
        self.base_url = str(
            config_value("SEARCH_EMBEDDING_BASE_URL", "http://127.0.0.1:1234/v1")
        ).rstrip("/")
        self.api_key = str(config_value("SEARCH_EMBEDDING_API_KEY", "") or "").strip()
        requested_dimensions = str(config_value("SEARCH_EMBEDDING_REQUEST_DIMENSIONS", "") or "").strip()
        try:
            self.request_dimensions = int(requested_dimensions) if requested_dimensions else None
            if self.request_dimensions is not None and self.request_dimensions <= 0:
                raise ValueError
        except ValueError:
            logger.warning("Invalid SEARCH_EMBEDDING_REQUEST_DIMENSIONS; omitting it from the request")
            self.request_dimensions = None
        try:
            self.request_timeout = float(config_value("SEARCH_EMBEDDING_TIMEOUT", "60"))
        except (TypeError, ValueError):
            logger.warning("Invalid SEARCH_EMBEDDING_TIMEOUT; using 60 seconds")
            self.request_timeout = 60.0
        # Passage batches (index backfill) keep the full timeout; a single query
        # must fit the search stage budget.
        self.query_timeout = min(self.request_timeout, embedding_stage_timeout_seconds())
        self._query_cache = OrderedDict()
        self._cache_lock = Lock()

    @property
    def device_name(self) -> str:
        return "embedding-api"

    @property
    def is_gpu(self) -> bool:
        return False

    def encode(self, texts: List[str], kind: str = "query") -> Optional[np.ndarray]:
        if not texts:
            return None
        prefix = "query: " if kind == "query" else "passage: "
        prepared = [prefix + (text or "") for text in texts]
        timeout = self.query_timeout if kind == "query" else self.request_timeout
        return self._encode_api(prepared, timeout)

    def _encode_api(self, texts: List[str], timeout: float) -> Optional[np.ndarray]:
        """Embed prepared texts through the OpenAI-compatible endpoint.

        Args:
            texts: Inputs already carrying their "query: "/"passage: " prefix.
            timeout: HTTP timeout in seconds for this request.

        Returns:
            Optional[np.ndarray]: float32 array of shape (len(texts), 1024) with
            unit-length rows, or None when the request or its payload is invalid.
        """
        endpoint = self.base_url if self.base_url.endswith("/embeddings") else f"{self.base_url}/embeddings"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {"model": self.model_name, "input": texts}
        if self.request_dimensions is not None:
            payload["dimensions"] = self.request_dimensions
        try:
            response = http_session.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json().get("data", [])
            ordered = sorted(data, key=lambda item: item.get("index", 0))
            vectors = np.asarray([item["embedding"] for item in ordered], dtype="float32")
            if vectors.ndim != 2 or vectors.shape != (len(texts), self.dimension):
                raise ValueError(
                    f"expected {len(texts)} vectors with {self.dimension} dimensions, got {vectors.shape}"
                )
            if not np.all(np.isfinite(vectors)):
                raise ValueError("Embedding API returned a non-finite embedding")
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            if np.any(norms == 0):
                raise ValueError("Embedding API returned a zero-length embedding")
            return (vectors / norms).astype("float32")
        except (requests.RequestException, AttributeError, KeyError, TypeError, ValueError) as exc:
            logger.warning(f"Semantic search disabled; embedding API request failed: {exc}")
            return None

    def encode_query(self, text: str):
        with self._cache_lock:
            cached = self._query_cache.get(text)
            if cached is not None:
                self._query_cache.move_to_end(text)
                return cached
        result = self.encode([text], kind="query")
        if result is None:
            # Do not cache transient embedding API failures.
            return None
        vector = result[0]
        with self._cache_lock:
            self._query_cache[text] = vector
            self._query_cache.move_to_end(text)
            if len(self._query_cache) > 512:
                self._query_cache.popitem(last=False)
        return vector


embedding_model = E5EmbeddingModel()
