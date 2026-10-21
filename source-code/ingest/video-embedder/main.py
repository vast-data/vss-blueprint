from opentelemetry import trace
from vast_runtime.vast_event import VastEvent  # type: ignore

from common.models import Settings, ReasoningEvent, EmbeddingResult
from common.embedding_client import EmbeddingClient
from common.visual_embedding_client import VisualEmbeddingClient
from common.s3_client import S3Client
from common.handler_utils import parse_reasoning_event, resolve_embed_text, validate_embed_text
from common.cosmos_embed_client import TransientError
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
    with ctx.tracer.start_as_current_span("Reasoning Embedder Initialization"):
        settings = Settings.from_ctx_secrets(ctx.secrets)
        ctx.embedding_client = EmbeddingClient(settings)
        ctx.settings = settings
        ctx.s3_client = S3Client(settings)
        ctx.visual_embedding_client = (
            VisualEmbeddingClient(settings) if settings.visual_embedding_enabled else None
        )
        ctx.segment_index = SegmentIndexChecker(settings) if _has_vdb_config(settings) else None


def handler(ctx, event: VastEvent):
    """Main handler function for vast serverless runtime"""
    
    with ctx.tracer.start_as_current_span("Video Embedder Handler") as handler_span:
        source = ""
        filename = ""
        try:
            data = event.get_data()
            if data.get("status") == "error":
                ctx.logger.warning(f"[SKIP] upstream error: {data.get('error')}")
                return {"status": "skipped", "reason": data.get("error", "upstream error")}

            event_type = getattr(event, 'get_type', lambda: 'element_trigger')()
            handler_span.set_attribute("event_type", event_type)
            
            with ctx.tracer.start_as_current_span("Reasoning Event Parsing") as parse_span:
                reasoning_event = parse_reasoning_event(data)
                
                source = reasoning_event.get("source", "")
                filename = reasoning_event.get("filename", "")

                if ctx.segment_index and source and ctx.segment_index.is_indexed(source):
                    ctx.logger.info(f"[SKIP] {filename} | already indexed in VastDB")
                    return {"status": "skipped", "reason": "Already indexed"}

                reasoning_content = reasoning_event.get("reasoning_content", "")
                text_to_embed = resolve_embed_text(reasoning_content)
                cosmos_model = reasoning_event.get("cosmos_model", "")
                tokens_used = reasoning_event.get("tokens_used", 0)
                cached_prompt_tokens = reasoning_event.get("cached_prompt_tokens", 0)
                processing_time = reasoning_event.get("processing_time", 0.0)
                status = reasoning_event.get("status", "success")
                
                is_public = reasoning_event.get("is_public", True)
                allowed_users = reasoning_event.get("allowed_users", "")
                tags = reasoning_event.get("tags", "")
                upload_timestamp = reasoning_event.get("upload_timestamp", "")
                segment_number = reasoning_event.get("segment_number", 0)
                total_segments = reasoning_event.get("total_segments", 1)
                segment_duration = reasoning_event.get("segment_duration", 5.0)
                segment_start_sec = reasoning_event.get("segment_start_sec")
                segment_end_sec = reasoning_event.get("segment_end_sec")
                segment_step_sec = reasoning_event.get("segment_step_sec")
                original_video = reasoning_event.get("original_video", filename)
                
                camera_id = reasoning_event.get("camera_id", "")
                capture_type = reasoning_event.get("capture_type", "")
                location = reasoning_event.get("location", "")
                scenario = reasoning_event.get("scenario", "")
                perception_json = reasoning_event.get("perception_json", "")
                object_classes = reasoning_event.get("object_classes", "")
                object_counts = reasoning_event.get("object_counts", "{}")
                max_detection_conf = reasoning_event.get("max_detection_conf", 0.0)
                perception_ok = reasoning_event.get("perception_ok", False)
                perception_source = reasoning_event.get("perception_source", "")
                detection_sidecar_uri = reasoning_event.get("detection_sidecar_uri", "")
                detection_frame_count = reasoning_event.get("detection_frame_count", 0)
                detection_count = reasoning_event.get("detection_count", 0)
                stream_id = reasoning_event.get("stream_id", "")
                chunk_index = reasoning_event.get("chunk_index")
                chunk_start_sec_meta = reasoning_event.get("chunk_start_sec")
                ingest_kind = reasoning_event.get("ingest_kind", "upload")
                
                allowed_users_count = len(allowed_users.split(",")) if allowed_users else 0
                
                ctx.logger.info(
                    f"[INPUT] {filename} | segment {segment_number}/{total_segments} | "
                    f"embed_text={len(text_to_embed)} chars"
                )
                
                parse_span.set_attributes({
                    "source": source,
                    "filename": filename,
                    "cosmos_model": cosmos_model,
                    "tokens_used": tokens_used,
                    "cached_prompt_tokens": cached_prompt_tokens,
                    "processing_time": processing_time,
                    "status": status,
                    "reasoning_content_length": len(reasoning_content),
                    "is_public": str(is_public),
                    "allowed_users_count": allowed_users_count,
                    "segment_number": segment_number,
                    "total_segments": total_segments,
                    "tags": tags,
                    "original_video": original_video,
                    "camera_id": camera_id,
                    "capture_type": capture_type,
                    "location": location,
                    "scenario": scenario
                })

            with ctx.tracer.start_as_current_span("Content Validation") as validation_span:
                if not validate_embed_text(reasoning_content):
                    validation_span.set_attributes({"valid": False})
                    ctx.logger.info(f"[SKIP] {filename} | no text to embed")
                    return {"status": "skipped", "reason": "No embed text"}
                
                validation_span.set_attributes({"valid": True})

            with ctx.tracer.start_as_current_span("Embedding Generation") as embed_span:
                ctx.logger.info(f"[EMBED] Generating embedding via {ctx.settings.embeddingmodel}")
                embeddings = ctx.embedding_client.get_embeddings([text_to_embed])
                embedding = embeddings[0] if embeddings else []
                
                if not embedding:
                    raise RuntimeError("Failed to generate embedding - empty response from API")
                
                ctx.logger.info(f"[EMBED] Complete | {len(embedding)} dimensions")
                
                embed_span.set_attributes({
                    "embedding_dimensions": len(embedding),
                    "embedding_model": ctx.settings.embeddingmodel
                })

            visual_embedding: list[float] = []
            visual_embedding_ok = False
            if ctx.visual_embedding_client and source.startswith("s3://"):
                with ctx.tracer.start_as_current_span("Visual Embedding Generation") as visual_span:
                    try:
                        ctx.logger.info(
                            f"[VISUAL_EMBED] Generating visual embedding via "
                            f"{ctx.settings.visual_embedding_model}"
                        )
                        video_bytes = ctx.s3_client.download_from_uri(source)
                        visual_embedding = ctx.visual_embedding_client.embed_segment_video(video_bytes)
                        frames_used = 0  # full MP4 embed (Cosmos-Embed1)
                        visual_embedding_ok = len(visual_embedding) > 0
                        visual_span.set_attributes({
                            "visual_embedding_dimensions": len(visual_embedding),
                            "visual_embedding_model": ctx.settings.visual_embedding_model,
                            "frames_used": frames_used,
                        })
                        ctx.logger.info(f"[VISUAL_EMBED] Complete | {len(visual_embedding)} dimensions")
                    except Exception as visual_error:
                        ctx.logger.warning(f"[VISUAL_EMBED] Failed (continuing with text only): {visual_error}")
                        visual_span.set_attributes({"visual_embedding_error": str(visual_error)})

            result = {
                "source": source,
                "filename": filename,
                "reasoning_content": reasoning_content,
                "embedding": embedding,
                "embedding_model": ctx.settings.embeddingmodel,
                "embedding_dimensions": len(embedding),
                "visual_embedding": visual_embedding,
                "visual_embedding_model": ctx.settings.visual_embedding_model if visual_embedding_ok else "",
                "visual_embedding_dimensions": len(visual_embedding),
                "visual_embedding_ok": visual_embedding_ok,
                "cosmos_model": cosmos_model,
                "tokens_used": tokens_used,
                "cached_prompt_tokens": cached_prompt_tokens,
                "processing_time": processing_time,
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
                "perception_json": perception_json,
                "object_classes": object_classes,
                "object_counts": object_counts,
                "max_detection_conf": max_detection_conf,
                "perception_ok": perception_ok,
                "perception_source": perception_source,
                "detection_sidecar_uri": detection_sidecar_uri,
                "detection_frame_count": detection_frame_count,
                "detection_count": detection_count,
                "stream_id": stream_id,
                "chunk_index": chunk_index,
                "chunk_start_sec": chunk_start_sec_meta,
                "ingest_kind": ingest_kind,
            }
            
            ctx.logger.info(f"[COMPLETE] {filename} | segment {segment_number}/{total_segments} | {len(embedding)} dims | metadata: camera={camera_id or 'none'}, type={capture_type or 'none'}")
            return result
            
        except TransientError as e:
            seg = source or filename or "unknown"
            handler_span.set_attribute("error", True)
            handler_span.set_attribute("error.message", str(e))
            handler_span.record_exception(e)
            ctx.logger.error(f"Embedding transient failure on {seg}, raising for pipeline retry: {e}")
            raise
        except Exception as e:
            handler_span.set_attribute("error", True)
            handler_span.set_attribute("error.message", str(e))
            handler_span.record_exception(e)
            ctx.logger.error(f"Embedding failed: {e}")
            err = {"status": "error", "error": str(e)}
            if source:
                err["source"] = source
            if filename:
                err["filename"] = filename
            return err

