"""Split one manual upload into the default chunk size and store each piece."""
from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _load_video_chunker():
    """Docker copies the module into src/. Local runs use source-code/shared/."""
    try:
        from src import video_chunker as mod
        return mod
    except ImportError:
        here = Path(__file__).resolve()
        if len(here.parents) <= 4:
            raise
        shared = here.parents[4] / "shared" / "video_chunker.py"
        spec = importlib.util.spec_from_file_location("video_chunker", shared)
        if spec is None or spec.loader is None:
            raise ImportError(f"video_chunker not found at {shared}") from None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod


_chunker = _load_video_chunker()
DEFAULT_CHUNK_DURATION_SEC = _chunker.DEFAULT_CHUNK_DURATION_SEC
chunk_s3_metadata = _chunker.chunk_s3_metadata
iter_video_chunks = _chunker.iter_video_chunks


def split_and_upload(
    s3_service,
    local_path: str,
    filename: str,
    *,
    user,
    is_public: bool,
    tags: list[str],
    allowed_users: list[str],
    scenario: str = "",
    custom_prompt: Optional[str] = None,
    camera_id: Optional[str] = None,
    capture_type: Optional[str] = None,
    location: Optional[str] = None,
) -> tuple[str, list[str]]:
    """Cut local_path at the default chunk size. Returns (stream_id, object keys).

    A file that already fits in one chunk is uploaded as chunk 0 without a re-cut.
    On failure, objects uploaded earlier in this call are deleted.
    """
    stream_id = str(uuid.uuid4())
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    unique_id = uuid.uuid4().hex[:8]
    chunk_dir = tempfile.mkdtemp(prefix="vss-upload-chunks-")
    keys: list[str] = []
    try:
        produced = 0
        for chunk in iter_video_chunks(local_path, DEFAULT_CHUNK_DURATION_SEC, work_dir=chunk_dir):
            piece_name = _piece_filename(filename, chunk.owns_path)
            extra = chunk_s3_metadata(
                stream_id=stream_id,
                chunk_index=chunk.chunk_index,
                chunk_start_sec=chunk.chunk_start_sec,
                chunk_duration_sec=chunk.chunk_duration_sec,
                capture_interval_sec=DEFAULT_CHUNK_DURATION_SEC,
                ingest_kind="upload_chunk",
                original_filename=filename,
            )
            key = s3_service.put_local_video(
                user,
                chunk.path,
                piece_name,
                is_public=is_public,
                tags=tags,
                allowed_users=allowed_users,
                scenario=scenario,
                custom_prompt=custom_prompt,
                camera_id=camera_id,
                capture_type=capture_type,
                location=location,
                extra_metadata=extra,
                chunk_index=chunk.chunk_index,
                stamp=stamp,
                unique_id=unique_id,
            )
            keys.append(key)
            produced += 1
            if chunk.owns_path:
                try:
                    os.remove(chunk.path)
                except OSError:
                    pass
        if produced == 0:
            raise RuntimeError("No chunks produced from uploaded video")
        return stream_id, keys
    except Exception:
        if keys:
            deleted, errors = s3_service.delete_keys(s3_service.settings.s3_upload_bucket, keys)
            logger.error(
                "Upload chunking failed after %s object(s); deleted %s, errors=%s",
                len(keys),
                deleted,
                errors,
            )
        raise
    finally:
        shutil.rmtree(chunk_dir, ignore_errors=True)


def _piece_filename(filename: str, remuxed: bool) -> str:
    """Extracted pieces are mp4. A file that was not cut keeps its original name."""
    name = filename or "video.mp4"
    if not remuxed:
        return name
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return f"{stem}.mp4"
