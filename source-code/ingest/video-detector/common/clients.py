import base64
import gzip
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

import boto3
import requests

from .object_counts import estimate_unique_object_counts

_CONN_EXC = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)
# Statuses raised as TransientError.
_TRANSIENT_STATUS = (401, 403, 408, 429, 500, 502, 503, 504)


class TransientError(Exception):
    """Error the pipeline retries."""


class S3Client:
    def __init__(self, settings):
        self.settings = settings
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3endpoint,
            aws_access_key_id=settings.s3accesskey,
            aws_secret_access_key=settings.s3secretkey,
            verify=False,
        )

    def head_object(self, bucket: str, key: str) -> Dict[str, Any]:
        return self.client.head_object(Bucket=bucket, Key=key)

    def generate_presigned_url(self, bucket: str, key: str, expires_in: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def download_bytes(self, bucket: str, key: str) -> bytes:
        response = self.client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()

    def upload_bytes(self, content: bytes, bucket: str, key: str) -> bool:
        try:
            self.client.put_object(
                Bucket=bucket,
                Key=key,
                Body=content,
                ContentType="application/gzip",
            )
            return True
        except Exception as exc:
            logging.error("[S3] upload failed s3://%s/%s: %s", bucket, key, exc)
            return False


class YoloInferClient:
    def __init__(self, settings):
        self.settings = settings
        host = (settings.yolo_infer_host or "").strip().rstrip("/")
        port = int(settings.yolo_infer_port or 8022)
        scheme = "http"
        if host.startswith("https://"):
            scheme, host = "https", host[8:]
        elif host.startswith("http://"):
            scheme, host = "http", host[7:]
        elif port == 443:
            scheme = "https"
        host = host.strip("/")
        default_port = 443 if scheme == "https" else 80
        if "/" in host:
            if port != default_port:
                hostname, _, path = host.partition("/")
                self.base_url = f"{scheme}://{hostname}:{port}/{path}"
            else:
                self.base_url = f"{scheme}://{host}"
        elif port == default_port:
            self.base_url = f"{scheme}://{host}"
        else:
            self.base_url = f"{scheme}://{host}:{port}"

    def infer(
        self,
        video_content: bytes,
        filename: str,
        include_frames: bool = True,
    ) -> Dict[str, Any]:
        payload = {
            "video_base64": base64.b64encode(video_content).decode(),
            "filename": filename,
            "include_frames": include_frames,
        }
        headers = {"Content-Type": "application/json"}
        token = (self.settings.detector_authorization or "").strip()
        if token:
            headers["Authorization"] = (
                token if token.lower().startswith("bearer ") else f"Bearer {token}"
            )
        # One connection retry, then TransientError.
        url = f"{self.base_url}/v1/infer"
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=600)
        except _CONN_EXC as exc:
            logging.warning("[RETRY] connection error to %s; retrying once: %s", url, exc)
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=600)
            except _CONN_EXC as exc2:
                raise TransientError(f"connection failed after 1 retry: {exc2}") from exc2
        if resp.status_code in _TRANSIENT_STATUS:
            raise TransientError(f"transient HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            raise TransientError(f"HTTP {resp.status_code}: {resp.text[:300]}") from exc
        return resp.json()


def normalize_yolo_response(raw: Dict[str, Any]) -> Dict[str, Any]:
    perception = raw.get("perception_json") or {}
    if not isinstance(perception, dict):
        perception = {}

    frames = raw.get("frames") or perception.get("frames")

    object_classes = raw.get("object_classes") or perception.get("object_classes") or []
    if isinstance(object_classes, list):
        classes = [str(c).strip().lower() for c in object_classes if str(c).strip()]
    else:
        classes = [p.strip().lower() for p in str(object_classes).split(",") if p.strip()]

    raw_counts = raw.get("object_counts") or perception.get("object_counts") or {}
    if isinstance(raw_counts, str):
        try:
            raw_counts = json.loads(raw_counts)
        except json.JSONDecodeError:
            raw_counts = {}
    if not isinstance(raw_counts, dict):
        raw_counts = {}

    frame_count = int(perception.get("frame_count") or raw.get("frame_count") or 0)
    if frame_count <= 0 and isinstance(frames, list):
        frame_count = len(frames)

    object_counts = estimate_unique_object_counts(
        frames if isinstance(frames, list) else None,
        raw_counts=raw_counts,
        frame_count=frame_count,
    )
    if object_counts and not classes:
        classes = sorted(object_counts.keys())

    source_model = str(perception.get("source") or raw.get("source") or "yolo11_coco")
    summary_doc = {
        "source": source_model,
        "object_classes": classes,
        "object_counts": object_counts,
        "object_counts_mode": "max_per_frame" if (isinstance(frames, list) and frames) else "per_frame_avg",
        "max_detection_conf": float(raw.get("max_detection_conf") or perception.get("max_detection_conf") or 0.0),
        "frame_count": frame_count,
        "detection_count": int(perception.get("detection_count") or raw.get("detection_count") or 0),
    }

    return {
        "perception_ok": bool(raw.get("perception_ok") or raw.get("ok")),
        "object_classes": ",".join(classes),
        "object_counts": json.dumps(object_counts, ensure_ascii=False),
        "max_detection_conf": summary_doc["max_detection_conf"],
        "perception_source": source_model,
        "detection_frame_count": summary_doc["frame_count"],
        "detection_count": summary_doc["detection_count"],
        "perception_json": json.dumps(summary_doc, ensure_ascii=False),
        "frames": frames,
    }


def build_sidecar_payload(
    segment_source: str,
    yolo_raw: Dict[str, Any],
    normalized: Dict[str, Any],
    segment_duration: float,
) -> Dict[str, Any]:
    frames = normalized.get("frames") or []
    frame_count = int(normalized.get("detection_frame_count") or 0)
    fps = (frame_count / segment_duration) if segment_duration > 0 and frame_count > 0 else 30.0

    video_shape: List[int] = []
    enriched_frames = []
    if isinstance(frames, list):
        for frame in frames:
            if not isinstance(frame, dict):
                continue
            idx = int(frame.get("frame_index") or 0)
            shape = frame.get("shape") or []
            if not video_shape and isinstance(shape, list) and len(shape) >= 2:
                video_shape = [int(shape[0]), int(shape[1])]
            enriched_frames.append(
                {
                    "frame_index": idx,
                    "time_sec": round(idx / fps, 4),
                    "shape": shape,
                    "detections": frame.get("detections") or [],
                }
            )

    try:
        object_counts = json.loads(normalized.get("object_counts") or "{}")
    except json.JSONDecodeError:
        object_counts = {}

    return {
        "source": normalized.get("perception_source") or "yolo11_coco",
        "segment_source": segment_source,
        "video_shape": video_shape,
        "fps": round(fps, 3),
        "frame_count": frame_count,
        "detection_count": int(normalized.get("detection_count") or 0),
        "object_classes": [c.strip() for c in (normalized.get("object_classes") or "").split(",") if c.strip()],
        "object_counts": object_counts,
        "max_detection_conf": float(normalized.get("max_detection_conf") or 0.0),
        "frames": enriched_frames,
    }


def write_sidecar_gzip(s3_client: S3Client, bucket: str, key: str, payload: Dict[str, Any]) -> str:
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    ok = s3_client.upload_bytes(body, bucket, key)
    if not ok:
        raise RuntimeError(f"Failed to write detection sidecar s3://{bucket}/{key}")
    return f"s3://{bucket}/{key}"


def sidecar_key_for_segment(segment_key: str, prefix: str) -> str:
    stem = segment_key.rsplit("/", 1)[-1]
    if stem.lower().endswith(".mp4"):
        stem = stem[:-4]
    prefix = (prefix or "detections/").lstrip("/")
    if not prefix.endswith("/"):
        prefix += "/"
    return f"{prefix}{stem}.json.gz"


def extract_s3_metadata(head_response: Dict[str, Any], default_scenario: str) -> Dict[str, Any]:
    s3_metadata = head_response.get("Metadata", {}) or {}
    is_public = str(s3_metadata.get("is-public", "true")).lower() == "true"
    segment_number = int(s3_metadata.get("segment_number") or 0)
    total_segments = int(s3_metadata.get("total_segments") or 1)
    segment_duration = float(s3_metadata.get("segment_duration") or 5.0)
    segment_step_sec = float(s3_metadata.get("segment_step_sec") or 5.0)
    segment_start_str = s3_metadata.get("segment_start_sec", "")
    segment_end_str = s3_metadata.get("segment_end_sec", "")
    if segment_start_str and segment_end_str:
        segment_start_sec = float(segment_start_str)
        segment_end_sec = float(segment_end_str)
    else:
        sn = segment_number if segment_number > 0 else 1
        segment_start_sec = (sn - 1) * segment_step_sec
        segment_end_sec = segment_start_sec + segment_duration

    chunk_index_raw = s3_metadata.get("chunk_index", "")
    chunk_start_raw = s3_metadata.get("chunk_start_sec", "")
    try:
        chunk_index = int(chunk_index_raw) if chunk_index_raw not in ("", None) else None
    except (TypeError, ValueError):
        chunk_index = None
    try:
        chunk_start_sec = float(chunk_start_raw) if chunk_start_raw not in ("", None) else None
    except (TypeError, ValueError):
        chunk_start_sec = None

    scenario = str(s3_metadata.get("scenario") or "").strip() or default_scenario
    custom_prompt_raw = str(s3_metadata.get("custom-prompt") or "").strip()
    custom_prompt = unquote(custom_prompt_raw) if custom_prompt_raw else ""

    return {
        "is_public": is_public,
        "allowed_users": s3_metadata.get("allowed-users", ""),
        "tags": s3_metadata.get("tags", ""),
        "upload_timestamp": s3_metadata.get("upload-timestamp", ""),
        "segment_number": segment_number,
        "total_segments": total_segments,
        "segment_duration": segment_duration,
        "segment_start_sec": segment_start_sec,
        "segment_end_sec": segment_end_sec,
        "segment_step_sec": segment_step_sec,
        "original_video": s3_metadata.get("original_video", ""),
        "camera_id": s3_metadata.get("camera-id", ""),
        "capture_type": s3_metadata.get("capture-type", ""),
        "location": s3_metadata.get("location", ""),
        "scenario": scenario,
        "custom_prompt": custom_prompt,
        "stream_id": str(s3_metadata.get("stream_id") or "").strip(),
        "chunk_index": chunk_index,
        "chunk_start_sec": chunk_start_sec,
        "ingest_kind": str(s3_metadata.get("ingest_kind") or "upload").strip(),
    }
