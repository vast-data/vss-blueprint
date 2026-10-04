"""Suggestions chips vs full key-event path."""
import unittest
from unittest.mock import patch

from src.models.user import User
from src.services.suggestions_service import SuggestionsService


def _svc() -> SuggestionsService:
    svc = object.__new__(SuggestionsService)
    svc.settings = type(
        "S",
        (),
        {"vdb_bucket": "bucket", "vdb_schema": "schema"},
    )()
    svc._table_name = "vss-prompts-events"
    return svc


def _rows():
    return [
        {
            "kind": "search_prompt",
            "query_text": "forklift near a person",
            "label": "",
            "original_video": "s3://b/a.mp4",
            "filename": "a.mp4",
            "segment_start_sec": 0,
            "segment_end_sec": 10,
            "generated_at": "2026-01-02T00:00:00Z",
            "batch_id": "batch-1",
            "is_active": True,
        },
        {
            "kind": "key_event",
            "query_text": "person enters frame",
            "label": "Entry",
            "original_video": "s3://b/a.mp4",
            "filename": "a_seg_001.mp4",
            "segment_start_sec": 3,
            "segment_end_sec": 8,
            "generated_at": "2026-01-02T00:00:00Z",
            "batch_id": "batch-1",
            "is_active": True,
        },
    ]


class SuggestionsChipsTests(unittest.TestCase):
    def test_chips_only_skips_video_collection_scan(self):
        svc = _svc()
        user = User(username="demo", auth_type="user")
        svc._read_all_rows = lambda: (_rows(), True, None)

        with patch.object(svc, "_attach_upload_timestamps") as attach, patch.object(
            svc, "_filter_events_by_user"
        ) as filt:
            out = svc.get_suggestions(user, chips_only=True)

        attach.assert_not_called()
        filt.assert_not_called()
        self.assertEqual(out["search_prompts"], ["forklift near a person"])
        self.assertEqual(out["key_events"], [])
        self.assertEqual(out["key_events_count"], 0)
        self.assertEqual(out["batch_id"], "batch-1")

    def test_full_path_filters_key_events(self):
        svc = _svc()
        user = User(username="demo", auth_type="user")
        svc._read_all_rows = lambda: (_rows(), True, None)
        svc._attach_upload_timestamps = lambda events: events
        svc._filter_events_by_user = lambda events, _user: events

        with patch(
            "src.utils.row_cache.cached_suggestions",
            side_effect=lambda _key, _ttl, loader: loader(),
        ):
            out = svc.get_suggestions(user, chips_only=False)

        self.assertEqual(out["search_prompts"], ["forklift near a person"])
        self.assertEqual(len(out["key_events"]), 1)
        self.assertEqual(out["key_events"][0]["query_text"], "person enters frame")


if __name__ == "__main__":
    unittest.main()
