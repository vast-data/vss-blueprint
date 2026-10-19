"""
Video management API endpoints
"""
import asyncio
import gzip
import json
import logging
import os
import shutil
import tempfile
from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException, status, Query, UploadFile, File, Form, Request, Response
from fastapi.responses import StreamingResponse
from src.services.auth_service import CurrentUser
from src.services.s3_service import (
    get_s3_service,
    if_none_match_satisfied,
    parse_stream_range,
)
from src.models.video import ChunkSearchResult
from src.services.vastdb_service import get_vastdb_service
from src.config import get_settings
from src.schemas.explore import (
    ExploreResponse,
    VideoDeleteResponse,
    VideoSynthesizeRequest,
    VideoSynthesizeResponse,
)
from src.ingest_metadata import parse_comma_list, truncate_custom_prompt

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/videos", tags=["Videos"])
settings = get_settings()

# Limit concurrent uploads; extra requests wait in queue (no rejection)
_upload_semaphore: Optional[asyncio.Semaphore] = None
_upload_semaphore_init_lock: Optional[asyncio.Lock] = None


async def _get_upload_semaphore() -> asyncio.Semaphore:
    """Return the shared upload semaphore; init once, race-free."""
    global _upload_semaphore, _upload_semaphore_init_lock
    if _upload_semaphore is not None:
        return _upload_semaphore
    if _upload_semaphore_init_lock is None:
        _upload_semaphore_init_lock = asyncio.Lock()
    async with _upload_semaphore_init_lock:
        if _upload_semaphore is None:
            n = get_settings().max_concurrent_uploads
            n = max(1, int(n))  # never 0: Semaphore(0) would block all uploads
            _upload_semaphore = asyncio.Semaphore(n)
            logger.info(f"Upload concurrency: max {n} parallel, rest queued")
    return _upload_semaphore


def _upload_split_file(
    s3_service,
    file,
    user,
    is_public: bool,
    tags: list,
    allowed_users: list,
    scenario: str,
    custom_prompt,
    camera_id,
    capture_type,
    location,
):
    """Spool the upload and split it. Runs in a worker thread."""
    from src.utils.upload_chunks import split_and_upload

    suffix = os.path.splitext(file.filename or "")[1] or ".mp4"
    fd, local_path = tempfile.mkstemp(suffix=suffix[:16], prefix="vss-upload-")
    os.close(fd)
    try:
        file.file.seek(0)
        with open(local_path, "wb") as out:
            shutil.copyfileobj(file.file, out)
        return split_and_upload(
            s3_service,
            local_path,
            file.filename or "video.mp4",
            user=user,
            is_public=is_public,
            tags=tags,
            allowed_users=allowed_users,
            scenario=scenario,
            custom_prompt=custom_prompt,
            camera_id=camera_id,
            capture_type=capture_type,
            location=location,
        )
    finally:
        try:
            os.remove(local_path)
        except OSError:
            pass


