"""Read LLM-generated search prompts and key events from VastDB."""
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pyarrow as pa
import vastdb
import vastdb._internal as _internal

from src.config import get_settings
from src.models.user import User
from src.utils.suggestion_dedupe import dedupe_key_events, dedupe_prompts

logger = logging.getLogger(__name__)

_original_build = _internal.build_query_data_request


def _apply_vector_patch():
    def patched(schema, predicate, field_names):
        supported, skip = [], set()
        for field in schema:
            t = str(field.type)
            if "fixed_size_list" in t or ("list<" in t and "float" in t):
                skip.add(field.name)
            else:
                supported.append(field)
        names = [n for n in field_names if n not in skip]
        return _original_build(pa.schema(supported), predicate, names)

    _internal.build_query_data_request = patched


_apply_vector_patch()


def normalize_generated_at(ts: Any) -> Optional[str]:
    """Return UTC ISO-8601 with Z suffix for browser-safe parsing."""
    if ts is None:
        return None
    if hasattr(ts, "to_pydatetime"):
        ts = ts.to_pydatetime()
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        return ts.isoformat().replace("+00:00", "Z")
    text = str(ts).strip()
    if not text:
        return None
    if text.endswith("Z") or re.search(r"[+-]\d{2}:\d{2}$", text):
        return text
    return f"{text}Z"


PROMPT_COLUMNS = (
    "pk", "kind", "query_text", "label", "original_video", "filename",
    "segment_start_sec", "segment_end_sec", "generated_at", "batch_id", "is_active",
)


class _PromptsTableMissing(Exception):
    pass


class _PromptsTableReadError(Exception):
    pass


