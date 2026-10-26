"""Parse NIM Prometheus text and turn a probe into a dashboard endpoint."""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

_SAMPLE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>\S+)"
)

_WANTED = {
    "num_requests_running": "running",
    "num_requests_waiting": "waiting",
    "gpu_cache_usage_perc": "cache",
    "kv_cache_usage_perc": "cache",
    "e2e_request_latency_seconds_sum": "lat_sum",
    "e2e_request_latency_seconds_count": "lat_count",
    "gpu_memory_used_bytes": "mem_used",
    "gpu_memory_total_bytes": "mem_total",
    "gpu_utilization": "gpu_util",
    "time_to_first_token_seconds_sum": "ttft_sum",
    "time_to_first_token_seconds_count": "ttft_count",
    "request_prefill_time_seconds_sum": "prefill_sum",
    "request_prefill_time_seconds_count": "prefill_count",
    "request_decode_time_seconds_sum": "decode_sum",
    "request_decode_time_seconds_count": "decode_count",
    "request_success_total": "success",
}

# Prefer the newer vLLM cache gauge when both are published.
_CACHE_RANK = {"kv_cache_usage_perc": 2, "gpu_cache_usage_perc": 1}


def _metric_name(name: str) -> Optional[str]:
    base = name.split(":")[-1]
    return base if base in _WANTED else None


def _number(raw: str) -> Optional[float]:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def _as_percent(value: float) -> float:
    """NIM cache gauges are a fraction (1 = 100%). Values above 1 are already percent."""
    pct = value * 100.0 if value <= 1.0 else value
    return round(max(0.0, min(100.0, pct)), 1)


def _label(labels: str, key: str) -> str:
    if not labels:
        return ""
    match = re.search(rf'(?:^|,){re.escape(key)}="([^"]*)"', labels)
    return match.group(1) if match else ""


def _avg(totals: Dict[str, float], seen: Dict[str, bool], sum_key: str, count_key: str) -> Optional[float]:
    if not seen.get(count_key) or totals.get(count_key, 0) <= 0:
        return None
    return round(totals[sum_key] / totals[count_key], 2)


def parse_prometheus(text: str) -> Dict[str, Any]:
    """Pull queue, GPU, latency split, and finish counts from a /v1/metrics body."""
    totals = {
        "running": 0.0,
        "waiting": 0.0,
        "lat_sum": 0.0,
        "lat_count": 0.0,
        "mem_used": 0.0,
        "mem_total": 0.0,
        "ttft_sum": 0.0,
        "ttft_count": 0.0,
        "prefill_sum": 0.0,
        "prefill_count": 0.0,
        "decode_sum": 0.0,
        "decode_count": 0.0,
        "finished": 0.0,
        "cut_off": 0.0,
        "failed": 0.0,
    }
    seen = {key: False for key in totals}
    cache_value: Optional[float] = None
    cache_rank = 0
    gpu_util: Optional[float] = None

    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _SAMPLE.match(stripped)
        if not match:
            continue
        base = _metric_name(match.group("name"))
        if not base:
            continue
        value = _number(match.group("value"))
        if value is None:
            continue
        slot = _WANTED[base]
        if slot == "cache":
            rank = _CACHE_RANK.get(base, 0)
            if rank > cache_rank:
                cache_rank = rank
                cache_value = value
            elif rank == cache_rank:
                cache_value = max(cache_value or 0.0, value)
            continue
        if slot == "gpu_util":
            # HELP says 0.0–1.0. Keep the busiest device.
            scaled = value * 100.0
            gpu_util = scaled if gpu_util is None else max(gpu_util, scaled)
            continue
        if slot == "success":
            reason = _label(match.group("labels") or "", "finished_reason")
            if reason == "length":
                bucket = "cut_off"
            elif reason in ("", "stop"):
                bucket = "finished"
            else:
                bucket = "failed"
            totals[bucket] += value
            seen[bucket] = True
            continue
        totals[slot] += value
        seen[slot] = True

    memory_pct = None
    if seen["mem_total"] and totals["mem_total"] > 0:
        memory_pct = round(max(0.0, min(100.0, (totals["mem_used"] / totals["mem_total"]) * 100.0)), 1)

    outcomes = seen["finished"] or seen["cut_off"] or seen["failed"]
    metrics_available = (
        any(seen[key] for key in ("running", "waiting", "lat_count", "mem_total"))
        or cache_value is not None
        or gpu_util is not None
        or outcomes
        or _avg(totals, seen, "ttft_sum", "ttft_count") is not None
    )
    return {
        "running": int(totals["running"]) if seen["running"] else None,
        "waiting": int(totals["waiting"]) if seen["waiting"] else None,
        "cache_pct": _as_percent(cache_value) if cache_value is not None else None,
        "avg_latency_sec": _avg(totals, seen, "lat_sum", "lat_count"),
        "gpu_memory_pct": memory_pct,
        "gpu_util_pct": round(max(0.0, min(100.0, gpu_util)), 1) if gpu_util is not None else None,
        "ttft_sec": _avg(totals, seen, "ttft_sum", "ttft_count"),
        "prefill_sec": _avg(totals, seen, "prefill_sum", "prefill_count"),
        "decode_sec": _avg(totals, seen, "decode_sum", "decode_count"),
        "finished": int(totals["finished"]) if outcomes else None,
        "cut_off": int(totals["cut_off"]) if outcomes else None,
        "failed": int(totals["failed"]) if outcomes else None,
        "metrics_available": metrics_available,
    }


