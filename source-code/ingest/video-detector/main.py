import logging
from typing import Any, Dict

from vast_runtime.vast_event import VastEvent  # type: ignore

from common.clients import (
    S3Client,
    YoloInferClient,
    build_sidecar_payload,
    extract_s3_metadata,
    normalize_yolo_response,
    sidecar_key_for_segment,
    write_sidecar_gzip,
)
from common.handler_utils import parse_s3_event, should_process_event
from common.models import Settings
from common.clients import TransientError
from common.segment_index import SegmentIndexChecker


def _has_vdb_config(settings: Settings) -> bool:
    return bool(
        settings.vdbendpoint
        and settings.vdbbucket
        and settings.vdbschema
        and settings.vdbaccesskey
        and settings.vdbsecretkey
        and settings.vdbcollection
    )


def init(ctx):
    with ctx.tracer.start_as_current_span("Video Detector Initialization"):
        settings = Settings.from_ctx_secrets(ctx.secrets)
        ctx.settings = settings
        ctx.s3_client = S3Client(settings)
        ctx.yolo_client = YoloInferClient(settings)
        ctx.segment_index = SegmentIndexChecker(settings) if _has_vdb_config(settings) else None
        ctx.logger.info(
            "[INIT] YOLO infer @ %s:%s model=%s",
            settings.yolo_infer_host,
            settings.yolo_infer_port,
            settings.yolo_model,
        )


def handler(ctx, event: VastEvent):
    with ctx.tracer.start_as_current_span("Video Detector Handler") as handler_span:
        try:
            data = event.get_data()
            event_info = parse_s3_event(data)
            bucket = event_info["bucket"]
            key = event_info["key"]
            event_name = event_info.get("event_name", "unknown")

            should_process, skip_reason = should_process_event(key, event_name)
            if not should_process:
                ctx.logger.info("[SKIP] %s | reason=%s", key, skip_reason)
                return {"status": "skipped", "reason": skip_reason}

            source = f"s3://{bucket}/{key}"
            filename = key.split("/")[-1] if "/" in key else key
            if ctx.segment_index and ctx.segment_index.is_indexed(source):
                ctx.logger.info("[SKIP] %s | already indexed", filename)
                return {"status": "skipped", "reason": "Already indexed"}

            head = ctx.s3_client.head_object(bucket, key)
            meta = extract_s3_metadata(head, default_scenario="general")
            if not meta.get("original_video"):
                meta["original_video"] = filename

            video_content = ctx.s3_client.download_bytes(bucket, key)
            ctx.logger.info(
                "[DOWNLOAD] %s | %.2fMB",
                filename,
                len(video_content) / (1024 * 1024),
            )
            include_frames = bool(ctx.settings.detection_store_frames)
            yolo_raw = ctx.yolo_client.infer(
                video_content,
                filename,
                include_frames=include_frames,
            )
            normalized = normalize_yolo_response(yolo_raw)

            sidecar_uri = ""
            if include_frames and normalized.get("frames"):
                sidecar_key = sidecar_key_for_segment(key, ctx.settings.detection_sidecar_prefix)
                sidecar_payload = build_sidecar_payload(
                    source,
                    yolo_raw,
                    normalized,
                    float(meta.get("segment_duration") or 5.0),
                )
                sidecar_uri = write_sidecar_gzip(ctx.s3_client, bucket, sidecar_key, sidecar_payload)

            result: Dict[str, Any] = {
                "source": source,
                "filename": filename,
                "status": "success",
                "detector_status": "success",
                **meta,
                "perception_json": normalized.get("perception_json", ""),
                "object_classes": normalized.get("object_classes", ""),
                "object_counts": normalized.get("object_counts", "{}"),
                "max_detection_conf": normalized.get("max_detection_conf", 0.0),
                "perception_ok": normalized.get("perception_ok", False),
                "perception_source": normalized.get("perception_source", "yolo11_coco"),
                "detection_sidecar_uri": sidecar_uri,
                "detection_frame_count": normalized.get("detection_frame_count", 0),
                "detection_count": normalized.get("detection_count", 0),
            }

            ctx.logger.info(
                "[DETECTOR] %s | classes=%s | sidecar=%s",
                filename,
                result.get("object_classes") or "none",
                "yes" if sidecar_uri else "no",
            )
            return result

        except TransientError as exc:
            seg = locals().get("source") or locals().get("filename") or "unknown"
            handler_span.record_exception(exc)
            ctx.logger.error("[DETECTOR] transient failure on %s, raising for pipeline retry: %s", seg, exc)
            raise
        except Exception as exc:
            handler_span.record_exception(exc)
            ctx.logger.error("[DETECTOR] failed: %s", exc)
            return {"status": "error", "error": str(exc)}
