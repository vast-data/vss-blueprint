"""Explore browse indexing: group rows and attach stream chunk labels."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.utils.browse_chunk import fully_indexed_videos, incomplete_videos
from src.utils.dashboard_stats import (
    _metadata_label,
    _parse_upload_day,
    build_metadata_filter_options,
)
from src.utils.stream_index import build_stream_meta_by_video


def group_accessible_rows(
    raw_rows: List[dict],
    has_access: Callable[[dict], bool],
) -> Tuple[Dict[str, List[dict]], Dict[str, dict]]:
    """Group segment rows by original_video with catalog metadata for explore."""
    rows_by_video: Dict[str, List[dict]] = defaultdict(list)
    by_video: Dict[str, dict] = {}

    for row in raw_rows:
        if not has_access(row):
            continue
        ov = str(row.get("original_video") or row.get("source") or "").strip()
        if not ov:
            continue

        rows_by_video[ov].append(row)
        day = _parse_upload_day(row.get("upload_timestamp"))
        loc = _metadata_label(row.get("location"))
        cam = _metadata_label(row.get("camera_id"))
        entry = by_video.get(ov)
        if entry is None:
            by_video[ov] = {
                "original_video": ov,
                "upload_day": day,
                "upload_timestamp": row.get("upload_timestamp"),
                "location_label": loc,
                "camera_label": cam,
            }
            continue

        ts = row.get("upload_timestamp")
        if ts is not None and (
            entry["upload_timestamp"] is None or str(ts) > str(entry["upload_timestamp"])
        ):
            entry["upload_timestamp"] = ts
            if day:
                entry["upload_day"] = day
        if entry.get("location_label") == "(empty)" and loc != "(empty)":
            entry["location_label"] = loc
        if entry.get("camera_label") == "(empty)" and cam != "(empty)":
            entry["camera_label"] = cam

    return rows_by_video, by_video


def build_explore_catalog(
    rows_by_video: Dict[str, List[dict]],
    by_video: Dict[str, dict],
    *,
    indexed: str = "complete",
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, dict], List[dict]]:
    """
    Filter chunks and derive stream labels plus flat rows.

    indexed="complete" keeps parents whose segment slots are all present.
    indexed="partial" keeps parents that have rows but are still missing slots.

    Returns stream_meta_by_video, filtered by_video, accessible_flat rows.
    """
    if indexed == "partial":
        selected = incomplete_videos(rows_by_video)
    else:
        selected = fully_indexed_videos(rows_by_video)
    filtered = {ov: entry for ov, entry in by_video.items() if ov in selected}
    stream_meta = build_stream_meta_by_video(rows_by_video)
    accessible_flat = [row for ov in selected for row in rows_by_video[ov]]
    return stream_meta, filtered, accessible_flat


def uploads_by_day_counts(by_video: Dict[str, dict]) -> List[dict]:
    counter: Counter[str] = Counter()
    for entry in by_video.values():
        if entry.get("upload_day"):
            counter[str(entry["upload_day"])] += 1
    return [{"date": day, "chunk_count": count} for day, count in sorted(counter.items(), reverse=True)]


def location_filter_options(accessible_flat: List[dict], has_videos: bool) -> List[dict]:
    return build_metadata_filter_options(accessible_flat, "location") if has_videos else []


def camera_filter_options(accessible_flat: List[dict], has_videos: bool) -> List[dict]:
    return build_metadata_filter_options(accessible_flat, "camera_id") if has_videos else []
