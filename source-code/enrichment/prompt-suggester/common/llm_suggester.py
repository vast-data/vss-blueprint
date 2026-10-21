"""Generate search prompts and key events from sampled segments."""
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from opentelemetry import trace

from .event_dedupe import (
    dedupe_key_events,
    dedupe_prompts,
    grounded_in_reasoning,
    normalize_search_prompt,
)
from .models import KeyEventSuggestion, Settings
from .stream_index import stream_fields_from_row, timeline_sec

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

MAX_QUERY_WORDS = 8

SYSTEM_PROMPT = """You write grounded search phrases for a video index.

Input: SAMPLE SEGMENTS — each line has an index i and that segment's reasoning_content.

For EVERY sample segment, produce exactly one item:
- Rephrase only what that segment's reasoning_content already says.
- Do NOT invent people, objects, colors, brands, streets, actions, or predicted moves
  that are not written in that reasoning_content. Rephrase; do not invent.
- Prefer the single densest phrase for that scene — keep concrete details that appear
  in the reasoning (colors, brands, street names, vehicle type/color, predicted next
  move, notable actions) when they fit in the word limit.
- query_text: max 8 words, one line, no period, no commas.
- label: short Title Case headline (3–8 words), also grounded; may mirror query_text.

No domain bias. No preferred scene shapes. No padding.

Output JSON only (no markdown):
{{"items":[{{"i":0,"query_text":"...","label":"..."}}]}}
Include one object in items for every sample index (0..n-1).
"""


def _video_meta(row: dict) -> Tuple[str, str]:
    ov = str(row.get("original_video") or row.get("source") or "").strip()
    fn = str(row.get("filename") or ov.rsplit("/", 1)[-1])
    return ov, fn


def _format_corpus(segments: List[dict], max_segment_lines: int) -> str:
    parts: List[str] = ["=== SAMPLE SEGMENTS ==="]
    for idx, seg in enumerate(segments[:max_segment_lines]):
        ov, fn = _video_meta(seg)
        sn = int(seg.get("segment_number") or 0)
        start = float(seg.get("segment_start_sec") or 0)
        end = float(seg.get("segment_end_sec") or start + 5)
        cap = str(seg.get("reasoning_content") or "").strip()
        stream = stream_fields_from_row(seg)
        stream_note = ""
        if stream.get("stream_id") is not None:
            stream_note = (
                f" | stream chunk={stream.get('chunk_index', '?')}"
                f" @ {timeline_sec(seg):.0f}s"
            )
        parts.append(
            f"- i={idx} | video={ov} | file={fn} | seg={sn} | {start:.0f}-{end:.0f}s"
            f"{stream_note}\n  reasoning: {cap}"
        )
    return "\n".join(parts)


def _extract_json_block(text: str) -> str:
    cleaned = text.strip()
    if "```" in cleaned:
        match = re.search(r"```(?:json)?\s*(\{.*)", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1)
    start = cleaned.find("{")
    if start < 0:
        return cleaned
    return cleaned[start:]


def _repair_json(text: str) -> str:
    s = text.strip()
    s = re.sub(r",\s*]", "]", s)
    s = re.sub(r",\s*}", "}", s)
    if s.count("[") > s.count("]"):
        s += "]" * (s.count("[") - s.count("]"))
    if s.count("{") > s.count("}"):
        s += "}" * (s.count("{") - s.count("}"))
    return s


def _salvage_items(text: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    pattern = (
        r'\{\s*"i"\s*:\s*(\d+)\s*,\s*"query_text"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*'
        r'"label"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}'
    )
    for m in re.finditer(pattern, text):
        items.append({
            "i": int(m.group(1)),
            "query_text": m.group(2).replace('\\"', '"'),
            "label": m.group(3).replace('\\"', '"'),
        })
    if items:
        return items
    # Legacy salvage (older NYC-shaped schema)
    legacy = (
        r'\{\s*"query_text"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"label"\s*:\s*"((?:[^"\\]|\\.)*)"'
    )
    for m in re.finditer(legacy, text):
        items.append({
            "i": len(items),
            "query_text": m.group(1).replace('\\"', '"'),
            "label": m.group(2).replace('\\"', '"'),
        })
    return items


def _parse_llm_json(content: str) -> Dict[str, Any]:
    block = _extract_json_block(content)
    attempts = [block, _repair_json(block)]
    end = block.rfind("}")
    if end > 0:
        attempts.append(_repair_json(block[: end + 1]))

    last_err: Optional[Exception] = None
    for candidate in attempts:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError as exc:
            last_err = exc

    logger.warning(
        "[SUGGEST] JSON parse failed (%s); salvaging partial LLM output",
        last_err,
    )
    return {"items": _salvage_items(content)}


def _new_batch_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]


