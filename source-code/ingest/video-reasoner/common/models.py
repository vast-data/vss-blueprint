import os
import json
from typing import Any, Optional, Dict
from pydantic import BaseModel, computed_field, Field


class Settings(BaseModel):
    """Configuration settings for video reasoner"""
    # S3 settings
    s3accesskey: str
    s3secretkey: str
    s3endpoint: str
    
    # Cosmos-Reason3 (hosted vLLM OpenAI API — base64-encoded segment MP4)
    cosmos_host: str = ""
    cosmos_port: int = 8001
    cosmoshttpscheme: str = "http"
    cosmos_authorization: str = Field(
        default="",
        description="Optional Bearer token for Cosmos API (sent as Authorization header when set)",
    )
    cosmos_model: str = ""
    cosmos_max_tokens: int = Field(default=4000, description="Maximum tokens in response for Cosmos (higher for detailed video analysis)")
    cosmos_temperature: float = Field(default=0.2, description="Sampling temperature for Cosmos")
    
    max_video_size_mb: int = 100
    # Optional VastDB settings for idempotency checks
    vdbendpoint: str = ""
    vdbbucket: str = ""
    vdbschema: str = ""
    vdbaccesskey: str = ""
    vdbsecretkey: str = ""
    vdbcollection: str = ""
    # Scenario for prompt selection
    # Options: surveillance, traffic, live_driving, nhl, sports, retail, warehouse, general
    scenario: str = "general"
    
    @computed_field
    @property
    def cosmos_url(self) -> str:
        """Compute Cosmos API URL (supports host with path prefix, e.g. api.example.com/tenant/model)."""
        scheme = (self.cosmoshttpscheme or "http").rstrip(":/")
        host = (self.cosmos_host or "").strip().strip("/")
        port = int(self.cosmos_port)
        default_port = 443 if scheme == "https" else 80
        if "/" in host:
            base = f"{scheme}://{host}"
            if port != default_port:
                hostname, _, path = host.partition("/")
                base = f"{scheme}://{hostname}:{port}/{path}"
            return f"{base}/v1/chat/completions"
        if port == default_port:
            return f"{scheme}://{host}/v1/chat/completions"
        return f"{scheme}://{host}:{port}/v1/chat/completions"
    
    @classmethod
    def from_ctx_secrets(cls, secrets: Dict[str, str]) -> 'Settings':
        """Load settings from runtime context secrets (uses model defaults for missing optional fields)"""
        raw = secrets["vss2-secret"]
        config = {field: raw[field] for field in cls.__annotations__.keys() if field in raw}
        return cls(**config)


class VideoReasoningResult(BaseModel):
    """Result from Cosmos-Reason3 video analysis"""
    source: str
    filename: str
    reasoning_content: str
    cosmos_model: str = ""
    tokens_used: int
    processing_time: float
    status: str = "success"
    
    # Metadata fields from S3 (passed through pipeline)
    is_public: bool = True  # Default to public (for CLI uploads)
    allowed_users: str | None = None  # Comma-separated
    tags: str | None = None  # Comma-separated
    upload_timestamp: str | None = None
    segment_number: int | None = None
    total_segments: int | None = None
    segment_duration: float | None = None
    segment_start_sec: float | None = None
    segment_end_sec: float | None = None
    original_video: str | None = None

    # Stream capture metadata (from video-streaming service)
    camera_id: str | None = None
    capture_type: str | None = None
    location: str | None = None

