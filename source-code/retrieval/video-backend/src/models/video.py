"""
Video models for database and API
"""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class VideoSearchResult(BaseModel):
    """Video search result returned to frontend"""
    filename: str
    source: str
    reasoning_content: str
    is_public: bool
    upload_timestamp: datetime
    duration: float
    segment_number: int
    total_segments: int
    segment_start_sec: float = 0.0
    segment_end_sec: float = 0.0
    original_video: str
    tags: List[str]
    similarity_score: float
    
    # Optional fields for display
    cosmos_model: Optional[str] = None
    tokens_used: Optional[int] = None
    cached_prompt_tokens: Optional[int] = None

    # Stream capture metadata (from video-streaming service)
    camera_id: Optional[str] = None
    capture_type: Optional[str] = None
    location: Optional[str] = None

    perception_json: Optional[str] = None
    object_classes: Optional[str] = None
    object_counts: Optional[str] = None
    max_detection_conf: Optional[float] = None
    perception_ok: Optional[bool] = None
    perception_source: Optional[str] = None
    detection_sidecar_uri: Optional[str] = None
    detection_frame_count: Optional[int] = None
    detection_count: Optional[int] = None
    extra_metadata: Optional[str] = None


class TimelineSegment(BaseModel):
    """One segment slot on a parent video timeline."""
    segment_number: int
    segment_start_sec: float
    segment_end_sec: float
    source: str
    reasoning_content: Optional[str] = None
    object_classes: Optional[str] = None
    object_counts: Optional[str] = None
    perception_ok: Optional[bool] = None
    similarity_score: float = 0.0
    is_search_match: bool = False
    query_highlight: bool = False
    is_best_match: bool = False


class StreamChunkPreview(BaseModel):
    """First segment file of one chunk in a capture."""
    chunk_index: int
    source: str


class ChunkSearchResult(BaseModel):
    """Search hit grouped by uploaded video (original_video)."""
    original_video: str
    filename: str
    chunk_duration_sec: float
    total_segments: int
    similarity_score: float
    best_segment_number: int
    best_match_start_sec: float
    best_match_end_sec: float
    preview_source: str
    reasoning_content: str
    is_public: bool
    upload_timestamp: datetime
    tags: List[str]
    matched_segment_count: int
    query: str
    timeline: List[TimelineSegment]
    camera_id: Optional[str] = None
    capture_type: Optional[str] = None
    location: Optional[str] = None
    cosmos_model: Optional[str] = None
    tokens_used: Optional[int] = None
    cached_prompt_tokens: Optional[int] = None
    stream_id: Optional[str] = None
    chunk_index: Optional[int] = None
    stream_chunk_total: Optional[int] = None
    stream_chunk_indices: Optional[List[int]] = None
    stream_chunk_previews: Optional[List[StreamChunkPreview]] = None
    prev_chunk_index: Optional[int] = None
    next_chunk_index: Optional[int] = None