@router.post("/upload")
async def upload_video(
    file: UploadFile = File(...),
    is_public: bool = Form(False),
    tags: str = Form(""),
    allowed_users: str = Form(""),
    scenario: str = Form(""),
    custom_prompt: str = Form(""),
    camera_id: str = Form(""),
    capture_type: str = Form(""),
    location: str = Form(""),
    stream_id: str = Form(""),
    chunk_number: Optional[int] = Form(None),
    current_user: CurrentUser = None
):
    """
    Upload video directly through backend (proxied to S3).
    Requires authentication.
    
    Args:
        file: Video file to upload
        is_public: Make video publicly accessible
        tags: Comma-separated tags
        allowed_users: Comma-separated list of allowed users
        scenario: Analysis scenario (e.g. general) - ignored if custom_prompt is set
        custom_prompt: Custom prompt for video reasoning (overrides scenario, max 800 chars)
        camera_id: Camera identifier (optional metadata)
        capture_type: Capture type - traffic, streets, crowds, malls, etc. (optional metadata)
        location: Geographic area/location (optional metadata)
        stream_id: Shared id for chunks of one video (optional). When set with
            chunk_number, the file is stored as that chunk and is not split again.
        chunk_number: 1-based position in that stream (required with stream_id)
        current_user: Current authenticated user

    Returns:
        Upload confirmation. A file without stream fields is split at the default
        chunk size (30 seconds). A file that already fits is stored as chunk 0.
    """
    logger.info(f"Upload request from {current_user.username}: filename='{file.filename}', is_public={is_public}")

    # Validate before taking a slot (invalid requests don't queue or consume capacity)
    file_ext = file.filename.lower().split('.')[-1] if '.' in file.filename else ''
    if f".{file_ext}" not in settings.allowed_video_extensions:
        logger.warning(f"Invalid file extension: {file_ext}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file extension. Allowed: {', '.join(settings.allowed_video_extensions)}"
        )
    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)
    max_size = settings.max_upload_size_mb * 1024 * 1024
    if file_size > max_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Max size: {settings.max_upload_size_mb}MB"
        )
    try:
        from src.utils.stream_upload import stream_s3_metadata
        stream_meta = stream_s3_metadata(stream_id, chunk_number)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    # Queue: take a slot (wait if at capacity); always release in finally
    sem = await _get_upload_semaphore()
    await sem.acquire()
    try:
        s3_service = get_s3_service()
        tags_list = parse_comma_list(tags)
        allowed_users_list = parse_comma_list(allowed_users)
        scenario_value = scenario.strip() if scenario else ""

        camera_id_value = camera_id.strip() if camera_id else None
        capture_type_value = capture_type.strip() if capture_type else None
        location_value = location.strip() if location else None
        custom_prompt_value = truncate_custom_prompt(custom_prompt)

        if stream_meta:
            object_key = await s3_service.upload_file(
                file=file,
                user=current_user,
                is_public=is_public,
                tags=tags_list,
                allowed_users=allowed_users_list,
                scenario=scenario_value,
                custom_prompt=custom_prompt_value,
                camera_id=camera_id_value,
                capture_type=capture_type_value,
                location=location_value,
                extra_metadata=stream_meta,
            )
            logger.info(f"Video uploaded: {object_key}")
            return {
                "success": True,
                "object_key": object_key,
                "stream_id": stream_meta.get("stream_id") or None,
                "chunk_number": chunk_number,
                "chunk_count": 1,
                "message": "Video uploaded successfully and will be processed shortly"
            }

        stream_id_value, object_keys = await asyncio.to_thread(
            _upload_split_file,
            s3_service,
            file,
            current_user,
            is_public,
            tags_list,
            allowed_users_list,
            scenario_value,
            custom_prompt_value,
            camera_id_value,
            capture_type_value,
            location_value,
        )
        logger.info("Video uploaded as %s chunk(s): %s", len(object_keys), object_keys[0])
        return {
            "success": True,
            "object_key": object_keys[0],
            "object_keys": object_keys,
            "stream_id": stream_id_value,
            "chunk_number": 1,
            "chunk_count": len(object_keys),
            "message": f"Video uploaded as {len(object_keys)} chunk(s) and will be processed shortly",
        }

    except HTTPException:
        raise
    except RuntimeError as exc:
        logger.error("Failed to split upload: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not split the video into chunks",
        )
    except Exception as e:
        logger.error(f"Failed to upload video: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload video: {str(e)}"
        )
    finally:
        try:
            sem.release()
        except Exception as e:
            logger.exception("Upload semaphore release failed; slot may be leaked: %s", e)


