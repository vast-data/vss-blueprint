"""
Text synthesis for search / explore using Cosmos-Reason3 (text-only chat; no video).
"""
import httpx
import logging
import time
from typing import Any, Dict, List, Optional
from src.utils.stream_index import format_stream_time, stream_fields_from_row
from src.config import get_settings, cosmos_synthesis_url
from src.models.video import ChunkSearchResult

logger = logging.getLogger(__name__)


# Fallback system prompt (only used if frontend doesn't send one)
DEFAULT_SYSTEM_PROMPT = """Role: Video analyst summarizing search results from retrieved clip evidence only.

Output format (markdown, blank line between sections):

**Answer**
2–3 short sentences. Direct, objective reply to the query. No filenames.

**Notable moments**
- **0:25–0:30 (Clip 1)** — One factual sentence (max ~18 words).
- **0:10–0:15 (Clip 3)** — …
Use at most 5 bullets. Merge duplicate observations across clips. Skip generic scene filler.

**Gaps** (omit section if not needed)
One sentence only when evidence is weak or clips disagree.

Rules:
- Use only reasoning text / object_classes from the evidence.
- Reference clips as "Clip N" (numbers from evidence headers). Never paste .mp4 filenames in the body.
- No clip inventory paragraphs ("Clip 1 (file.mp4), Clip 2 …").
- No repeated boilerplate: urban setting, daylight, clear skies, no hazards, bustling street.
- Stay under ~180 words unless the query truly needs more detail.
- Accuracy over style; do not invent people, actions, or times."""


