from typing import Any, Dict, List

from pydantic import BaseModel


class Settings(BaseModel):
    """Configuration from DataEngine secret `vss2-secret`."""

    vdbendpoint: str
    vdbbucket: str
    vdbschema: str
    vdbaccesskey: str
    vdbsecretkey: str
    vdbcollection: str = "vss-collection"
    vdbpromptscollection: str = "vss-events-collection"

    cosmos_host: str = ""
    cosmos_port: int = 8001
    cosmoshttpscheme: str = "http"
    cosmos_authorization: str = ""
    cosmos_model: str = ""
    cosmos_max_tokens: int = 6000
    cosmos_temperature: float = 0.2

    suggestions_max_segments: int = 48
    suggestions_search_count: int = 10
    suggestions_events_count: int = 30
    suggestions_max_events_per_video: int = 3
    suggestions_lookback_hours: int = 168

    @classmethod
    def from_ctx_secrets(cls, secrets: Dict[str, Any]) -> "Settings":
        raw = secrets["vss2-secret"]
        config = {field: raw[field] for field in cls.__annotations__.keys() if field in raw}
        return cls(**config)

    @property
    def cosmos_url(self) -> str:
        """Cosmos-Reason3 chat URL. Supports host with a path prefix
        (e.g. gateway/tenant/slot) and omits the port when it matches the
        scheme default (443 https / 80 http)."""
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


class KeyEventSuggestion(BaseModel):
    """LLM output row for dashboard key events."""

    query_text: str
    label: str = ""
    original_video: str = ""
    filename: str = ""
    segment_start_sec: float = 0.0
    segment_end_sec: float = 0.0


class SuggestionsResult(BaseModel):
    """Parsed LLM JSON for one scheduled run."""

    search_prompts: List[str] = []
    key_events: List[KeyEventSuggestion] = []