@router.get("/stream")
async def stream_video(
    request: Request,
    source: str = Query(..., description="S3 source URL (s3://bucket/key)"),
    token: str = Query(..., description="JWT authentication token")
):
    """
    Stream video content proxied through backend (bytes are not re-encoded; same as S3 object).

    Supports HTTP Range (206) for seeking, ETag + If-None-Match (304), and S3 Content-Type.
    Quality matches the stored object; tune ingest encoding for fewer artifacts.
    The token must be in the URL because HTML5 <video> cannot send custom headers.
    """
    from src.services.auth_service import get_current_user_from_token
    
    # Validate token and get user
    try:
        current_user = await get_current_user_from_token(token)
        logger.info(f"Stream request from {current_user.username}: source='{source}'")
    except Exception as e:
        logger.error(f"Authentication failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )
    
    try:
        # Validate S3 URL format
        if not source.startswith('s3://'):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid S3 source URL format"
            )
        
        # Parse S3 URL: s3://bucket/key
        s3_path = source[5:]  # Remove 's3://'
        parts = s3_path.split('/', 1)
        
        if len(parts) != 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid S3 URL format. Expected: s3://bucket/key"
            )
        
        bucket, key = parts
        
        s3_service = get_s3_service()
        meta = s3_service.head_object_for_stream(bucket, key)
        total_size = meta.content_length
        range_header = request.headers.get("range")
        spec = parse_stream_range(range_header, total_size)
        filename = key.split("/")[-1]

        base_headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": f'inline; filename="{filename}"',
        }
        if meta.etag:
            base_headers["ETag"] = meta.etag

        if spec == "full" and if_none_match_satisfied(
            request.headers.get("if-none-match"), meta.etag
        ):
            return Response(
                status_code=status.HTTP_304_NOT_MODIFIED,
                headers={k: v for k, v in base_headers.items() if k != "Content-Disposition"},
            )

        if spec == "unsatisfiable":
            return Response(
                status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                headers={"Content-Range": f"bytes */{total_size}"},
            )

        if isinstance(spec, tuple):
            start, end = spec
            content_length = end - start + 1
            video_stream = s3_service.stream_video_range(bucket, key, start, end)
            logger.info(
                f"Partial stream for {current_user.username}: s3://{bucket}/{key} "
                f"bytes {start}-{end}/{total_size}"
            )
            partial_headers = {
                **base_headers,
                "Content-Range": f"bytes {start}-{end}/{total_size}",
                "Content-Length": str(content_length),
            }
            return StreamingResponse(
                video_stream,
                status_code=status.HTTP_206_PARTIAL_CONTENT,
                media_type=meta.content_type,
                headers=partial_headers,
            )

        video_stream = s3_service.stream_video(bucket, key)
        logger.info(f"Streaming video for {current_user.username}: s3://{bucket}/{key}")
        full_headers = {**base_headers, "Content-Length": str(total_size)}
        return StreamingResponse(
            video_stream,
            media_type=meta.content_type,
            headers=full_headers,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to stream video: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to stream video: {str(e)}"
        )


@router.get("/playback-url")
async def get_playback_presigned_url(
    source: str = Query(..., description="S3 source URL (s3://bucket/key)"),
    token: str = Query(..., description="JWT authentication token"),
    expires_in: int = Query(3600, ge=60, le=86400, description="Presigned URL lifetime (seconds)"),
):
    """
    Return a time-limited S3 presigned GET URL for the same object as /stream.

    Bytes are identical to the proxied stream; use this only if your bucket CORS
    allows GET from your frontend origin (required for <video src>). Otherwise keep
    using /videos/stream (now with HTTP Range support).
    """
    from src.services.auth_service import get_current_user_from_token

    try:
        current_user = await get_current_user_from_token(token)
    except Exception as e:
        logger.error(f"Authentication failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    try:
        if not source.startswith("s3://"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid S3 source URL format",
            )
        s3_path = source[5:]
        parts = s3_path.split("/", 1)
        if len(parts) != 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid S3 URL format. Expected: s3://bucket/key",
            )
        bucket, key = parts
        s3_service = get_s3_service()
        url = s3_service.generate_download_url(bucket, key, expires_in=expires_in)
        logger.info(f"Presigned playback URL for {current_user.username}: s3://{bucket}/{key}")
        return {"url": url, "expires_in": expires_in}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to generate playback URL: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate playback URL: {str(e)}",
        )


CHUNK_SUMMARIZE_SYSTEM_PROMPT = """You are a video analyst summarizing ONE parent video chunk from segment evidence only.

Output markdown with:
**Overview** — 2–4 factual sentences covering the full clip in chronological order.

**Timeline** — up to 8 bullets: `start–end sec` — what happened (use segment timestamps from evidence).

Rules:
- Use only facts from the provided segment evidence.
- Do not invent people, actions, or times outside segment ranges.
- No generic filler (urban setting, daylight, clear skies).
- Stay under ~250 words unless the user question requires more detail."""