class LLMService:
    """Cosmos-Reason3 text synthesis."""

    def __init__(self):
        self.settings = get_settings()
        if not (self.settings.cosmos_host or "").strip():
            raise ValueError(
                "cosmos_host is not configured — set Cosmos-Reason3 host in backend secret "
                "(same host/port as ingest video-reasoner)."
            )
        self.cosmos_url = cosmos_synthesis_url(self.settings)
        self.cosmos_authorization = (self.settings.cosmos_authorization or "").strip()
        self.model_name = self.settings.cosmos_model
        self.timeout = self.settings.synthesis_timeout_seconds
        self.max_tokens = self.settings.synthesis_max_tokens
        self.max_continuations = self.settings.synthesis_max_continuations
        self.temperature = self.settings.cosmos_temperature
        self.default_prompt = DEFAULT_SYSTEM_PROMPT
        logger.info(
            "[SYNTHESIS] Cosmos-Reason3 %s @ %s (max_tokens=%s, timeout=%ss)",
            self.model_name,
            self.cosmos_url,
            self.max_tokens,
            self.timeout,
        )
    
    def synthesize_search_results(
        self, 
        query: str, 
        top_results: List[Dict],
        custom_system_prompt: Optional[str] = None
    ) -> Dict:
        """Frontend prompt wins. Results are already limited by the search API."""
        start_time = time.time()
        
        top_n = len(top_results)
        
        if top_n == 0:
            return {
                "response": "No video segments found to analyze.",
                "segments_used": 0,
                "segments_analyzed": [],
                "model": self.model_name,
                "tokens_used": 0,
                "processing_time": 0.0,
                "error": None
            }
        
        effective_prompt = custom_system_prompt.strip() if custom_system_prompt and custom_system_prompt.strip() else self.default_prompt
        
        summaries_text = self._format_summaries(top_results[:top_n])
        
        user_message = f"""User Query: {query}

Video Clip Evidence (reasoning text from indexed segments):
{summaries_text}

Write a concise summary using only this evidence. Follow the markdown sections in your system prompt.
Use Clip N labels from the headers above; do not list filenames."""
        
        try:
            response_data = self._call_cosmos_chat(user_message, system_prompt=effective_prompt)
            
            processing_time = time.time() - start_time
            
            segment_names = self._clip_labels(top_results[:top_n])
            
            return {
                "response": response_data.get("content", ""),
                "segments_used": top_n,
                "segments_analyzed": segment_names,
                "model": self.model_name,
                "tokens_used": response_data.get("tokens_used", 0),
                "processing_time": round(processing_time, 2),
                "error": None
            }
            
        except Exception as e:
            processing_time = time.time() - start_time
            error_msg = str(e)
            logger.error("Cosmos synthesis error: %s", error_msg)
            
            # Extract segment names even on error
            segment_names = self._clip_labels(top_results[:top_n])
            
            return {
                "response": f"Failed to generate AI synthesis: {error_msg}",
                "segments_used": top_n,
                "segments_analyzed": segment_names,
                "model": self.model_name,
                "tokens_used": 0,
                "processing_time": round(processing_time, 2),
                "error": error_msg
            }
    
    @staticmethod
    def _format_media_time(seconds: float) -> str:
        """Format seconds as M:SS or H:MM:SS for in-video timeline references."""
        if seconds is None:
            return "?"
        try:
            total = int(float(seconds))
        except (TypeError, ValueError):
            return "?"
        if total < 0:
            return "?"
        minutes, secs = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    @staticmethod
    def _parent_video_label(original_video: str) -> str:
        if original_video.startswith("s3://"):
            return original_video.rsplit("/", 1)[-1]
        return original_video

    @staticmethod
    def chunk_search_result_to_evidence(chunk: ChunkSearchResult) -> Dict[str, Any]:
        """Build LLM evidence from a grouped chunk using reasoning text."""
        segments: List[Dict[str, Any]] = []
        for seg in chunk.timeline:
            reasoning = (seg.reasoning_content or "").strip()
            object_classes = (seg.object_classes or "").strip()
            segments.append({
                "segment_number": seg.segment_number,
                "segment_start_sec": seg.segment_start_sec,
                "segment_end_sec": seg.segment_end_sec,
                "reasoning_content": reasoning or "No reasoning text available.",
                "object_classes": object_classes,
                "is_search_match": seg.is_search_match,
                "is_best_match": seg.is_best_match,
                "query_highlight": seg.query_highlight,
                "similarity_score": seg.similarity_score,
                **stream_fields_from_row({"extra_metadata": getattr(seg, "extra_metadata", None)}),
            })
        return {
            "evidence_type": "chunk",
            "original_video": chunk.original_video,
            "filename": chunk.filename,
            "similarity_score": chunk.similarity_score,
            "total_segments": chunk.total_segments,
            "chunk_duration_sec": chunk.chunk_duration_sec,
            "best_segment_number": chunk.best_segment_number,
            "segment_start_sec": chunk.best_match_start_sec,
            "segment_end_sec": chunk.best_match_end_sec,
            "upload_timestamp": chunk.upload_timestamp,
            "segments": segments,
        }

    @staticmethod
    def _clip_labels(results: List[Dict]) -> List[str]:
        labels = []
        for result in results:
            parent = LLMService._parent_video_label(result.get("original_video", "Unknown"))
            start = LLMService._format_media_time(result.get("segment_start_sec"))
            end = LLMService._format_media_time(result.get("segment_end_sec"))
            labels.append(f"{parent} (best moment {start}–{end})")
        return labels

    def _format_summaries(self, results: List[Dict]) -> str:
        """Format clip timeline evidence for LLM input."""
        formatted = []
        for i, result in enumerate(results, 1):
            if result.get("evidence_type") == "chunk":
                formatted.append(self._format_chunk_evidence(i, result))
                continue
            formatted.append(self._format_legacy_segment_evidence(i, result))
        return "\n\n".join(formatted)

    def _format_chunk_evidence(self, index: int, result: Dict) -> str:
        parent_label = self._parent_video_label(result.get("original_video", "Unknown video"))
        score = result.get("similarity_score", 0)
        best_start = self._format_media_time(result.get("segment_start_sec"))
        best_end = self._format_media_time(result.get("segment_end_sec"))
        duration = self._format_media_time(result.get("chunk_duration_sec"))
        upload_ts = result.get("upload_timestamp")
        if upload_ts is not None and hasattr(upload_ts, "strftime"):
            ts_str = upload_ts.strftime("%Y-%m-%d %H:%M:%S")
        else:
            ts_str = str(upload_ts)[:19].replace("T", " ") if upload_ts else "?"

        header = (
            f"Clip {index}: {parent_label} "
            f"({result.get('total_segments', '?')} segments, {duration} total) "
            f"[best match {best_start}–{best_end}, relevance {score:.1%}] | Uploaded: {ts_str}"
        )
        segment_blocks = []
        for seg in result.get("segments") or []:
            sn = seg.get("segment_number", "?")
            t0 = self._format_media_time(seg.get("segment_start_sec"))
            t1 = self._format_media_time(seg.get("segment_end_sec"))
            flags = []
            if seg.get("is_best_match"):
                flags.append("BEST MATCH")
            elif seg.get("is_search_match"):
                flags.append("search match")
            if seg.get("query_highlight"):
                flags.append("query hit")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            stream = stream_fields_from_row(seg)
            stream_note = ""
            pos = stream.get("stream_position_sec")
            if pos is not None:
                try:
                    stream_note = f" | stream @ {format_stream_time(float(pos))}"
                except (TypeError, ValueError):
                    pass
            elif stream.get("chunk_index") is not None:
                stream_note = f" | chunk {stream.get('chunk_index')}"
            block = [f"  Segment {sn} ({t0}–{t1}){flag_str}{stream_note}:", f"  reasoning: {seg.get('reasoning_content', '')}"]
            object_classes = (seg.get("object_classes") or "").strip()
            if object_classes:
                block.append(f"  object_classes: {object_classes[:800]}")
            segment_blocks.append("\n".join(block))
        return header + "\n" + "\n".join(segment_blocks)

    def _format_legacy_segment_evidence(self, index: int, result: Dict) -> str:
        reasoning = (result.get("reasoning_content") or result.get("summary") or "").strip()
        object_classes = (result.get("object_classes") or "").strip()
        parent_label = self._parent_video_label(result.get("original_video", "Unknown video"))
        segment_num = result.get("segment_number", "?")
        total_segments = result.get("total_segments", "?")
        score = result.get("similarity_score", 0)
        start_sec = result.get("segment_start_sec")
        end_sec = result.get("segment_end_sec")
        if start_sec is not None and end_sec is not None:
            video_time = (
                f"{self._format_media_time(start_sec)}–{self._format_media_time(end_sec)} in video"
            )
        else:
            video_time = "unknown position in video"
        upload_ts = result.get("upload_timestamp")
        if upload_ts is not None and hasattr(upload_ts, "strftime"):
            ts_str = upload_ts.strftime("%Y-%m-%d %H:%M:%S")
        else:
            ts_str = str(upload_ts)[:19].replace("T", " ") if upload_ts else "?"
        header = (
            f"Clip {index}: {parent_label} (segment {segment_num}/{total_segments}, {video_time}) "
            f"[match: {score:.1%}] | Uploaded: {ts_str}"
        )
        body_parts = [f"reasoning: {reasoning or 'No reasoning text available.'}"]
        if object_classes:
            body_parts.append(f"object_classes: {object_classes[:800]}")
        return f"{header}\n" + "\n".join(body_parts)
    
    def _call_cosmos_chat(self, user_message: str, system_prompt: Optional[str] = None) -> Dict:
        """Text-only chat. No video payload."""
        effective_system_prompt = system_prompt if system_prompt else self.default_prompt

        headers = {"Content-Type": "application/json"}
        token = self.cosmos_authorization
        if token:  # only sent for routed/gateway APIs; local vLLM needs no auth header
            headers["Authorization"] = (
                token if token.lower().startswith("bearer ") else f"Bearer {token}"
            )

        messages = [
            {"role": "system", "content": effective_system_prompt},
            {"role": "user", "content": user_message},
        ]

        all_content_parts: List[str] = []
        total_tokens_used = 0
        max_rounds = max(0, int(self.max_continuations)) + 1
        finish_reason = None

        with httpx.Client(timeout=self.timeout) as client:
            for round_idx in range(max_rounds):
                payload = {
                    "model": self.model_name,
                    "messages": messages,
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                    "stream": False,
                }
                response = client.post(self.cosmos_url, json=payload, headers=headers)
                if response.status_code != 200:
                    raise RuntimeError(
                        f"Cosmos synthesis error ({response.status_code}): {response.text[:1000]}"
                    )

                data = response.json()
                choices = data.get("choices", [])
                if not choices:
                    raise RuntimeError("No choices in Cosmos API response")

                first_choice = choices[0]
                content_part = first_choice.get("message", {}).get("content", "") or ""
                finish_reason = first_choice.get("finish_reason")
                total_tokens_used += data.get("usage", {}).get("total_tokens", 0)

                if content_part:
                    all_content_parts.append(content_part)

                hit_token_limit = finish_reason in ("length", "max_tokens")
                if not hit_token_limit:
                    break

                if round_idx >= max_rounds - 1:
                    break

                messages.append({"role": "assistant", "content": content_part})
                messages.append({
                    "role": "user",
                    "content": (
                        "Continue exactly where you stopped. Do not restart or repeat prior lines. "
                        "Return only the remaining continuation."
                    ),
                })

        return {
            "content": "".join(all_content_parts).strip(),
            "tokens_used": total_tokens_used,
        }

# Global LLM service instance
_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    """Get or create global LLM service instance"""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service

