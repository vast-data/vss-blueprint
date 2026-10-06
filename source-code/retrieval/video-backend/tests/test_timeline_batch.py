"""Batched timeline OR hydrate."""
import unittest
from unittest.mock import MagicMock

import pandas as pd

from src.models.user import User
from src.services.vastdb_service import VastDBService


class TimelineBatchTests(unittest.TestCase):
    def test_list_segments_for_videos_uses_or_not_in(self):
        svc = object.__new__(VastDBService)
        svc._adbc_connection = {
            "bucket": "b",
            "schema": "s",
            "driver_path": "/x",
            "endpoint": "http://e",
            "access_key": "a",
            "secret_key": "k",
        }
        svc.settings = MagicMock(vdb_collection="c")
        user = User(username="u", auth_type="user")
        captured = {}

        def fake_exec(sql):
            captured["sql"] = sql
            return pd.DataFrame(
                [
                    {
                        "filename": "a_1.mp4",
                        "source": "s3://b/a_1.mp4",
                        "reasoning_content": "r",
                        "allowed_users": [],
                        "is_public": True,
                        "upload_timestamp": None,
                        "duration": 1.0,
                        "segment_number": 1,
                        "total_segments": 1,
                        "segment_start_sec": 0,
                        "segment_end_sec": 1,
                        "original_video": "s3://b/a.mp4",
                        "tags": [],
                        "cosmos_model": "",
                        "tokens_used": 0,
                        "cached_prompt_tokens": 0,
                        "camera_id": "",
                        "capture_type": "",
                        "location": "",
                        "perception_json": "",
                        "object_classes": "",
                        "object_counts": "",
                        "max_detection_conf": None,
                        "perception_ok": True,
                        "perception_source": "",
                        "detection_sidecar_uri": "",
                        "detection_frame_count": 0,
                        "detection_count": 0,
                    }
                ]
            )

        svc._execute_adbc_query = fake_exec
        svc._user_has_access = lambda row, u, *a, **k: True
        out = svc.list_segments_for_videos(
            ["s3://b/a.mp4", "s3://b/c.mp4"], user
        )
        sql = captured["sql"]
        self.assertNotIn(" IN (", sql)
        self.assertIn("original_video = 's3://b/a.mp4'", sql)
        self.assertIn(" OR ", sql)
        self.assertIn("original_video = 's3://b/c.mp4'", sql)
        self.assertEqual(len(out["s3://b/a.mp4"]), 1)
        self.assertEqual(out["s3://b/c.mp4"], [])


if __name__ == "__main__":
    unittest.main()
