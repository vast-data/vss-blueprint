"""
Configuration management for Vast VSS Blueprint Backend
"""
from pydantic import Field
from pydantic_settings import BaseSettings
from typing import Optional
import yaml
import os


class Settings(BaseSettings):
    """Application settings loaded from environment or secret file"""
    
    # API Settings (defaults only, not required in secret)
    app_name: str = Field(default="Video Reasoning API", description="Application name")
    app_version: str = Field(default="1.0.0", description="Application version")
    debug: bool = Field(default=False, description="Debug mode")
    
    # VastDB Settings
    vdb_endpoint: str = Field(..., description="VastDB endpoint")
    vdb_bucket: str = Field(default="vss-vastdb-bucket", description="VastDB bucket")
    vdb_schema: str = Field(default="vss-schema", description="VastDB schema")
    vdb_collection: str = Field(default="vss-collection", description="VastDB collection")
    vdb_prompts_collection: str = Field(
        default="vss-events-collection",
        description="Table for LLM-generated search prompts and key events",
    )
    vdb_access_key: str = Field(..., description="VastDB access key")
    vdb_secret_key: str = Field(..., description="VastDB secret key")
    
    # S3 Settings
    s3_endpoint: str = Field(..., description="S3 endpoint URL")
    s3_access_key: str = Field(..., description="S3 access key")
    s3_secret_key: str = Field(..., description="S3 secret key")
    s3_upload_bucket: str = Field(default="vss-chunks", description="S3 bucket for video uploads")
    s3_segments_bucket: str = Field(default="vss-chunks-segments", description="S3 bucket for processed video segments")
    s3_region: str = Field(default="us-east-1", description="S3 region")
    s3_use_ssl: bool = False
    
    # NVIDIA NIM Embedding Settings
    embedding_host: str = Field(..., description="NVIDIA NIM embedding host")
    embedding_port: int = Field(default=80, description="NVIDIA NIM embedding port")
    embedding_http_scheme: str = Field(default="http", description="HTTP scheme")
    embedding_model: str = Field(
        default="nvidia/cosmos-embed1",
        description="Embedding model",
    )
    embedding_dimensions: int = Field(default=256, description="Embedding dimensions (256 for Cosmos-Embed1)")
    nvidia_api_key: Optional[str] = Field(default="", description="NVIDIA API key (for cloud)")
    embedding_authorization: str = Field(default="", description="Optional Bearer token for hosted/routed embedding APIs (sent as Authorization when set)")
    embedding_local_nim: bool = Field(default=False, description="True = use local NIM (embedding_host/port), False = NVIDIA Cloud")

    # Visual / multimodal embedding (hybrid search)
    visual_embedding_model: str = Field(
        default="nvidia/cosmos-embed1",
        description="Visual/video embedding model (Cosmos-Embed1 uses segment MP4)",
    )
    visual_embedding_dimensions: int = Field(default=256, description="Visual embedding dimensions")
    visual_embedding_host: str = Field(default="", description="Visual NIM host (defaults to embedding_host)")
    visual_embedding_port: int = Field(default=0, description="Visual NIM port (defaults to embedding_port)")
    visual_embedding_http_scheme: str = Field(default="", description="Visual NIM scheme (defaults to embedding scheme)")
    visual_embedding_local_nim: bool = Field(default=False, description="Use local NIM for visual embeddings")
    hybrid_text_weight: float = Field(default=0.6, ge=0.0, le=1.0, description="Hybrid search weight for text vs visual")
    
    # Cosmos-Reason3 text synthesis
    cosmos_host: str = Field(default="", description="Cosmos-Reason3 host (same as ingest reasoner)")
    cosmos_port: int = Field(default=8001, description="Cosmos-Reason3 port")
    cosmoshttpscheme: str = Field(default="http", description="http or https for Cosmos-Reason3")
    cosmos_authorization: str = Field(default="", description="Optional Bearer token for hosted/routed Cosmos-Reason3 API (sent as Authorization when set)")
    cosmos_model: str = Field(default="nvidia/cosmos3-nano-reasoner", description="Cosmos-Reason3 model id")
    cosmos_temperature: float = Field(default=0.2, description="Sampling temperature for synthesis")
    synthesis_max_tokens: int = Field(default=2000, description="Max tokens per synthesis completion chunk")
    synthesis_timeout_seconds: int = Field(default=120, description="Cosmos synthesis API timeout in seconds")
    synthesis_max_continuations: int = Field(default=4, description="Extra continuation chunks when response hits token limit")

    # Unused. Synthesis uses Cosmos-Reason3.
    llm_model_name: str = Field(default="meta/llama-3.1-8b-instruct", description="Deprecated")
    llm_host: str = Field(default="integrate.api.nvidia.com", description="Deprecated")
    llm_port: int = Field(default=443, description="Deprecated")
    llm_http_scheme: str = Field(default="https", description="Deprecated")
    llm_timeout_seconds: int = Field(default=10, description="Deprecated")
    llm_max_tokens: int = Field(default=1200, description="Deprecated")
    llm_max_continuations: int = Field(default=4, description="Deprecated")
    llm_local_nim: bool = Field(default=False, description="Deprecated")

    # Upload Settings
    max_upload_size_mb: int = Field(default=25, description="Maximum upload size in MB")
    max_concurrent_uploads: int = Field(
        default=10,
        description="Max parallel uploads; extra requests wait in queue (no rejection)"
    )
    # Same extensions the segmenter accepts. It converts them to MP4.
    allowed_video_extensions: list[str] = Field(
        default=[".mp4", ".mov", ".webm", ".avi", ".mkv"],
        description="Allowed video extensions at upload (ingest pipeline converts to MP4 for Cosmos)"
    )
    
    # VAST VMS & Tenant (used for login; not sent from frontend)
    vast_host: str = Field(..., description="VAST management server address (VMS) for user authentication")
    tenant_name: str = Field(default="default", description="Tenant name for user authentication")
    jwt_secret: str = Field(..., description="Secret for signing app JWT tokens (e.g. openssl rand -hex 32)")
    
    # CORS Settings (defaults only, not required in secret)
    cors_origins: list[str] = Field(
        default=["http://localhost:4200"],
        description="Allowed CORS origins"
    )

    # UI display timezone (IANA name, e.g. Asia/Jerusalem for Israel IDT/IST)
    display_timezone: str = Field(
        default="UTC",
        description="IANA timezone for upload timestamps in the UI",
    )
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Global settings instance
_settings: Optional[Settings] = None