def _items_to_events(
    items: List[Dict[str, Any]],
    segments: List[dict],
) -> List[Dict[str, Any]]:
    """Map LLM items onto corpus segments; drop ungrounded phrases."""
    events: List[Dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        try:
            idx = int(raw.get("i"))
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= len(segments):
            continue
        seg = segments[idx]
        reasoning = str(seg.get("reasoning_content") or "")
        q = normalize_search_prompt(
            str(raw.get("query_text") or ""),
            max_words=MAX_QUERY_WORDS,
        )
        if not q or not grounded_in_reasoning(q, reasoning):
            logger.info(
                "[SUGGEST] Dropping ungrounded item i=%s: %s",
                idx,
                (raw.get("query_text") or "")[:72],
            )
            continue
        lbl = str(raw.get("label") or "").strip()
        if not lbl or lbl.lower() == q.lower():
            lbl = ""
        else:
            lbl = normalize_search_prompt(lbl, max_words=MAX_QUERY_WORDS)
            if lbl and not grounded_in_reasoning(lbl, reasoning):
                lbl = ""
        ov, fn = _video_meta(seg)
        start = float(seg.get("segment_start_sec") or 0)
        end = float(seg.get("segment_end_sec") or start + 5)
        events.append(
            KeyEventSuggestion(
                query_text=q,
                label=lbl,
                original_video=ov,
                filename=fn,
                segment_start_sec=start,
                segment_end_sec=end,
            ).model_dump()
        )
    return events


def generate_suggestions(
    settings: Settings,
    segments: List[dict],
) -> Tuple[List[str], List[Dict[str, Any]], str]:
    """Call Cosmos; keep only phrases grounded in each segment's reasoning."""
    if not segments:
        return [], [], ""

    batch_id = _new_batch_id()
    segment_lines = min(settings.suggestions_max_segments, 48)
    sample = segments[:segment_lines]
    corpus = _format_corpus(sample, segment_lines)

    user = (
        f"Corpus: {len(sample)} sample segments. "
        f"Return one grounded item per index i=0..{len(sample) - 1}.\n\n"
        f"{corpus}"
    )

    max_tokens = max(settings.cosmos_max_tokens, 4000)
    payload: Dict[str, Any] = {
        "model": settings.cosmos_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": min(settings.cosmos_temperature, 0.2),
        "response_format": {"type": "json_object"},
    }

    headers = {"Content-Type": "application/json"}
    token = (settings.cosmos_authorization or "").strip()
    if token:
        headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"

    with tracer.start_as_current_span("Cosmos Suggestions LLM") as span:
        span.set_attributes({
            "cosmos_url": settings.cosmos_url,
            "model": settings.cosmos_model,
            "segments_sampled": len(sample),
            "max_tokens": max_tokens,
        })
        response = requests.post(settings.cosmos_url, json=payload, headers=headers, timeout=180)
        if response.status_code == 400 and "response_format" in (response.text or ""):
            payload.pop("response_format", None)
            response = requests.post(settings.cosmos_url, json=payload, headers=headers, timeout=180)
        response.raise_for_status()
        body = response.json()

    content = body["choices"][0]["message"]["content"]
    finish = (body.get("choices") or [{}])[0].get("finish_reason")
    if finish == "length":
        logger.warning("[SUGGEST] LLM hit max_tokens=%s; using salvage/repair", max_tokens)

    data = _parse_llm_json(content)
    raw_items = data.get("items") or []
    if not raw_items and (data.get("key_events") or data.get("search_prompts")):
        # Legacy shape: a search_prompts list.
        for p in data.get("search_prompts") or []:
            raw_items.append({"i": len(raw_items) % max(len(sample), 1), "query_text": p, "label": ""})

    events = _items_to_events(raw_items if isinstance(raw_items, list) else [], sample)
    events = dedupe_key_events(events)
    events.sort(
        key=lambda e: (
            float(e.get("segment_start_sec") or 0),
            str(e.get("original_video") or ""),
        )
    )
    events = events[: settings.suggestions_events_count]

    prompts = dedupe_prompts(
        [str(e.get("query_text") or "") for e in events],
        limit=settings.suggestions_search_count,
    )

    return prompts, events, batch_id
