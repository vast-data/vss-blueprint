"""
NVIDIA Cosmos-Embed1 NIM client (/v1/embeddings).

API differs from OpenAI-style embed models:
  - request_type: query | bulk_text | bulk_video
  - model: nvidia/cosmos-embed1
  - output: 256-dim vectors (no dimensions parameter)
"""
import base64
import logging
from typing import List, Optional

import requests

COSMOS_EMBED1_MODEL = "nvidia/cosmos-embed1"

_CONN_EXC = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)
# Statuses raised as TransientError.
_TRANSIENT_STATUS = (401, 403, 408, 429, 500, 502, 503, 504)


class TransientError(Exception):
    """Error the pipeline retries."""


class CosmosEmbed1Client:
    def __init__(self, settings):
        self.model = getattr(settings, "embeddingmodel", COSMOS_EMBED1_MODEL) or COSMOS_EMBED1_MODEL
        if "cosmos-embed" not in self.model.lower():
            self.model = COSMOS_EMBED1_MODEL
        self.nvidia_api_key = getattr(settings, "nvidia_api_key", None) or ""
        self.embedding_authorization = getattr(settings, "embedding_authorization", "") or ""
        self.is_cloud = not getattr(settings, "embedding_local_nim", False)
        scheme = (getattr(settings, "embeddinghttpscheme", "http") or "http").rstrip(":/")
        host = (getattr(settings, "embeddinghost", "localhost") or "localhost").strip().strip("/")
        port = int(getattr(settings, "embeddingport", 8002) or 8002)
        default_port = 443 if scheme == "https" else 80
        if "/" in host:  # host includes a path, e.g. gateway/tenant/model
            hostname, _, path = host.partition("/")
            netloc = hostname if port == default_port else f"{hostname}:{port}"
            self.base_url = f"{scheme}://{netloc}/{path}/v1"
        elif port == default_port:
            self.base_url = f"{scheme}://{host}/v1"
        else:
            self.base_url = f"{scheme}://{host}:{port}/v1"
        self.expected_dim = int(getattr(settings, "embeddingdimensions", 256) or 256)

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        token = (self.embedding_authorization or "").strip()
        if token:
            headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
        elif self.is_cloud and self.nvidia_api_key:
            headers["Authorization"] = f"Bearer {self.nvidia_api_key}"
        return headers

    def _post(self, payload: dict, timeout: int = 120) -> List[List[float]]:
        url = f"{self.base_url}/embeddings"
        # One connection retry, then TransientError.
        try:
            response = requests.post(url, json=payload, headers=self._headers(), timeout=timeout)
        except _CONN_EXC as exc:
            logging.warning("[RETRY] connection error to %s; retrying once: %s", url, exc)
            try:
                response = requests.post(url, json=payload, headers=self._headers(), timeout=timeout)
            except _CONN_EXC as exc2:
                raise TransientError(f"connection failed after 1 retry: {exc2}") from exc2
        if response.status_code in _TRANSIENT_STATUS:
            raise TransientError(f"transient HTTP {response.status_code}: {response.text[:300]}")
        if response.status_code == 200:
            items = response.json().get("data", [])
            vectors = [item.get("embedding", []) for item in items if item.get("embedding")]
            if not vectors:
                raise RuntimeError("Cosmos-Embed1 returned no embeddings")
            return vectors
        raise TransientError(f"Cosmos-Embed1 failed: {response.status_code}: {response.text[:500]}")

    def embed_texts(self, texts: List[str], *, for_query: bool = False) -> List[List[float]]:
        if not texts:
            return []
        if len(texts) == 1:
            for_query = True
        if for_query and len(texts) == 1:
            payload = {
                "input": texts[0],
                "model": self.model,
                "request_type": "query",
                "encoding_format": "float",
            }
        else:
            payload = {
                "input": texts,
                "model": self.model,
                "request_type": "bulk_text",
                "encoding_format": "float",
            }
        logging.info(
            f"[COSMOS_EMBED] Text embed | n={len(texts)} | "
            f"request_type={payload['request_type']}"
        )
        return self._post(payload)

    def embed_video_mp4(self, video_bytes: bytes) -> List[float]:
        """Embed a short segment MP4 (query mode, base64 video)."""
        if not video_bytes:
            raise ValueError("Empty video bytes")
        b64 = base64.b64encode(video_bytes).decode("ascii")
        payload = {
            "input": f"data:video/mp4;base64,{b64}",
            "model": self.model,
            "request_type": "query",
            "encoding_format": "float",
        }
        logging.info(f"[COSMOS_EMBED] Video embed | {len(video_bytes)} bytes")
        vectors = self._post(payload, timeout=180)
        return vectors[0]

    def embed_query_text(self, text: str) -> List[float]:
        return self.embed_texts([text], for_query=True)[0]
