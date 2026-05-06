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
    video_url: str
    is_public: bool
    upload_timestamp: datetime
    duration: float
    segment_number: int
    total_segments: int
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

