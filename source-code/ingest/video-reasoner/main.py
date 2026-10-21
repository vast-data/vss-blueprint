from opentelemetry import trace
from vast_runtime.vast_event import VastEvent  # type: ignore
from urllib.parse import unquote

from common.models import Settings, VideoReasoningResult
from common.clients import S3Client, CosmosReasoningClient
from common.handler_utils import parse_s3_event, should_process_event, is_detector_handoff, parse_s3_uri
from common.perception import perception_from_detector, EMPTY_PERCEPTION
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
    """Initialize the serverless function"""
    with ctx.tracer.start_as_current_span("Video Reasoner Initialization"):
        settings = Settings.from_ctx_secrets(ctx.secrets)
        ctx.s3_client = S3Client(settings)
        
        ctx.reasoning_client = CosmosReasoningClient(settings)
        ctx.logger.info(
            f"[INIT] Cosmos-Reason3: {settings.cosmos_model} @ {settings.cosmos_url}"
        )
        ctx.segment_index = SegmentIndexChecker(settings) if _has_vdb_config(settings) else None
        
        ctx.settings = settings


def handler(ctx, event: VastEvent):
    """Main handler function for vast serverless runtime"""
    
    with ctx.tracer.start_as_current_span("Video Reasoner Handler") as handler_span:
        try:
            data = event.get_data()
            if isinstance(data, dict) and data.get("status") in ("error", "skipped"):
                reason = data.get("error") or data.get("reason") or "upstream error"
                ctx.logger.warning(f"[SKIP] upstream {data.get('status')}: {reason}")
                return {"status": "skipped", "reason": reason}
            event_type = getattr(event, 'get_type', lambda: 'element_trigger')()
            handler_span.set_attribute("event_type", event_type)
            detector_handoff = is_detector_handoff(data)

            with ctx.tracer.start_as_current_span("Event Parsing") as parse_span:
                if detector_handoff:
                    source = str(data["source"])
                    uri = parse_s3_uri(source)
                    bucket = uri["bucket"]
                    key = uri["key"]
                    event_name = "detector-handoff"
                else:
                    event_info = parse_s3_event(data)
                    bucket = event_info["bucket"]
                    key = event_info["key"]
                    event_name = event_info.get("event_name", "unknown")
                parse_span.set_attributes({
                    "bucket": bucket,
                    "key": key,
                    "event_name": event_name,
                    "detector_handoff": detector_handoff,
                })
                ctx.logger.info(f"[INPUT] s3://{bucket}/{key} | event={event_name} | detector={detector_handoff}")

            with ctx.tracer.start_as_current_span("Event Validation") as validation_span:
                should_process, skip_reason = should_process_event(key, event_name)
                if not should_process:
                    validation_span.set_attributes({"skip_reason": skip_reason})
                    ctx.logger.info(f"[SKIP] {key} | reason={skip_reason}")
                    return {"status": "skipped", "reason": skip_reason}
                
                validation_span.set_attributes({
                    "file_type": "mp4",
                    "supported": True
                })

            source = f"s3://{bucket}/{key}"
            filename = key.split('/')[-1] if '/' in key else key
            if ctx.segment_index and ctx.segment_index.is_indexed(source):
                ctx.logger.info(f"[SKIP] {filename} | already indexed in VastDB")
                return {"status": "skipped", "reason": "Already indexed"}

            with ctx.tracer.start_as_current_span("S3 Download") as download_span:
                video_content = ctx.s3_client.download_file(bucket, key)
                size_mb = len(video_content) / (1024 * 1024)
                ctx.logger.info(f"[DOWNLOAD] {filename} | {size_mb:.2f}MB")
                download_span.set_attributes({
                    "bucket": bucket,
                    "key": key,
                    "file_size": len(video_content)
                })

            with ctx.tracer.start_as_current_span("Metadata Extraction") as metadata_span:
                if detector_handoff:
                    is_public = bool(data.get("is_public", True))
                    allowed_users = data.get("allowed_users", "")
                    tags = data.get("tags", "")
                    upload_timestamp = data.get("upload_timestamp", "")
                    segment_number = int(data.get("segment_number") or 0)
                    total_segments = int(data.get("total_segments") or 1)
                    segment_duration = float(data.get("segment_duration") or 5.0)
                    segment_start_sec = float(data.get("segment_start_sec") or 0.0)
                    segment_end_sec = float(data.get("segment_end_sec") or segment_start_sec + segment_duration)
                    segment_step_sec = float(data.get("segment_step_sec") or 5.0)
                    original_video = data.get("original_video") or filename
                    camera_id = data.get("camera_id", "")
                    capture_type = data.get("capture_type", "")
                    location = data.get("location", "")
                    stream_id = str(data.get("stream_id") or "").strip()
                    chunk_index = data.get("chunk_index")
                    chunk_start_sec_meta = data.get("chunk_start_sec")
                    ingest_kind = str(data.get("ingest_kind") or "upload").strip()
                    scenario = str(data.get("scenario") or ctx.settings.scenario).strip() or ctx.settings.scenario
                    custom_prompt = str(data.get("custom_prompt") or "").strip()
                else:
                    try:
                        head_response = ctx.s3_client.head_object(bucket=bucket, key=key)
                        s3_metadata = head_response.get("Metadata", {})
                        
                        is_public_str = s3_metadata.get("is-public", "true")
                        is_public = is_public_str.lower() == "true"
                        allowed_users = s3_metadata.get("allowed-users", "")
                        tags = s3_metadata.get("tags", "")
                        upload_timestamp = s3_metadata.get("upload-timestamp", "")
                        segment_number_str = s3_metadata.get("segment_number", "0")
                        total_segments_str = s3_metadata.get("total_segments", "1")
                        segment_duration_str = s3_metadata.get("segment_duration", "5.0")
                        segment_start_str = s3_metadata.get("segment_start_sec", "")
                        segment_end_str = s3_metadata.get("segment_end_sec", "")
                        segment_step_str = s3_metadata.get("segment_step_sec", "5.0")
                        original_video = s3_metadata.get("original_video", filename)
                        
                        camera_id = s3_metadata.get("camera-id", "")
                        capture_type = s3_metadata.get("capture-type", "")
                        location = s3_metadata.get("location", "")
                        
                        stream_id = str(s3_metadata.get("stream_id") or "").strip()
                        chunk_index_raw = s3_metadata.get("chunk_index", "")
                        chunk_start_raw = s3_metadata.get("chunk_start_sec", "")
                        ingest_kind = str(s3_metadata.get("ingest_kind") or "upload").strip()
                        try:
                            chunk_index = int(chunk_index_raw) if chunk_index_raw not in ("", None) else None
                        except (TypeError, ValueError):
                            chunk_index = None
                        try:
                            chunk_start_sec_meta = float(chunk_start_raw) if chunk_start_raw not in ("", None) else None
                        except (TypeError, ValueError):
                            chunk_start_sec_meta = None
                        
                        scenario = s3_metadata.get("scenario", "").strip()
                        if not scenario:
                            scenario = ctx.settings.scenario
                        
                        custom_prompt_raw = s3_metadata.get("custom-prompt", "").strip()
                        custom_prompt = unquote(custom_prompt_raw) if custom_prompt_raw else ""
                        
                        segment_number = int(segment_number_str) if segment_number_str else 0
                        total_segments = int(total_segments_str) if total_segments_str else 1
                        segment_duration = float(segment_duration_str) if segment_duration_str else 5.0
                        segment_step_sec = float(segment_step_str) if segment_step_str else 5.0
                        if segment_start_str and segment_end_str:
                            segment_start_sec = float(segment_start_str)
                            segment_end_sec = float(segment_end_str)
                        else:
                            sn = segment_number if segment_number > 0 else 1
                            segment_start_sec = (sn - 1) * segment_step_sec
                            segment_end_sec = segment_start_sec + segment_duration
                    except Exception as e:
                        ctx.logger.warning(f"[METADATA] Extraction failed, using defaults: {e}")
                        is_public = True
                        allowed_users = ""
                        tags = ""
                        upload_timestamp = ""
                        segment_number = 0
                        total_segments = 1
                        segment_duration = 5.0
                        segment_start_sec = 0.0
                        segment_end_sec = 5.0
                        segment_step_sec = 5.0
                        original_video = filename
                        camera_id = ""
                        capture_type = ""
                        location = ""
                        scenario = ctx.settings.scenario
                        custom_prompt = ""
                        stream_id = ""
                        chunk_index = None
                        chunk_start_sec_meta = None
                        ingest_kind = "upload"

                prompt_info = f"custom_prompt=set ({len(custom_prompt)} chars)" if custom_prompt else f"scenario={scenario}"
                ctx.logger.info(f"[METADATA] segment {segment_number}/{total_segments} | camera={camera_id or 'none'} | type={capture_type or 'none'} | area={location or 'none'} | {prompt_info}")
                
                metadata_span.set_attributes({
                    "is_public": str(is_public),
                    "segment_number": segment_number,
                    "total_segments": total_segments,
                    "original_video": original_video,
                    "camera_id": camera_id,
                    "capture_type": capture_type,
                    "location": location,
                    "scenario": scenario,
                    "custom_prompt": "set" if custom_prompt else ""
                })

            with ctx.tracer.start_as_current_span("Object Detection") as perception_span:
                if detector_handoff:
                    perception_result = perception_from_detector(data)
                else:
                    perception_result = dict(EMPTY_PERCEPTION)
                perception_span.set_attributes({
                    "perception_ok": perception_result.get("perception_ok", False),
                    "object_classes": perception_result.get("object_classes", ""),
                    "perception_source": perception_result.get("perception_source", ""),
                })
                ctx.logger.info(
                    f"[DETECTION] ok={perception_result.get('perception_ok')} | "
                    f"classes={perception_result.get('object_classes') or 'none'} | "
                    f"source={perception_result.get('perception_source') or 'none'}"
                )

            object_classes = perception_result.get("object_classes", "")

            with ctx.tracer.start_as_current_span("Video Reasoning Analysis") as reasoning_span:
                prompt_info = f"custom_prompt=set ({len(custom_prompt)} chars)" if custom_prompt else f"scenario={scenario}"
                ctx.logger.info(
                    f"[COSMOS] Starting analysis → {ctx.reasoning_client.settings.cosmos_host} | {prompt_info}"
                )
                
                reasoning_result = ctx.reasoning_client.analyze_video(
                    video_content, 
                    filename, 
                    prompt=custom_prompt if custom_prompt else None,
                    scenario=scenario,
                    object_classes=object_classes or None,
                )
                
                content_length = len(reasoning_result.get("reasoning_content", ""))
                tokens_used = reasoning_result.get("tokens_used", 0)
                cached_prompt_tokens = reasoning_result.get("cached_prompt_tokens", 0)
                processing_time = reasoning_result.get("processing_time", 0)
                model_name = reasoning_result.get("cosmos_model", "")

                reasoning_span.set_attributes({
                    "source": source,
                    "filename": filename,
                    "reasoning_content_length": content_length,
                    "tokens_used": tokens_used,
                    "cached_prompt_tokens": cached_prompt_tokens,
                    "processing_time_seconds": processing_time,
                    "model": model_name,
                    "scenario": scenario,
                    "custom_prompt": "set" if custom_prompt else ""
                })

                cache_part = f" ({cached_prompt_tokens} cached input tokens)" if cached_prompt_tokens else ""
                ctx.logger.info(
                    f"[COSMOS] Complete | {content_length} chars | {tokens_used} tokens{cache_part} | {processing_time:.2f}s"
                )

            object_counts = perception_result.get("object_counts", "{}")

            result = {
                "source": source,
                "filename": filename,
                "reasoning_content": reasoning_result["reasoning_content"],
                "cosmos_model": reasoning_result["cosmos_model"],
                "tokens_used": reasoning_result["tokens_used"],
                "cached_prompt_tokens": reasoning_result.get("cached_prompt_tokens", 0),
                "processing_time": reasoning_result["processing_time"],
                "status": "success",
                "is_public": is_public,
                "allowed_users": allowed_users,
                "tags": tags,
                "upload_timestamp": upload_timestamp,
                "segment_number": segment_number,
                "total_segments": total_segments,
                "segment_duration": segment_duration,
                "segment_start_sec": segment_start_sec,
                "segment_end_sec": segment_end_sec,
                "segment_step_sec": segment_step_sec,
                "original_video": original_video,
                "camera_id": camera_id,
                "capture_type": capture_type,
                "location": location,
                "scenario": scenario,
                "perception_json": perception_result.get("perception_json", ""),
                "object_classes": object_classes,
                "object_counts": object_counts,
                "max_detection_conf": perception_result.get("max_detection_conf", 0.0),
                "perception_ok": perception_result.get("perception_ok", False),
                "perception_source": perception_result.get("perception_source", ""),
                "detection_sidecar_uri": perception_result.get("detection_sidecar_uri", ""),
                "detection_frame_count": perception_result.get("detection_frame_count", 0),
                "detection_count": perception_result.get("detection_count", 0),
                "stream_id": stream_id,
                "chunk_index": chunk_index,
                "chunk_start_sec": chunk_start_sec_meta,
                "ingest_kind": ingest_kind,
            }
            
            ctx.logger.info(f"[COMPLETE] {filename} | segment {segment_number}/{total_segments}")
            if ctx.segment_index:
                ctx.segment_index.mark_indexed(source)
            return result
            
        except TransientError as e:
            seg = locals().get("source") or locals().get("filename") or "unknown"
            handler_span.set_attribute("error", True)
            handler_span.set_attribute("error.message", str(e))
            handler_span.record_exception(e)
            ctx.logger.error(f"Reasoning transient failure on {seg}, raising for pipeline retry: {e}")
            raise
        except Exception as e:
            handler_span.set_attribute("error", True)
            handler_span.set_attribute("error.message", str(e))
            handler_span.record_exception(e)
            ctx.logger.error(f"Reasoning failed: {e}")
            return {"status": "error", "error": str(e)}

