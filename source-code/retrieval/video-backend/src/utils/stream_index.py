"""Parse stream/chunk indexing fields stored in VastDB extra_metadata or row dicts."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Dict, List, Optional


def parse_extra_metadata(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    text = str(raw).strip()
    if not text or text == "{}":
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def stream_fields_from_row(row: dict) -> Dict[str, Any]:
    """Merge top-level row keys with extra_metadata stream index fields."""
    extra = parse_extra_metadata(row.get("extra_metadata"))
    out: Dict[str, Any] = {}
    for key in ("stream_id", "chunk_index", "chunk_start_sec", "stream_position_sec", "ingest_kind"):
        val = row.get(key)
        if val is None or val == "":
            val = extra.get(key)
        if val is not None and val != "":
            out[key] = val
    return out


def row_source_key(row: dict) -> str:
    """Stable identity for an indexed segment clip."""
    src = str(row.get("source") or "").strip()
    if src:
        return src
    ov = str(row.get("original_video") or "").strip()
    sn = int(row.get("segment_number") or 0)
    return f"{ov}#{sn}"


def format_stream_time(sec: Optional[float]) -> str:
    if sec is None:
        return "?"
    try:
        total = max(0, int(float(sec)))
    except (TypeError, ValueError):
        return "?"
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def stream_identity(row: dict) -> Dict[str, Any]:
    """stream_id and chunk_index for one indexed video, or {} when it is not a chunk."""
    fields = stream_fields_from_row(row)
    stream_id = str(fields.get("stream_id") or "").strip()
    chunk_index = _parse_chunk_index(fields.get("chunk_index"))
    if not stream_id or chunk_index is None:
        return {}
    return {"stream_id": stream_id, "chunk_index": chunk_index}


def _parse_chunk_index(raw: Any) -> Optional[int]:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def build_stream_meta_by_video(rows_by_video: Dict[str, List[dict]]) -> Dict[str, Dict[str, Any]]:
    """
    Per original_video stream labels for explore cards.

    Values: stream_id, chunk_index (0-based), stream_chunk_total (1-based session size),
    stream_chunk_indices (every indexed chunk in that capture, sorted),
    stream_chunk_previews (first segment file of each of those chunks).
    """
    session_max: Dict[str, int] = defaultdict(int)
    pending: Dict[str, Dict[str, Any]] = {}

    for ov, rows in rows_by_video.items():
        stream_id: Optional[str] = None
        chunk_index: Optional[int] = None
        for row in rows:
            fields = stream_fields_from_row(row)
            sid = str(fields.get("stream_id") or "").strip()
            if sid:
                stream_id = sid
            idx = _parse_chunk_index(fields.get("chunk_index"))
            if idx is not None:
                chunk_index = idx
        if stream_id and chunk_index is not None:
            pending[ov] = {
                "stream_id": stream_id,
                "chunk_index": chunk_index,
                "source": _first_segment_source(rows),
            }
            session_max[stream_id] = max(session_max[stream_id], chunk_index + 1)

    by_stream: Dict[str, List[tuple]] = defaultdict(list)
    for ov, meta in pending.items():
        by_stream[str(meta["stream_id"])].append((int(meta["chunk_index"]), ov))

    labeled: Dict[str, Dict[str, Any]] = {}
    for ov, meta in pending.items():
        sid = str(meta["stream_id"])
        idx = int(meta["chunk_index"])
        siblings = by_stream[sid]
        lower = [i for i, other in siblings if other != ov and i < idx]
        higher = [i for i, other in siblings if other != ov and i > idx]
        previews = []
        for sibling_index, sibling_ov in sorted(siblings, key=lambda item: int(item[0])):
            source = str(pending[sibling_ov].get("source") or "").strip()
            if source:
                previews.append({"chunk_index": int(sibling_index), "source": source})
        labeled[ov] = {
            "stream_id": meta["stream_id"],
            "chunk_index": meta["chunk_index"],
            "stream_chunk_total": session_max[sid],
            "stream_chunk_indices": sorted({int(i) for i, _other in siblings}),
            "stream_chunk_previews": previews,
            "prev_chunk_index": max(lower) if lower else None,
            "next_chunk_index": min(higher) if higher else None,
        }
    return labeled


def _first_segment_source(rows: List[dict]) -> str:
    """Stored file for the earliest segment of one chunk."""
    chosen = ""
    chosen_number: Optional[int] = None
    for row in rows:
        source = str(row.get("source") or "").strip()
        if not source:
            continue
        try:
            number = int(row.get("segment_number") or 0)
        except (TypeError, ValueError):
            number = 0
        if chosen_number is None or number < chosen_number:
            chosen = source
            chosen_number = number
    return chosen
