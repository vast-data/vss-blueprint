"""Short live probes of the reasoner and embed NIM endpoints for the dashboard."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import requests

from src.config import cosmos_synthesis_url, get_settings
from src.services.cosmos_embed_client import CosmosEmbed1Client
from src.utils.nim_metrics import interpret_probe, parse_prometheus

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SEC = 2.0
PROBE_CACHE_SEC = 15.0

_cache_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": 0.0, "value": None}


def _v1_base_from_chat(url: str) -> str:
    suffix = "/chat/completions"
    if url.endswith(suffix):
        return url[: -len(suffix)]
    return url.rstrip("/")


def _auth_header(token: str) -> Dict[str, str]:
    text = (token or "").strip()
    if not text:
        return {}
    value = text if text.lower().startswith("bearer ") else f"Bearer {text}"
    return {"Authorization": value}


def _get(url: str, headers: Dict[str, str]) -> Tuple[Optional[int], Optional[str], str]:
    """Return (status, error_kind, body). error_kind is timeout or connection."""
    try:
        response = requests.get(url, headers=headers, timeout=PROBE_TIMEOUT_SEC)
    except requests.Timeout:
        return None, "timeout", ""
    except requests.RequestException:
        return None, "connection", ""
    return response.status_code, None, response.text or ""


def _probe_base(
    *,
    name: str,
    label: str,
    model: str,
    base: str,
    headers: Dict[str, str],
) -> Dict[str, Any]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        health_future = pool.submit(_get, f"{base}/health/ready", headers)
        metrics_future = pool.submit(_get, f"{base}/metrics", headers)
        health_code, health_error, _body = health_future.result()
        metrics_code, metrics_error, metrics_body = metrics_future.result()
    parsed: Dict[str, Any] = {}
    if health_error is None and health_code is not None and health_code < 500:
        if metrics_error is None and metrics_code == 200:
            parsed = parse_prometheus(metrics_body)
            if not parsed.get("metrics_available"):
                parsed = {}
    return interpret_probe(
        name=name,
        label=label,
        model=model,
        health_code=health_code,
        error_kind=health_error,
        parsed=parsed,
    )


def _probe_reasoner() -> Dict[str, Any]:
    settings = get_settings()
    model = settings.cosmos_model or ""
    host = (settings.cosmos_host or "").strip()
    if not host:
        return interpret_probe(
            name="reasoner",
            label="Reasoner",
            model=model,
            health_code=None,
            error_kind=None,
            detail="Cosmos host is not configured",
        )
    base = _v1_base_from_chat(cosmos_synthesis_url(settings))
    return _probe_base(
        name="reasoner",
        label="Reasoner",
        model=model,
        base=base,
        headers=_auth_header(settings.cosmos_authorization),
    )


def _probe_embed() -> Dict[str, Any]:
    client = CosmosEmbed1Client()
    return _probe_base(
        name="embed",
        label="Embed",
        model=client.model or "",
        base=client.base_url.rstrip("/"),
        headers=client._headers(),
    )


def _safe(kind: str, fn) -> Dict[str, Any]:
    try:
        return fn()
    except Exception as exc:
        logger.warning("[DASHBOARD] %s probe failed: %s", kind, exc)
        return interpret_probe(
            name=kind,
            label="Reasoner" if kind == "reasoner" else "Embed",
            model="",
            health_code=None,
            error_kind="connection",
            detail="Probe failed",
        )


def _probe_both() -> Dict[str, Any]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        reasoner_future = pool.submit(_safe, "reasoner", _probe_reasoner)
        embed_future = pool.submit(_safe, "embed", _probe_embed)
        reasoner = reasoner_future.result()
        embed = embed_future.result()
    for endpoint in (reasoner, embed):
        if endpoint.get("status") in ("down", "unavailable") and endpoint.get("detail"):
            logger.warning(
                "[DASHBOARD] %s probe %s: %s",
                endpoint.get("name"),
                endpoint.get("status"),
                endpoint.get("detail"),
            )
    return {
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "reasoner": reasoner,
        "embed": embed,
    }


def probe_model_endpoints() -> Dict[str, Any]:
    """Reasoner and embed health plus NIM metrics. Cached briefly. Never raises."""
    now = time.monotonic()
    with _cache_lock:
        cached = _cache["value"]
        if cached is not None and now - float(_cache["at"]) < PROBE_CACHE_SEC:
            return cached
    value = _probe_both()
    with _cache_lock:
        _cache["at"] = time.monotonic()
        _cache["value"] = value
    return value


def clear_probe_cache() -> None:
    with _cache_lock:
        _cache["at"] = 0.0
        _cache["value"] = None
