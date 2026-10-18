"""Split a finished local video into fixed-duration chunks (ffmpeg stream copy).

Used by manual upload and batch sync. Live capture does not use this module:
it records the next window from a URL instead of cutting a finished file.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_DURATION_SEC = 30.0
MIN_CHUNK_DURATION_SEC = 5.0
MAX_CHUNK_DURATION_SEC = 600.0


@dataclass(frozen=True)
class VideoChunk:
    path: str
    chunk_index: int
    chunk_start_sec: float
    chunk_duration_sec: float
    # False when path is the source file (short videos are not re-cut).
    owns_path: bool = True


def normalize_chunk_duration(raw: Optional[float]) -> float:
    try:
        value = float(raw if raw is not None else DEFAULT_CHUNK_DURATION_SEC)
    except (TypeError, ValueError):
        value = DEFAULT_CHUNK_DURATION_SEC
    return max(MIN_CHUNK_DURATION_SEC, min(MAX_CHUNK_DURATION_SEC, value))


def probe_duration_sec(path: str) -> Optional[float]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120)
        if result.returncode != 0:
            logger.warning("ffprobe failed for %s: %s", path, (result.stderr or "").strip())
            return None
        data = json.loads(result.stdout or "{}")
        duration = float(data.get("format", {}).get("duration", 0))
        return duration if duration > 0 else None
    except (json.JSONDecodeError, ValueError, subprocess.TimeoutExpired) as exc:
        logger.warning("ffprobe error for %s: %s", path, exc)
        return None


def extract_chunk(source_path: str, start_sec: float, duration_sec: float, output_path: str) -> bool:
    duration_sec = max(0.1, float(duration_sec))
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start_sec:.3f}",
        "-t",
        f"{duration_sec:.3f}",
        "-i",
        source_path,
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        output_path,
    ]
    timeout_sec = max(60, int(duration_sec * 4))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout_sec)
        if result.returncode != 0:
            logger.warning(
                "ffmpeg extract failed start=%.3f len=%.3f: %s",
                start_sec,
                duration_sec,
                (result.stderr or "").strip()[-500:],
            )
            return False
        return os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg timed out start=%.3f len=%.3f", start_sec, duration_sec)
        return False


def iter_video_chunks(
    source_path: str,
    chunk_duration_sec: float,
    work_dir: Optional[str] = None,
) -> Iterator[VideoChunk]:
    """Yield chunk files. Caller deletes a chunk path only when owns_path is true."""
    duration = probe_duration_sec(source_path)
    if duration is None:
        raise RuntimeError(f"Could not probe video duration: {source_path}")

    chunk_duration_sec = normalize_chunk_duration(chunk_duration_sec)
    if duration <= chunk_duration_sec + 0.05:
        yield VideoChunk(
            path=source_path,
            chunk_index=0,
            chunk_start_sec=0.0,
            chunk_duration_sec=duration,
            owns_path=False,
        )
        return

    chunk_index = 0
    start = 0.0
    out_dir = work_dir or tempfile.mkdtemp(prefix="vss-chunks-")

    while start < duration - 0.05:
        this_len = min(chunk_duration_sec, duration - start)
        out_path = os.path.join(out_dir, f"chunk_{chunk_index:04d}.mp4")
        if not extract_chunk(source_path, start, this_len, out_path):
            raise RuntimeError(
                f"ffmpeg failed for chunk {chunk_index} (start={start:.3f}s, len={this_len:.3f}s)"
            )
        yield VideoChunk(
            path=out_path,
            chunk_index=chunk_index,
            chunk_start_sec=start,
            chunk_duration_sec=this_len,
            owns_path=True,
        )
        chunk_index += 1
        start += chunk_duration_sec


def estimate_chunk_count(duration_sec: Optional[float], chunk_duration_sec: float) -> int:
    if duration_sec is None or duration_sec <= 0:
        return 1
    import math

    return max(1, math.ceil(duration_sec / normalize_chunk_duration(chunk_duration_sec)))


def chunk_s3_metadata(
    *,
    stream_id: str,
    chunk_index: int,
    chunk_start_sec: float,
    chunk_duration_sec: float,
    capture_interval_sec: float,
    ingest_kind: str,
    original_filename: Optional[str] = None,
) -> dict[str, str]:
    """S3 metadata that couples one piece to a stream. Explore reads stream_id and chunk_index."""
    meta = {
        "stream_id": str(stream_id),
        "chunk_index": str(int(chunk_index)),
        "chunk_start_sec": f"{float(chunk_start_sec):.3f}",
        "chunk_duration_sec": f"{float(chunk_duration_sec):.3f}",
        "capture_interval": f"{float(capture_interval_sec):.3f}",
        "ingest_kind": str(ingest_kind),
    }
    name = (original_filename or "").strip()
    if name:
        meta["original-filename"] = name
    return meta
