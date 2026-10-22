"""
Agent-oriented tool API over VastDB + hybrid search.
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.schemas.search import VideoSearchRequest, VideoSearchResponse
from src.schemas.explore import ExploreResponse, VideoSynthesizeRequest, VideoSynthesizeResponse
from src.services.auth_service import CurrentUser
from src.services.vastdb_service import get_vastdb_service
from src.api.v1 import search as search_api
from src.api.v1 import videos as videos_api

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tools", tags=["Agent Tools"])


class SegmentListResponse(BaseModel):
    original_video: str
    total: int
    segments: List[Dict[str, Any]]


class SegmentDetailResponse(BaseModel):
    segment: Dict[str, Any]


@router.get("/detections")
async def tool_detections(
    source: str = Query(..., description="Segment clip S3 URI (source)"),
    current_user: CurrentUser = None,
):
    """Fetch YOLO bbox sidecar for a segment (agent tool: get_detections)."""
    return await videos_api.get_video_detections(source=source, current_user=current_user)


@router.get("/segments", response_model=SegmentListResponse)
async def list_segments(
    original_video: str = Query(..., description="Parent video S3 URI (original_video)"),
    current_user: CurrentUser = None,
):
    """List all segment rows for a parent video (agent tool: list_segments)."""
    vastdb = get_vastdb_service()
    segments = vastdb.list_segments_for_video(original_video, current_user)
    return SegmentListResponse(
        original_video=original_video,
        total=len(segments),
        segments=segments,
    )


@router.get("/segment", response_model=SegmentDetailResponse)
async def get_segment(
    source: str = Query(..., description="Segment clip S3 URI (source)"),
    current_user: CurrentUser = None,
):
    """Fetch one segment row by source URI (agent tool: get_segment_json)."""
    vastdb = get_vastdb_service()
    result = vastdb.get_video_by_source(source, current_user)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Segment not found")
    return SegmentDetailResponse(segment=result.model_dump())


@router.post("/search", response_model=VideoSearchResponse)
async def tool_search(
    request: VideoSearchRequest,
    current_user: CurrentUser = None,
):
    """Hybrid/text/visual search (agent tool: search_hybrid)."""
    return await search_api.search_videos(request, current_user)


@router.get("/explore", response_model=ExploreResponse)
async def tool_explore(
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
    camera_id: Optional[str] = Query(
        default=None,
        description="Filter chunks by upload metadata camera id",
    ),
    limit: int = Query(default=48, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Browse indexed videos by upload date without a query (agent tool: explore_timeline)."""
    return await videos_api.explore_videos(
        current_user=current_user,
        scope=scope,
        date=date,
        location=location,
        camera_id=camera_id,
        limit=limit,
        offset=offset,
    )


@router.post("/synthesize", response_model=VideoSynthesizeResponse)
async def tool_synthesize(
    body: VideoSynthesizeRequest,
    current_user: CurrentUser,
):
    """On-demand LLM synthesis over all segments for one parent video (agent tool: synthesize_video)."""
    return await videos_api.synthesize_video_chunk(body, current_user)