class SuggestionsService:
    def __init__(self):
        self.settings = get_settings()
        endpoint = self.settings.vdb_endpoint
        if not endpoint.startswith(("http://", "https://")):
            endpoint = f"http://{endpoint}"
        self._session = vastdb.connect(
            endpoint=endpoint,
            access=self.settings.vdb_access_key,
            secret=self.settings.vdb_secret_key,
            ssl_verify=False,
        )
        self._table_name = getattr(
            self.settings, "vdb_prompts_collection", "vss-events-collection"
        )

    def _read_all_rows(self) -> tuple[List[dict], bool, Optional[str]]:
        from src.utils.row_cache import PROMPTS_CACHE_TTL_SEC, cached_prompt_rows

        cache_key = (
            f"prompts:{self.settings.vdb_bucket}:{self.settings.vdb_schema}:{self._table_name}"
        )
        try:
            rows = cached_prompt_rows(cache_key, PROMPTS_CACHE_TTL_SEC, self._load_prompt_rows)
            return rows, True, None
        except _PromptsTableMissing as exc:
            return [], False, str(exc)
        except _PromptsTableReadError as exc:
            return [], False, str(exc)

    def _load_prompt_rows(self) -> List[dict]:
        table_ref = (
            f"{self.settings.vdb_bucket}/{self.settings.vdb_schema}/{self._table_name}"
        )
        with self._session.transaction() as tx:
            bucket = tx.bucket(self.settings.vdb_bucket)
            db_schema = bucket.schema(self.settings.vdb_schema)
            try:
                table = db_schema.table(self._table_name)
            except Exception as exc:
                logger.warning("[SUGGESTIONS] Prompts table missing: %s", exc)
                raise _PromptsTableMissing(f"Could not read prompts table {table_ref}.") from exc
            try:
                result = table.select(columns=list(PROMPT_COLUMNS), internal_row_id=False)
                arrow = result.read_all()
            except Exception as exc:
                logger.warning("[SUGGESTIONS] Prompts table read failed: %s", exc)
                raise _PromptsTableReadError(f"Could not read prompts table {table_ref}.") from exc
        if arrow.num_rows == 0:
            return []
        try:
            return arrow.to_pylist()
        except Exception:
            import pandas as pd
            return [row.to_dict() for _, row in arrow.to_pandas().iterrows()]

    @staticmethod
    def _ts_sort_key(ts: Any) -> float:
        if ts is None:
            return 0.0
        if hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts.timestamp()
        iso = normalize_generated_at(ts)
        if not iso:
            return 0.0
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0

    def _latest_batch_id(self, rows: List[dict]) -> Optional[str]:
        if not rows:
            return None
        best_ts = None
        best_batch = None
        for row in rows:
            if not row.get("is_active"):
                continue
            ts = row.get("generated_at")
            bid = str(row.get("batch_id") or "")
            if not bid:
                continue
            key = self._ts_sort_key(ts)
            if best_ts is None or key > best_ts:
                best_ts = key
                best_batch = bid
        return best_batch

    @staticmethod
    def _row_to_key_event(row: dict) -> Dict[str, Any]:
        query = str(row.get("query_text") or "").strip()
        label = str(row.get("label") or "").strip()
        if label.lower() == query.lower():
            label = ""
        gen_at = normalize_generated_at(row.get("generated_at"))
        return {
            "query_text": query,
            "label": label,
            "original_video": str(row.get("original_video") or ""),
            "filename": str(row.get("filename") or ""),
            "segment_start_sec": float(row.get("segment_start_sec") or 0),
            "segment_end_sec": float(row.get("segment_end_sec") or 0),
            "generated_at": gen_at,
            "batch_id": str(row.get("batch_id") or ""),
        }

    def _dedupe_key_event_rows(self, event_rows: List[dict]) -> List[Dict[str, Any]]:
        """Timeline order, then subject-aware dedupe across stored key_event rows."""
        event_rows = sorted(
            event_rows,
            key=lambda r: (
                -self._ts_sort_key(r.get("generated_at")),
                float(r.get("segment_start_sec") or 0),
                str(r.get("original_video") or ""),
            ),
        )
        events = [self._row_to_key_event(row) for row in event_rows]
        events = [ev for ev in events if ev.get("query_text")]
        kept = dedupe_key_events(events)
        kept.sort(
            key=lambda e: (
                float(e.get("segment_start_sec") or 0),
                str(e.get("original_video") or ""),
            )
        )
        return kept

    def _attach_upload_timestamps(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not events:
            return events
        from src.services.vastdb_service import get_vastdb_service

        needed = {
            str(e.get("original_video") or "").strip()
            for e in events
            if str(e.get("original_video") or "").strip() and not e.get("upload_timestamp")
        }
        ts_map: Dict[str, Any] = {}
        if needed:
            ts_map = get_vastdb_service().map_upload_timestamps_by_video(needed)

        for ev in events:
            ov = str(ev.get("original_video") or "").strip()
            raw = ev.get("upload_timestamp") or (ts_map.get(ov) if ov else None)
            ev["upload_timestamp"] = normalize_generated_at(raw)
        return events

    def _filter_events_by_user(self, events: List[Dict[str, Any]], user: User) -> List[Dict[str, Any]]:
        if not events:
            return events
        from src.services.vastdb_service import get_vastdb_service

        accessible = get_vastdb_service().accessible_original_videos(user)
        visible: List[Dict[str, Any]] = []
        for ev in events:
            ov = str(ev.get("original_video") or "").strip()
            if ov and ov in accessible:
                visible.append(ev)
        return visible

    def _empty_payload(
        self,
        table: str,
        *,
        table_available: bool,
        table_message: Optional[str],
    ) -> Dict[str, Any]:
        return {
            "batch_id": None,
            "generated_at": None,
            "search_prompts": [],
            "key_events": [],
            "key_events_count": 0,
            "table": table,
            "prompts_table_available": table_available,
            "table_message": table_message,
        }

    def _search_prompts_from_rows(self, active: List[dict]) -> List[str]:
        prompt_rows = sorted(
            [r for r in active if str(r.get("kind") or "") == "search_prompt"],
            key=lambda r: -self._ts_sort_key(r.get("generated_at")),
        )
        raw_prompts: List[str] = []
        for row in prompt_rows:
            q = str(row.get("query_text") or "").strip()
            if q:
                raw_prompts.append(q)
        return dedupe_prompts(raw_prompts, limit=10)[:10]

    def _build_suggestions(self, user: User, *, chips_only: bool) -> Dict[str, Any]:
        rows, table_available, table_message = self._read_all_rows()
        table = f"{self.settings.vdb_bucket}/{self.settings.vdb_schema}/{self._table_name}"
        if not table_available:
            return self._empty_payload(
                table, table_available=False, table_message=table_message
            )

        active = [r for r in rows if r.get("is_active")]
        if not active:
            return self._empty_payload(table, table_available=True, table_message=None)

        prompts = self._search_prompts_from_rows(active)
        newest = max(active, key=lambda r: self._ts_sort_key(r.get("generated_at")))
        batch_id = str(newest.get("batch_id") or "") or None
        generated_iso = normalize_generated_at(newest.get("generated_at"))

        if chips_only:
            return {
                "batch_id": batch_id,
                "generated_at": generated_iso,
                "search_prompts": prompts,
                "key_events": [],
                "key_events_count": 0,
                "table": table,
                "prompts_table_available": True,
                "table_message": None,
            }

        event_rows = [r for r in active if str(r.get("kind") or "") == "key_event"]
        events = self._dedupe_key_event_rows(event_rows)
        events = self._attach_upload_timestamps(events)
        events = self._filter_events_by_user(events, user)
        return {
            "batch_id": batch_id,
            "generated_at": generated_iso,
            "search_prompts": prompts,
            "key_events": events,
            "key_events_count": len(events),
            "table": table,
            "prompts_table_available": True,
            "table_message": None,
        }

    def get_suggestions(self, user: User, *, chips_only: bool = False) -> Dict[str, Any]:
        if chips_only:
            return self._build_suggestions(user, chips_only=True)

        from src.utils.row_cache import SUGGESTIONS_CACHE_TTL_SEC, cached_suggestions

        cache_key = (
            f"suggestions:{self.settings.vdb_bucket}:{self.settings.vdb_schema}:"
            f"{self._table_name}:{user.normalized_username}"
        )
        return cached_suggestions(
            cache_key,
            SUGGESTIONS_CACHE_TTL_SEC,
            lambda: self._build_suggestions(user, chips_only=False),
        )


_service: Optional[SuggestionsService] = None


def get_suggestions_service() -> SuggestionsService:
    global _service
    if _service is None:
        _service = SuggestionsService()
    return _service
