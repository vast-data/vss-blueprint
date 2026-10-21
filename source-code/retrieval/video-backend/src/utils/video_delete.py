"""Plan which objects a video delete may remove.

A parent upload is one explore card. Its segments and detection sidecars are
leftovers of that same upload, not of other videos.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Set

_SEGMENT_KEY = re.compile(
    r"^(segments/.+)_segment_\d+_of_\d+\.[A-Za-z0-9]+$"
)


def parse_s3_uri(uri: str) -> tuple[str, str]:
    text = (uri or "").strip()
    if not text.startswith("s3://"):
        raise ValueError(f"Invalid S3 URI: {uri}")
    rest = text[5:]
    bucket, _, key = rest.partition("/")
    if not bucket or not key:
        raise ValueError(f"Invalid S3 URI: {uri}")
    return bucket, key


def normalize_name_list(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if hasattr(value, "tolist") and not isinstance(value, (str, bytes)):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        names = []
        for item in value:
            text = str(item).strip()
            if text:
                names.append(text)
        return names
    return []


def user_owns_object_key(uri: str, username: str) -> bool:
    owner = (username or "").strip()
    if not owner:
        return False
    try:
        _, key = parse_s3_uri(uri)
    except ValueError:
        return False
    return key == owner or key.startswith(owner + "/")


def user_can_delete_video(username: str, original_video: str, rows: Iterable[dict]) -> bool:
    """True when every row is owned by this user, or no owner was recorded.

    Uploads always store the uploader in allowed_users. Streaming captures did not,
    so those rows have an empty list and an object key outside the user prefix.
    """
    found = False
    for row in rows:
        found = True
        allowed = normalize_name_list(row.get("allowed_users"))
        if not allowed:
            continue
        if username in allowed:
            continue
        if user_owns_object_key(original_video, username):
            continue
        return False
    return found


def segment_family_prefix(key: str) -> str | None:
    match = _SEGMENT_KEY.match(key or "")
    if not match:
        return None
    return match.group(1) + "_segment_"


def detection_family_prefix(segment_key: str) -> str | None:
    match = _SEGMENT_KEY.match(segment_key or "")
    if not match:
        return None
    base_name = match.group(1).rsplit("/", 1)[-1]
    return f"detections/{base_name}_segment_"


def allowed_delete_buckets(upload_bucket: str, segments_bucket: str) -> Set[str]:
    names = set()
    for name in (upload_bucket, segments_bucket, f"{upload_bucket}-segments" if upload_bucket else ""):
        text = (name or "").strip()
        if text:
            names.add(text)
    return names


def plan_object_deletes(
    original_video: str,
    rows: Iterable[dict],
    allowed_buckets: Set[str],
) -> tuple[Dict[str, Set[str]], Dict[str, Set[str]], List[str]]:
    """Exact keys, list prefixes, and URIs outside the allowed buckets."""
    keys: Dict[str, Set[str]] = {}
    prefixes: Dict[str, Set[str]] = {}
    skipped: List[str] = []

    def add_uri(uri: str, *, expand_family: bool) -> None:
        text = (uri or "").strip()
        if not text:
            return
        try:
            bucket, key = parse_s3_uri(text)
        except ValueError:
            skipped.append(text)
            return
        if bucket not in allowed_buckets:
            skipped.append(text)
            return
        keys.setdefault(bucket, set()).add(key)
        if not expand_family:
            return
        family = segment_family_prefix(key)
        if family:
            prefixes.setdefault(bucket, set()).add(family)
        detections = detection_family_prefix(key)
        if detections:
            prefixes.setdefault(bucket, set()).add(detections)

    add_uri(original_video, expand_family=False)
    for row in rows:
        add_uri(str(row.get("source") or ""), expand_family=True)
        add_uri(str(row.get("detection_sidecar_uri") or ""), expand_family=False)

    return keys, prefixes, skipped