def interpret_probe(
    *,
    name: str,
    label: str,
    model: str,
    health_code: Optional[int],
    error_kind: Optional[str],
    parsed: Optional[Dict[str, Any]] = None,
    detail: Optional[str] = None,
) -> Dict[str, Any]:
    """Map a health code and optional metrics scrape into one live tile."""
    metrics = parsed or {}
    metrics_available = bool(metrics.get("metrics_available"))
    status = "down"
    resolved = detail

    if error_kind == "timeout":
        status, resolved = "down", resolved or "Timed out"
    elif error_kind == "connection":
        status, resolved = "down", resolved or "Connection failed"
    elif health_code is not None and health_code >= 500:
        status, resolved = "down", resolved or f"HTTP {health_code}"
    elif health_code == 200 or metrics_available:
        status, resolved = "ready", None
        waiting = int(metrics.get("waiting") or 0)
        cache = metrics.get("cache_pct")
        if waiting > 0 or (cache is not None and float(cache) >= 85):
            status = "busy"
    elif health_code in (401, 403):
        status, resolved = "unavailable", resolved or "Auth rejected"
    elif health_code == 404:
        status, resolved = "unavailable", resolved or "No health endpoint"
    elif health_code is None and not error_kind:
        status, resolved = "unavailable", resolved or "Not configured"
    else:
        status, resolved = "down", resolved or "No response"

    if status in ("down", "unavailable"):
        metrics = {}
        metrics_available = False

    return {
        "name": name,
        "label": label,
        "status": status,
        "model": model or "",
        "detail": resolved,
        "running": metrics.get("running"),
        "waiting": metrics.get("waiting"),
        "cache_pct": metrics.get("cache_pct"),
        "avg_latency_sec": metrics.get("avg_latency_sec"),
        "gpu_memory_pct": metrics.get("gpu_memory_pct"),
        "gpu_util_pct": metrics.get("gpu_util_pct"),
        "ttft_sec": metrics.get("ttft_sec"),
        "prefill_sec": metrics.get("prefill_sec"),
        "decode_sec": metrics.get("decode_sec"),
        "finished": metrics.get("finished"),
        "cut_off": metrics.get("cut_off"),
        "failed": metrics.get("failed"),
        "metrics_available": metrics_available,
    }
