"""Home Watch API — talks to team VSS. JWT stays on the server."""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import quote, urljoin

import requests
from flask import Flask, Response, jsonify, request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("home-watch")

VSS_URL = os.environ.get("VSS_URL", "").rstrip("/")
VSS_USERNAME = os.environ.get("VSS_USERNAME", "")
VSS_PASSWORD = os.environ.get("VSS_PASSWORD", "")
PORT = int(os.environ.get("PORT", "8080"))

CAMERAS = [
    {
        "id": "indoor",
        "label": "Indoor ceiling",
        "short": "Indoor",
        "camera_id": "smartspace_cam-1",
        "location": "indoor",
        "seed": "Warehouse_017_Camera_chunk_0009.mp4",
    },
    {
        "id": "dashcam",
        "label": "Dashcam",
        "short": "Car",
        "camera_id": "pie_cam-3",
        "location": "toronto",
        "seed": "set06_video_chunk_0014.mp4",
    },
    {
        "id": "house",
        "label": "House exterior",
        "short": "House",
        "camera_id": "neighborhood_cam-1",
        "location": "neighborhood",
        "seed": "neighborhood_20260901_chunk_0007.mp4",
    },
]

ALERT_SEARCHES = [
    {
        "kind": "house",
        "card_id": "house",
        "label": "House",
        "camera_id": "neighborhood_cam-1",
        "query": (
            "person, dog, or vehicle at the driveway or sidewalk "
            "outside the house, someone walking a dog, parked or approaching car"
        ),
    },
    {
        "kind": "car",
        "card_id": "dashcam",
        "label": "Car",
        "camera_id": "pie_cam-3",
        "query": (
            "pedestrian in the roadway, brake lights, road work, sudden stop, "
            "hazard while driving, SUV braking for a person crossing"
        ),
    },
]

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

app = Flask(__name__)
_http = requests.Session()
_token_lock = threading.Lock()
_cached_token: Optional[str] = None


@app.after_request
def _cors(resp: Response) -> Response:
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Range"
    resp.headers["Access-Control-Expose-Headers"] = "Content-Range, Accept-Ranges, Content-Length"
    return resp


def _vss(path: str) -> str:
    if not VSS_URL:
        raise RuntimeError("VSS_URL is not set")
    return urljoin(VSS_URL + "/", path.lstrip("/"))


def _login() -> str:
    if not VSS_USERNAME or not VSS_PASSWORD:
        raise RuntimeError("VSS credentials are not set")
    log.info("vss login user=%s url=%s", VSS_USERNAME, VSS_URL)
    r = _http.post(
        _vss("/api/v1/auth/login"),
        json={"username": VSS_USERNAME, "password": VSS_PASSWORD},
        timeout=30,
    )
    r.raise_for_status()
    token = (r.json() or {}).get("access_token")
    if not token:
        raise RuntimeError("vss login returned no access_token")
    return token


def _token(force: bool = False) -> str:
    global _cached_token
    with _token_lock:
        if force or not _cached_token:
            _cached_token = _login()
        return _cached_token


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def vss_json(method: str, path: str, **kwargs: Any) -> Any:
    timeout = kwargs.pop("timeout", 90)
    extra_headers = kwargs.pop("headers", {})
    last_err: Optional[Exception] = None
    for attempt, force in enumerate((False, True)):
        try:
            token = _token(force=force)
            r = _http.request(
                method,
                _vss(path),
                headers={**extra_headers, **_auth_headers(token)},
                timeout=timeout,
                **kwargs,
            )
            if r.status_code == 401 and attempt == 0:
                log.info("vss 401 on %s %s; re-login", method, path)
                continue
            r.raise_for_status()
            if not r.content:
                return None
            return r.json()
        except requests.HTTPError as exc:
            last_err = exc
            if exc.response is not None and exc.response.status_code == 401 and attempt == 0:
                continue
            raise
    if last_err:
        raise last_err
    raise RuntimeError("vss request failed")


def clip_url(source: str) -> str:
    return f"/api/clip?source={quote(source, safe='')}"


def _empty_camera(cam: dict) -> dict:
    return {
        "id": cam["id"],
        "label": cam["label"],
        "short": cam["short"],
        "camera_id": cam["camera_id"],
        "location": cam["location"],
        "source": "",
        "clip_url": "",
        "filename": cam["seed"],
        "start_sec": 0,
        "reasoning": "",
        "ok": False,
    }


def _chunk_score(chunk: dict) -> float:
    try:
        return float(chunk.get("similarity_score") or 0)
    except (TypeError, ValueError):
        return 0.0


