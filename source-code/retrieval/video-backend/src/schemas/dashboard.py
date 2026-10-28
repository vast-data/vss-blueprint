"""Dashboard API schemas."""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CountItem(BaseModel):
    label: str
    count: int


class ObjectStatItem(BaseModel):
    label: str
    segment_count: int
    instance_count: int = 0


class UploadDayItem(BaseModel):
    date: str
    segment_rows: int


class S3Inventory(BaseModel):
    chunks_bucket: str
    chunks_mp4: Optional[int] = None
    segments_bucket: str
    segments_mp4: Optional[int] = None
    segmenter_output_bucket: str
    segmenter_output_mp4: Optional[int] = None
    errors: Optional[Dict[str, str]] = None


class PipelineAlignment(BaseModel):
    segments_s3_mp4: Optional[int] = None
    indexed_clips: int = 0
    segment_rows: int = 0
    pending_index: Optional[int] = None
    re_ingest_excess: int = 0
    indexed_matches_segments_s3: Optional[bool] = None
    rows_match_segments_s3: Optional[bool] = None
    segments_bucket_matches_segmenter: Optional[bool] = None
    healthy: Optional[bool] = None


class DashboardOverview(BaseModel):
    total_rows: int
    segment_rows: int
    other_rows: int
    unique_videos: int
    fully_indexed_videos: int = 0
    indexed_clips: int = 0
    re_ingest_rows: int = 0
    re_ingest_clips: int = 0
    stream_sessions: int = 0
    duplicate_segment_slots: int = 0
    duplicate_segment_rows: int = 0
    public_segment_rows: int
    private_segment_rows: int


class DashboardQuality(BaseModel):
    reasoning_ok: int
    reasoning_ok_pct: float
    perception_ok: int
    perception_ok_pct: float
    with_object_classes: int
    with_object_classes_pct: float


class RecentVideoItem(BaseModel):
    original_video: str
    filename: str
    stream_id: Optional[str] = None
    segment_rows: int
    indexed_clips: int = 0
    chunk_count: int = 0
    re_ingest_rows: int = 0
    stream_span_sec: Optional[float] = None
    ingest_kind: Optional[str] = None
    unique_segments: int = 0
    expected_segments: int = 0
    duplicate_rows: int = 0
    upload_timestamp: Optional[str] = None
    camera_id: str
    capture_type: str
    location: str
    is_public: bool


class DashboardModels(BaseModel):
    reasoner_calls: int = 0
    token_total: int = 0
    token_avg: float = 0.0
    cached_tokens: int = 0


class LiveEndpoint(BaseModel):
    name: str
    label: str
    status: str = Field(description="ready | busy | down | unavailable")
    model: str = ""
    detail: Optional[str] = None
    running: Optional[int] = None
    waiting: Optional[int] = None
    cache_pct: Optional[float] = None
    avg_latency_sec: Optional[float] = None
    gpu_memory_pct: Optional[float] = None
    gpu_util_pct: Optional[float] = None
    ttft_sec: Optional[float] = None
    prefill_sec: Optional[float] = None
    decode_sec: Optional[float] = None
    finished: Optional[int] = None
    cut_off: Optional[int] = None
    failed: Optional[int] = None
    metrics_available: bool = False


class LiveEndpoints(BaseModel):
    probed_at: str
    reasoner: LiveEndpoint
    embed: LiveEndpoint


class DashboardStatsResponse(BaseModel):
    table: str
    table_available: bool = True
    table_message: Optional[str] = None
    generated_at: str
    query_time_ms: float
    scope: str = Field(default="all", description="all | mine | public")
    overview: DashboardOverview
    quality: DashboardQuality
    models: DashboardModels = Field(default_factory=DashboardModels)
    live_endpoints: Optional[LiveEndpoints] = None
    objects: List[ObjectStatItem]
    metadata: Dict[str, List[CountItem]]
    uploads_by_day: List[UploadDayItem]
    recent_videos: List[RecentVideoItem]
    s3_inventory: Optional[S3Inventory] = None
    pipeline_alignment: Optional[PipelineAlignment] = None