def load_settings_from_yaml(yaml_path: str) -> dict:
    """Load settings from YAML file"""
    with open(yaml_path, 'r') as f:
        data = yaml.safe_load(f)
        # Handle nested structure (e.g., videobackendsecret: {...})
        if isinstance(data, dict) and len(data) == 1:
            key = list(data.keys())[0]
            return data[key]
        return data


def cosmos_synthesis_url(settings: Settings) -> str:
    """OpenAI-compatible chat completions URL for Cosmos-Reason3 text synthesis.

    Supports host with a path prefix (e.g. gateway/tenant/model) and omits the port
    when it matches the scheme default (443 https / 80 http)."""
    scheme = (settings.cosmoshttpscheme or "http").rstrip(":/")
    host = (settings.cosmos_host or "").strip().strip("/")
    port = int(settings.cosmos_port)
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


def get_settings() -> Settings:
    """Get or create global settings instance"""
    global _settings
    if _settings is None:
        # Try to load from mounted secret file first
        secret_path = os.getenv("SECRET_PATH", "/etc/secrets/config.yaml")
        if os.path.exists(secret_path):
            print(f"📄 Loading configuration from {secret_path}")
            config_data = load_settings_from_yaml(secret_path)
            _settings = Settings(**config_data)
        else:
            print("📄 Loading configuration from environment variables")
            _settings = Settings()
    return _settings