def _pick_source(chunk: dict, start_sec: Optional[float] = None) -> str:
    if start_sec is not None:
        timeline = chunk.get("timeline") or []
        for seg in timeline:
            try:
                a = float(seg.get("segment_start_sec") or 0)
                b = float(seg.get("segment_end_sec") or 0)
            except (TypeError, ValueError):
                continue
            src = (seg.get("source") or "").strip()
            if src and a <= start_sec <= b:
                return src
            if src and start_sec >= a and (b == 0 or start_sec < b + 0.05):
                return src
        best = None
        for seg in timeline:
            src = (seg.get("source") or "").strip()
            if not src:
                continue
            try:
                a = float(seg.get("segment_start_sec") or 0)
            except (TypeError, ValueError):
                a = 0
            if start_sec >= a:
                best = src
        if best:
            return best
    return (chunk.get("preview_source") or "").strip()


def _summary(chunk: dict) -> str:
    text = (chunk.get("reasoning_content") or "").strip()
    if not text:
        return (chunk.get("filename") or chunk.get("original_video") or "clip").split("/")[-1]
    one = " ".join(text.split())
    if len(one) > 160:
        one = one[:157].rstrip() + "…"
    return one


def _matches_camera(chunk: dict, cam: dict) -> bool:
    cid = (chunk.get("camera_id") or "").strip()
    if cid and cid == cam["camera_id"]:
        return True
    blob = " ".join(
        str(chunk.get(k) or "")
        for k in ("original_video", "filename", "preview_source")
    )
    return cam["seed"] in blob


def _window(time_filter: str, day: Optional[str]) -> tuple[str, Optional[str], Optional[str]]:
    allowed = {"all", "1h", "24h", "7d", "custom"}
    if day:
        return "custom", f"{day}T00:00:00", f"{day}T23:59:59"
    tf = time_filter if time_filter in allowed else "all"
    return tf, None, None


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _in_window(
    ts: Any,
    time_filter: str,
    custom_start: Optional[str],
    custom_end: Optional[str],
) -> bool:
    if time_filter == "all":
        return True
    parsed = _parse_ts(ts)
    if parsed is None:
        return False
    if time_filter == "custom":
        start = _parse_ts(custom_start)
        end = _parse_ts(custom_end)
        if not start or not end:
            return False
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return start <= parsed <= end
    delta = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}.get(
        time_filter
    )
    if not delta:
        return True
    return parsed >= datetime.now(timezone.utc) - delta


def _latest_chunk(
    cam: dict,
    day: Optional[str] = None,
    time_filter: str = "all",
    custom_start: Optional[str] = None,
    custom_end: Optional[str] = None,
) -> Optional[dict]:
    extra: dict[str, Any] = {}
    if day:
        extra["date"] = day
    tries = [
        {"location": cam["location"], "limit": 48, **extra},
        {"limit": 48, **extra},
    ]
    for params in tries:
        data = vss_json(
            "GET",
            "/api/v1/videos/explore",
            params={"scope": "all", "indexed": "complete", **params},
        ) or {}
        chunks = data.get("chunks") or []
        matched = [c for c in chunks if _matches_camera(c, cam)]
        pool = matched or [
            c
            for c in chunks
            if (c.get("location") or "").strip().lower() == cam["location"].lower()
        ]
        pool = [
            c
            for c in pool
            if _in_window(c.get("upload_timestamp"), time_filter, custom_start, custom_end)
        ]
        if pool:
            pool.sort(key=lambda c: str(c.get("upload_timestamp") or ""), reverse=True)
            return pool[0]
    return None


def _camera_payload(
    cam: dict,
    day: Optional[str] = None,
    time_filter: str = "all",
    custom_start: Optional[str] = None,
    custom_end: Optional[str] = None,
) -> dict:
    out = _empty_camera(cam)
    try:
        chunk = _latest_chunk(cam, day, time_filter, custom_start, custom_end)
    except Exception:
        log.exception("explore failed for camera_id=%s", cam["camera_id"])
        return out
    if not chunk:
        return out
    source = _pick_source(chunk, 0)
    out.update(
        {
            "source": source,
            "clip_url": clip_url(source) if source else "",
            "filename": (chunk.get("filename") or cam["seed"]).split("/")[-1],
            "start_sec": float(chunk.get("best_match_start_sec") or 0),
            "reasoning": _summary(chunk),
            "ok": bool(source),
        }
    )
    return out


