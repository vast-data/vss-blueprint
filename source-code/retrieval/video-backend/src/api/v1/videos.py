"""
Video management API endpoints
"""
import asyncio
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, status, Query, UploadFile, File, Form, Request, Response
from fastapi.responses import StreamingResponse
from src.services.auth_service import CurrentUser
from src.services.s3_service import (
    get_s3_service,
    if_none_match_satisfied,
    parse_stream_range,
)
from src.services.vastdb_service import get_vastdb_service
from src.config import get_settings

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
        current_user: Current authenticated user
        
    Returns:
        Upload confirmation with object key
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

    # Queue: take a slot (wait if at capacity); always release in finally
    sem = await _get_upload_semaphore()
    await sem.acquire()
    try:
        s3_service = get_s3_service()
        tags_list = [t.strip() for t in tags.split(',') if t.strip()] if tags else []
        allowed_users_list = [u.strip() for u in allowed_users.split(',') if u.strip()] if allowed_users else []
        scenario_value = scenario.strip() if scenario else ""

        camera_id_value = camera_id.strip() if camera_id else None
        capture_type_value = capture_type.strip() if capture_type else None
        location_value = location.strip() if location else None
        custom_prompt_value = custom_prompt.strip()[:800] if custom_prompt else None
        
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
            location=location_value
        )

        logger.info(f"Video uploaded: {object_key}")
        return {
            "success": True,
            "object_key": object_key,
            "message": "Video uploaded successfully and will be processed shortly"
        }

    except HTTPException:
        raise
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

