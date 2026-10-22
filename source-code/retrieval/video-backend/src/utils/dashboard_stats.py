"""Aggregate VastDB row snapshots into dashboard statistics."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from src.utils.browse_chunk import fully_indexed_videos
from src.utils.stream_index import row_source_key, stream_fields_from_row
from src.ingest_metadata import FILTERABLE_METADATA_COLUMNS

# Matches video-segmenter default output_bucket_suffix.
SEGMENTER_OUTPUT_BUCKET_SUFFIX = "-segments"


def _split_object_classes(value: Any) -> List[str]:
    text = str(value or "").strip()
    if not text:
        return []
    parts: List[str] = []
    for part in text.replace("|", ",").replace(";", ",").split(","):
        label = part.strip().lower()
        if label:
            parts.append(label)
    # Preserve order, drop dupes within a row
    seen: Set[str] = set()
    out: List[str] = []
    for label in parts:
        if label not in seen:
            seen.add(label)
            out.append(label)
    return out


def _parse_object_counts(value: Any) -> Dict[str, int]:
    """Parse YOLO object_counts JSON into {class: concurrent_count}."""
    if value is None or value == "":
        return {}
    data = value
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, int] = {}
    for key, raw in data.items():
        label = str(key or "").strip().lower()
        if not label:
            continue
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if n > 0:
            out[label] = n
    return out


def _parse_upload_day(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.date().isoformat()
    text = str(value).strip()
    if not text:
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).date().isoformat()
    except ValueError:
        return text[:10] if len(text) >= 10 else None


def _metadata_label(value: Any) -> str:
    text = str(value or "").strip()
    return text if text else "(empty)"


def filter_rows_by_metadata_label(
    rows: List[dict],
    field: str,
    label: Optional[str],
) -> List[dict]:
    """Keep rows whose metadata field normalizes to the given label."""
    if not label:
        return rows
    return [row for row in rows if _metadata_label(row.get(field)) == label]


def build_metadata_filter_options(rows: List[dict], field: str, limit: int = 30) -> List[Dict[str, Any]]:
    """Video counts per metadata label (for explore filter pills)."""
    by_video: Dict[str, str] = {}
    for row in rows:
        ov = str(row.get("original_video") or row.get("source") or "").strip()
        if not ov:
            continue
        label = _metadata_label(row.get(field))
        prev = by_video.get(ov)
        if prev is None or (prev == "(empty)" and label != "(empty)"):
            by_video[ov] = label
    counter: Counter[str] = Counter(by_video.values())
    return [
        {"label": label, "chunk_count": count}
        for label, count in counter.most_common(limit)
    ]


def build_location_filter_options(rows: List[dict], limit: int = 30) -> List[Dict[str, Any]]:
    """Video counts per location label."""
    return build_metadata_filter_options(rows, "location", limit)


def _group_key(row: dict) -> str:
    stream = stream_fields_from_row(row)
    stream_id = str(stream.get("stream_id") or "").strip()
    if stream_id:
        return f"stream:{stream_id}"
    ov = str(row.get("original_video") or row.get("source") or "").strip()
    return f"video:{ov}" if ov else f"row:{row_source_key(row)}"


def build_dashboard_stats(rows: List[dict]) -> Dict[str, Any]:
    """Build dashboard payload from accessible VastDB rows."""
    segment_rows = list(rows)
    other_rows = 0

    parent_videos: Set[str] = set()
    rows_by_video: Dict[str, List[dict]] = defaultdict(list)
    for row in segment_rows:
        ov = str(row.get("original_video") or row.get("source") or "").strip()
        if ov:
            parent_videos.add(ov)
            rows_by_video[ov].append(row)
    fully_indexed_count = len(fully_indexed_videos(rows_by_video))

    source_counter: Counter[str] = Counter()
    for row in segment_rows:
        source_counter[row_source_key(row)] += 1
    re_ingest_rows = sum(count - 1 for count in source_counter.values() if count > 1)
    re_ingest_clips = sum(1 for count in source_counter.values() if count > 1)
    indexed_clips = len(source_counter)

    segment_counter: Counter[str] = Counter()
    instance_counter: Counter[str] = Counter()
    for row in segment_rows:
        counts = _parse_object_counts(row.get("object_counts"))
        classes = _split_object_classes(row.get("object_classes"))
        labels = set(counts) | set(classes)
        for label in labels:
            segment_counter[label] += 1
            if label in counts:
                instance_counter[label] += counts[label]
            else:
                # Fallback when only object_classes is present
                instance_counter[label] += 1

    metadata_breakdown: Dict[str, List[Dict[str, Any]]] = {}
    metadata_fields = [f for f in FILTERABLE_METADATA_COLUMNS if f != "object_classes"]
    for field in metadata_fields:
        counter: Counter[str] = Counter()
        for row in segment_rows:
            counter[_metadata_label(row.get(field))] += 1
        metadata_breakdown[field] = [
            {"label": label, "count": count}
            for label, count in counter.most_common(20)
        ]

    uploads_by_day: Counter[str] = Counter()
    for row in segment_rows:
        day = _parse_upload_day(row.get("upload_timestamp"))
        if day:
            uploads_by_day[day] += 1

    public_segments = sum(1 for r in segment_rows if bool(r.get("is_public")))
    reasoning_ok = sum(1 for r in segment_rows if str(r.get("reasoning_content") or "").strip())
    perception_ok = sum(1 for r in segment_rows if bool(r.get("perception_ok")))
    with_objects = sum(1 for r in segment_rows if _split_object_classes(r.get("object_classes")))

    group_stats: Dict[str, dict] = {}
    for row in segment_rows:
        gkey = _group_key(row)
        stream = stream_fields_from_row(row)
        ov = str(row.get("original_video") or row.get("source") or "").strip()
        entry = group_stats.setdefault(
            gkey,
            {
                "group_key": gkey,
                "stream_id": str(stream.get("stream_id") or "").strip() or None,
                "original_video": ov,
                "filename": str(row.get("filename") or ov.rsplit("/", 1)[-1]),
                "segment_rows": 0,
                "sources": set(),
                "chunks": set(),
                "chunk_indices": set(),
                "stream_positions": [],
                "upload_timestamp": row.get("upload_timestamp"),
                "camera_id": _metadata_label(row.get("camera_id")),
                "capture_type": _metadata_label(row.get("capture_type")),
                "location": _metadata_label(row.get("location")),
                "is_public": bool(row.get("is_public")),
                "ingest_kind": str(stream.get("ingest_kind") or "").strip() or None,
            },
        )
        entry["segment_rows"] += 1
        entry["sources"].add(row_source_key(row))
        if ov:
            entry["chunks"].add(ov)
        if stream.get("chunk_index") is not None:
            try:
                entry["chunk_indices"].add(int(stream["chunk_index"]))
            except (TypeError, ValueError):
                pass
        pos = stream.get("stream_position_sec")
        if pos is not None:
            try:
                entry["stream_positions"].append(float(pos))
            except (TypeError, ValueError):
                pass
        ts = row.get("upload_timestamp")
        if ts is not None and (entry["upload_timestamp"] is None or str(ts) > str(entry["upload_timestamp"])):
            entry["upload_timestamp"] = ts

    recent_videos: List[Dict[str, Any]] = []
    for entry in group_stats.values():
        sources: Set[str] = entry["sources"]
        re_ingest = sum(source_counter[s] - 1 for s in sources if source_counter[s] > 1)
        positions = entry["stream_positions"]
        stream_span_sec = None
        if positions:
            stream_span_sec = round(max(positions) - min(positions), 1)
        chunk_count = len(entry["chunk_indices"]) or len(entry["chunks"])
        recent_videos.append(
            {
                "original_video": entry["original_video"],
                "filename": entry["filename"],
                "stream_id": entry["stream_id"],
                "segment_rows": entry["segment_rows"],
                "indexed_clips": len(sources),
                "chunk_count": chunk_count,
                "re_ingest_rows": re_ingest,
                "stream_span_sec": stream_span_sec,
                "ingest_kind": entry["ingest_kind"],
                "upload_timestamp": _format_timestamp(entry["upload_timestamp"]),
                "camera_id": entry["camera_id"],
                "capture_type": entry["capture_type"],
                "location": entry["location"],
                "is_public": entry["is_public"],
                # Legacy fields for older clients
                "unique_segments": len(sources),
                "expected_segments": int(entry.get("total_segments") or 0),
                "duplicate_rows": re_ingest,
            }
        )

    recent_videos.sort(key=lambda item: item.get("upload_timestamp") or "", reverse=True)

    segment_total = len(segment_rows)
    quality = {
        "reasoning_ok": reasoning_ok,
        "reasoning_ok_pct": round((reasoning_ok / segment_total) * 100, 1) if segment_total else 0.0,
        "perception_ok": perception_ok,
        "perception_ok_pct": round((perception_ok / segment_total) * 100, 1) if segment_total else 0.0,
        "with_object_classes": with_objects,
        "with_object_classes_pct": round((with_objects / segment_total) * 100, 1) if segment_total else 0.0,
    }

    stream_sessions = len({stream_fields_from_row(r).get("stream_id") for r in segment_rows if stream_fields_from_row(r).get("stream_id")})

    return {
        "overview": {
            "total_rows": len(rows),
            "segment_rows": segment_total,
            "other_rows": other_rows,
            "unique_videos": len(parent_videos),
            "fully_indexed_videos": fully_indexed_count,
            "indexed_clips": indexed_clips,
            "re_ingest_rows": re_ingest_rows,
            "re_ingest_clips": re_ingest_clips,
            "stream_sessions": stream_sessions,
            # Legacy aliases
            "duplicate_segment_slots": re_ingest_clips,
            "duplicate_segment_rows": re_ingest_rows,
            "public_segment_rows": public_segments,
            "private_segment_rows": segment_total - public_segments,
        },
        "quality": quality,
        "objects": [
            {
                "label": label,
                "segment_count": segment_counter[label],
                "instance_count": instance_counter[label],
            }
            for label, _ in instance_counter.most_common(25)
        ],
        "metadata": metadata_breakdown,
        "uploads_by_day": [
            {"date": day, "segment_rows": count}
            for day, count in sorted(uploads_by_day.items())
        ],
        "recent_videos": recent_videos[:15],
    }


def segmenter_output_bucket(upload_bucket: str, suffix: str = SEGMENTER_OUTPUT_BUCKET_SUFFIX) -> str:
    return f"{upload_bucket}{suffix}"


def attach_s3_inventory(
    payload: Dict[str, Any],
    *,
    upload_bucket: str,
    segments_bucket: str,
    bucket_counts: Dict[str, int],
    bucket_errors: Dict[str, str],
) -> None:
    """Add S3 MP4 inventory and pipeline alignment hints to a dashboard payload."""
    segmenter_bucket = segmenter_output_bucket(upload_bucket)
    inventory: Dict[str, Any] = {
        "chunks_bucket": upload_bucket,
        "chunks_mp4": bucket_counts.get(upload_bucket),
        "segments_bucket": segments_bucket,
        "segments_mp4": bucket_counts.get(segments_bucket),
        "segmenter_output_bucket": segmenter_bucket,
        "segmenter_output_mp4": bucket_counts.get(segmenter_bucket),
        "errors": bucket_errors or None,
    }
    if segmenter_bucket == segments_bucket:
        inventory.pop("segmenter_output_mp4", None)

    overview = payload.get("overview") or {}
    indexed_clips = int(overview.get("indexed_clips") or overview.get("segment_rows") or 0)
    segment_rows = int(overview.get("segment_rows") or 0)
    re_ingest_rows = int(overview.get("re_ingest_rows") or overview.get("duplicate_segment_rows") or 0)

    segments_s3 = inventory.get("segments_mp4")
    if segmenter_bucket != segments_bucket:
        segments_s3 = inventory.get("segmenter_output_mp4", segments_s3)

    pending_index = None
    indexed_matches = None
    rows_match = None
    if segments_s3 is not None:
        pending_index = max(0, segments_s3 - indexed_clips)
        indexed_matches = segments_s3 == indexed_clips
        rows_match = segments_s3 == segment_rows

    payload["s3_inventory"] = inventory
    payload["pipeline_alignment"] = {
        "segments_s3_mp4": segments_s3,
        "indexed_clips": indexed_clips,
        "segment_rows": segment_rows,
        "pending_index": pending_index,
        "re_ingest_excess": re_ingest_rows,
        "indexed_matches_segments_s3": indexed_matches,
        "rows_match_segments_s3": rows_match,
        "segments_bucket_matches_segmenter": segments_bucket == segmenter_bucket,
        "healthy": (
            indexed_matches is True
            and rows_match is True
            and re_ingest_rows == 0
            and segments_bucket == segmenter_bucket
        )
        if segments_s3 is not None
        else None,
    }


def empty_dashboard_stats() -> Dict[str, Any]:
    """Zeroed dashboard payload when the table is empty or unavailable."""
    return {
        "overview": {
            "total_rows": 0,
            "segment_rows": 0,
            "other_rows": 0,
            "unique_videos": 0,
            "fully_indexed_videos": 0,
            "indexed_clips": 0,
            "re_ingest_rows": 0,
            "re_ingest_clips": 0,
            "stream_sessions": 0,
            "duplicate_segment_slots": 0,
            "duplicate_segment_rows": 0,
            "public_segment_rows": 0,
            "private_segment_rows": 0,
        },
        "quality": {
            "reasoning_ok": 0,
            "reasoning_ok_pct": 0.0,
            "perception_ok": 0,
            "perception_ok_pct": 0.0,
            "with_object_classes": 0,
            "with_object_classes_pct": 0.0,
        },
        "objects": [],
        "metadata": {
            "camera_id": [],
            "capture_type": [],
            "location": [],
        },
        "uploads_by_day": [],
        "recent_videos": [],
    }


def _format_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