@router.delete("", response_model=VideoDeleteResponse)
async def delete_video(
    original_video: str = Query(..., min_length=1, description="Parent upload S3 URI"),
    current_user: CurrentUser = None,
):
    """
    Delete one explore video: source object, segments, detection sidecars,
    collection rows, and prompt/event rows.

    Only the uploader (allowed_users, or the S3 key prefix) can delete it.
    """
    from src.utils.row_cache import clear_read_caches
    from src.utils.video_delete import (
        allowed_delete_buckets,
        plan_object_deletes,
        user_can_delete_video,
    )

    parent = original_video.strip()
    logger.info("Delete request from %s: video='%s'", current_user.username, parent)
    vastdb = get_vastdb_service()
    try:
        rows = vastdb.rows_for_video_delete(parent)
    except Exception as exc:
        logger.error("Failed to load rows for delete %s: %s", parent, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load video rows: {exc}",
        )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video not found",
        )
    if not user_can_delete_video(current_user.username, parent, rows):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete videos you uploaded",
        )

    buckets = allowed_delete_buckets(settings.s3_upload_bucket, settings.s3_segments_bucket)
    keys, prefixes, skipped = plan_object_deletes(parent, rows, buckets)
    s3 = get_s3_service()
    object_errors = list(skipped)
    for bucket, bucket_prefixes in prefixes.items():
        for prefix in bucket_prefixes:
            try:
                for key in s3.list_keys(bucket, prefix):
                    keys.setdefault(bucket, set()).add(key)
            except Exception as exc:
                logger.warning("Failed to list s3://%s/%s: %s", bucket, prefix, exc)
                object_errors.append(f"s3://{bucket}/{prefix}: {exc}")

    try:
        prompts_deleted = vastdb.delete_rows_by_column(
            settings.vdb_prompts_collection, "original_video", parent
        )
    except Exception as exc:
        logger.error("Failed to delete prompt rows for %s: %s", parent, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete prompt rows: {exc}",
        )
    try:
        segments_deleted = vastdb.delete_rows_by_column(
            settings.vdb_collection, "original_video", parent
        )
    except Exception as exc:
        logger.error("Failed to delete video rows for %s: %s", parent, exc)
        clear_read_caches()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete index rows: {exc}",
        )

    objects_deleted = 0
    for bucket, bucket_keys in keys.items():
        deleted, errors = s3.delete_keys(bucket, bucket_keys)
        objects_deleted += deleted
        object_errors.extend(errors)

    clear_read_caches()
    logger.info(
        "Deleted %s: segments=%s prompts=%s objects=%s errors=%s",
        parent,
        segments_deleted,
        prompts_deleted,
        objects_deleted,
        len(object_errors),
    )
    return VideoDeleteResponse(
        original_video=parent,
        segments_deleted=segments_deleted,
        prompts_deleted=prompts_deleted,
        objects_deleted=objects_deleted,
        object_errors=object_errors,
    )


@router.get("/chunk", response_model=ChunkSearchResult)
async def get_stream_chunk(
    current_user: CurrentUser,
    stream_id: str = Query(..., min_length=1),
    chunk_index: int = Query(..., ge=0),
    scope: str = Query(default="all", pattern="^(all|mine|public)$"),
):
    """Return one stream chunk card so Explore can open the previous or next piece."""
    logger.info(
        "Stream chunk request from %s: stream_id=%s chunk_index=%s scope=%s",
        current_user.username,
        stream_id,
        chunk_index,
        scope,
    )
    try:
        chunk = get_vastdb_service().get_stream_chunk(
            current_user, stream_id, chunk_index, scope=scope
        )
    except Exception as exc:
        logger.error("Failed to load stream chunk %s #%s: %s", stream_id, chunk_index, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load stream chunk: {exc}",
        )
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stream chunk not found")
    return chunk