def _search_alerts(
    spec: dict,
    time_filter: str,
    custom_start: Optional[str],
    custom_end: Optional[str],
) -> dict:
    payload: dict[str, Any] = {
        "query": spec["query"],
        "top_k": 8,
        "llm_top_n": 3,
        "include_public": True,
        "time_filter": time_filter,
        "min_similarity": 0.12,
        "metadata_filters": {"camera_id": spec["camera_id"]},
    }
    if time_filter == "custom":
        payload["custom_start_date"] = custom_start
        payload["custom_end_date"] = custom_end
    data = vss_json("POST", "/api/v1/search", json=payload, timeout=120) or {}
    llm = data.get("llm_synthesis") or {}
    synthesis = ""
    if isinstance(llm, dict):
        synthesis = (llm.get("response") or "").strip()
    alerts = []
    for chunk in data.get("chunk_results") or []:
        start = float(chunk.get("best_match_start_sec") or 0)
        source = _pick_source(chunk, start)
        if not source:
            continue
        alerts.append(
            {
                "id": f"{spec['kind']}:{chunk.get('original_video')}:{chunk.get('best_segment_number')}",
                "kind": spec["kind"],
                "label": spec["label"],
                "card_id": spec["card_id"],
                "camera_id": spec["camera_id"],
                "summary": _summary(chunk),
                "source": source,
                "clip_url": clip_url(source),
                "filename": (chunk.get("filename") or "").split("/")[-1],
                "start_sec": start,
                "end_sec": float(chunk.get("best_match_end_sec") or start),
                "score": round(_chunk_score(chunk), 4),
            }
        )
    if not alerts:
        synthesis = ""
    elif not synthesis:
        synthesis = alerts[0]["summary"]
    return {
        "kind": spec["kind"],
        "label": spec["label"],
        "card_id": spec["card_id"],
        "text": synthesis,
        "alerts": alerts,
    }


@app.get("/health")
@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "team": VSS_USERNAME or "unknown"})


@app.get("/api/cameras")
def api_cameras():
    day = (request.args.get("date") or "").strip() or None
    time_filter, custom_start, custom_end = _window(
        (request.args.get("time_filter") or "all").strip(),
        day,
    )
    errors = 0
    by_id: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = {
            pool.submit(_camera_payload, cam, day, time_filter, custom_start, custom_end): cam
            for cam in CAMERAS
        }
        for fut in as_completed(futs):
            cam = futs[fut]
            try:
                by_id[cam["id"]] = fut.result()
            except Exception:
                errors += 1
                log.exception("camera payload failed id=%s", cam["id"])
                by_id[cam["id"]] = _empty_camera(cam)
    cameras = [by_id[c["id"]] for c in CAMERAS if c["id"] in by_id]
    healthy = bool(cameras) and all(c.get("ok") for c in cameras) and errors == 0
    return jsonify(
        {
            "team": VSS_USERNAME or "unknown",
            "healthy": healthy,
            "cameras": cameras,
        }
    )


@app.get("/api/alerts")
def api_alerts():
    day = (request.args.get("date") or "").strip() or None
    time_filter, custom_start, custom_end = _window(
        (request.args.get("time_filter") or "24h").strip(),
        day,
    )
    merged: list[dict] = []
    summaries: list[dict] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [
            pool.submit(_search_alerts, spec, time_filter, custom_start, custom_end)
            for spec in ALERT_SEARCHES
        ]
        for fut in as_completed(futs):
            try:
                pack = fut.result()
                summaries.append(
                    {
                        "kind": pack["kind"],
                        "label": pack["label"],
                        "card_id": pack["card_id"],
                        "text": pack["text"],
                    }
                )
                merged.extend(pack["alerts"])
            except Exception:
                log.exception("alert search failed")
    summaries.sort(key=lambda s: 0 if s.get("kind") == "house" else 1)
    seen = set()
    uniq = []
    for item in sorted(merged, key=lambda a: a.get("score") or 0, reverse=True):
        key = (item.get("kind"), item.get("source"), round(item.get("start_sec") or 0, 1))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    return jsonify(
        {
            "team": VSS_USERNAME or "unknown",
            "time_filter": time_filter,
            "date": day,
            "summaries": summaries,
            "alerts": uniq[:12],
        }
    )


@app.get("/api/clip")
def api_clip():
    source = (request.args.get("source") or "").strip()
    if not source.startswith("s3://"):
        return jsonify({"error": "source must be an s3:// URI"}), 400
    headers = {}
    if request.headers.get("Range"):
        headers["Range"] = request.headers["Range"]
    url = _vss("/api/v1/videos/stream")
    upstream = None
    for attempt in range(2):
        token = _token(force=attempt == 1)
        upstream = _http.get(
            url,
            params={"source": source, "token": token},
            headers=headers,
            stream=True,
            timeout=120,
        )
        if upstream.status_code == 401 and attempt == 0:
            upstream.close()
            continue
        break
    assert upstream is not None
    if upstream.status_code >= 400:
        body = upstream.content[:500]
        ctype = upstream.headers.get("Content-Type", "text/plain")
        upstream.close()
        return Response(body, status=upstream.status_code, content_type=ctype)

    out_headers = {}
    for key, val in upstream.headers.items():
        if key.lower() in HOP_BY_HOP:
            continue
        if key.lower() in {"content-type", "content-length", "content-range", "accept-ranges", "etag", "cache-control"}:
            out_headers[key] = val
    if "Accept-Ranges" not in out_headers:
        out_headers["Accept-Ranges"] = "bytes"

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return Response(
        generate(),
        status=upstream.status_code,
        headers=out_headers,
        mimetype=upstream.headers.get("Content-Type", "video/mp4"),
        direct_passthrough=True,
    )


if __name__ == "__main__":
    if not VSS_URL:
        log.warning("VSS_URL is empty; API calls will fail until the Secret is mounted")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