@router.get("/explore", response_model=ExploreResponse)
async def explore_videos(
    current_user: CurrentUser,
    scope: str = Query(default="all", pattern="^(all|mine|public)$"),
    date: Optional[str] = Query(
        default=None,
        description="Filter chunks by upload date (YYYY-MM-DD)",
    ),
    location: Optional[str] = Query(
        default=None,
        description="Filter chunks by upload metadata location label",
    ),
    indexed: str = Query(
        default="complete",
        pattern="^(complete|partial)$",
        description="complete = every segment indexed; partial = missing segments",
    ),
    limit: int = Query(default=48, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Browse indexed video chunks by upload date without a search query."""
    logger.info(
        "Explore request from %s: scope=%s date=%s indexed=%s limit=%s offset=%s",
        current_user.username,
        scope,
        date,
        indexed,
        limit,
        offset,
    )
    vastdb = get_vastdb_service()
    payload = vastdb.list_explore_chunks(
        current_user,
        scope=scope,
        date=date,
        location=location,
        indexed=indexed,
        limit=limit,
        offset=offset,
    )
    return ExploreResponse(**payload)


@router.post("/synthesize", response_model=VideoSynthesizeResponse)
async def synthesize_video_chunk(
    body: VideoSynthesizeRequest,
    current_user: CurrentUser,
):
    """On-demand LLM synthesis over all segments for one parent video (no VastDB write)."""
    from datetime import datetime, timezone

    from src.services.llm_service import get_llm_service

    logger.info(
        "Synthesize request from %s: video='%s'",
        current_user.username,
        body.original_video,
    )
    vastdb = get_vastdb_service()
    segments = vastdb.list_segments_for_video(body.original_video, current_user)
    if not segments:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No accessible segments found for video: {body.original_video}",
        )

    capped = segments[: body.max_segments]
    top_results = []
    for seg in capped:
        top_results.append({
            "summary": seg.get("reasoning_content") or "",
            "reasoning_content": seg.get("reasoning_content") or "",
            "original_video": body.original_video,
            "segment_number": seg.get("segment_number"),
            "segment_start_sec": seg.get("segment_start_sec"),
            "segment_end_sec": seg.get("segment_end_sec"),
            "similarity_score": 1.0,
        })

    llm = get_llm_service()
    synthesis = llm.synthesize_search_results(
        query=body.question,
        top_results=top_results,
        custom_system_prompt=body.system_prompt or CHUNK_SUMMARIZE_SYSTEM_PROMPT,
    )

    return VideoSynthesizeResponse(
        original_video=body.original_video,
        segment_count=len(segments),
        segments_used=len(capped),
        answer=synthesis.get("response", ""),
        llm_synthesis=synthesis,
        generated_at=datetime.now(timezone.utc),
    )


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"Invalid S3 URI: {uri}")
    rest = uri[5:]
    bucket, _, key = rest.partition("/")
    if not bucket or not key:
        raise ValueError(f"Invalid S3 URI: {uri}")
    return bucket, key


@router.get("/detections")
async def get_video_detections(
    source: str = Query(..., description="Segment clip S3 URI (source)"),
    current_user: CurrentUser = None,
) -> Dict[str, Any]:
    """Load YOLO detection sidecar JSON for a segment (bbox overlay)."""
    logger.info("Detections request from %s: source=%s", current_user.username, source)
    vastdb_service = get_vastdb_service()
    segment = vastdb_service.get_video_by_source(source, current_user)
    if not segment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Segment not found")

    sidecar_uri = (segment.detection_sidecar_uri or "").strip()
    if not sidecar_uri:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No detection sidecar for segment")

    try:
        bucket, key = _parse_s3_uri(sidecar_uri)
        raw = get_s3_service().get_object_bytes(bucket, key)
        if key.lower().endswith(".gz"):
            raw = gzip.decompress(raw)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Sidecar JSON must be an object")
        return payload
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to load detections for %s: %s", source, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load detections: {exc}",
        )


@router.get("/metadata")
async def get_video_metadata(
    source: str = Query(..., description="S3 source URL"),
    current_user: CurrentUser = None
):
    """
    Get metadata for a specific video segment
    
    Args:
        source: S3 source URL
        current_user: Current authenticated user
        
    Returns:
        Video segment metadata (reasoning, tags, etc.)
    """
    logger.info(f"Metadata request from {current_user.username}: source='{source}'")
    
    try:
        vastdb_service = get_vastdb_service()
        video = vastdb_service.get_video_by_source(source, current_user)
        
        if not video:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Video not found or you don't have permission to access it"
            )
        
        return video.dict()
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get video metadata: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get video metadata: {str(e)}"
        )

