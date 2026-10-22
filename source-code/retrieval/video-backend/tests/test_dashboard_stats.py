"""Tests for dashboard statistics aggregation."""
import unittest

from src.utils.dashboard_stats import (
    attach_s3_inventory,
    build_dashboard_stats,
    build_location_filter_options,
    empty_dashboard_stats,
    filter_rows_by_metadata_label,
    segmenter_output_bucket,
)


class DashboardStatsTests(unittest.TestCase):
    def test_build_dashboard_stats_aggregates_objects_metadata_and_time(self):
        rows = [
            {
"source": "s3://bucket/a_seg_001.mp4",
                "original_video": "s3://bucket/a.mp4",
                "filename": "a_seg_001.mp4",
                "segment_number": 1,
                "total_segments": 2,
                "upload_timestamp": "2026-05-31T10:00:00",
                "camera_id": "cam-01",
                "capture_type": "streets",
                "location": "midtown",
                "object_classes": "person,forklift",
                "object_counts": '{"person": 3, "forklift": 1}',
                "reasoning_content": "Forklift near pedestrians.",
                "perception_ok": True,
                "is_public": True,
            },
            {
"source": "s3://bucket/a_seg_001.mp4",
                "original_video": "s3://bucket/a.mp4",
                "filename": "a_seg_001.mp4",
                "segment_number": 1,
                "total_segments": 2,
                "upload_timestamp": "2026-05-31T11:00:00",
                "camera_id": "cam-01",
                "capture_type": "streets",
                "location": "midtown",
                "object_classes": "person",
                "object_counts": '{"person": 2}',
                "reasoning_content": "",
                "perception_ok": False,
                "is_public": True,
            },
            {
"source": "s3://bucket/a_seg_002.mp4",
                "original_video": "s3://bucket/a.mp4",
                "filename": "a_seg_002.mp4",
                "segment_number": 2,
                "total_segments": 2,
                "upload_timestamp": "2026-05-31T10:00:00",
                "camera_id": "",
                "capture_type": "",
                "location": "",
                "object_classes": "",
                "reasoning_content": "Segment two reasoning.",
                "perception_ok": True,
                "is_public": False,
            },
        ]

        stats = build_dashboard_stats(rows)
        self.assertEqual(stats["overview"]["segment_rows"], 3)
        self.assertEqual(stats["overview"]["unique_videos"], 1)
        self.assertEqual(stats["overview"]["fully_indexed_videos"], 1)
        self.assertEqual(stats["overview"]["indexed_clips"], 2)
        self.assertEqual(stats["overview"]["re_ingest_rows"], 1)
        self.assertEqual(stats["quality"]["reasoning_ok"], 2)
        self.assertEqual(stats["quality"]["reasoning_ok_pct"], 66.7)

        by_label = {item["label"]: item for item in stats["objects"]}
        self.assertEqual(by_label["person"]["segment_count"], 2)
        self.assertEqual(by_label["person"]["instance_count"], 5)
        self.assertEqual(by_label["forklift"]["segment_count"], 1)
        self.assertEqual(by_label["forklift"]["instance_count"], 1)
        self.assertNotIn("object_classes", stats["metadata"])
        self.assertEqual(len(stats["uploads_by_day"]), 1)
        self.assertEqual(stats["recent_videos"][0]["indexed_clips"], 2)
        self.assertEqual(stats["recent_videos"][0]["re_ingest_rows"], 1)

    def test_fully_indexed_videos_partial_parent(self):
        rows = [
            {
                "source": "s3://bucket/a_seg_001.mp4",
                "original_video": "s3://bucket/a.mp4",
                "segment_number": 1,
                "total_segments": 2,
                "is_public": True,
            },
            {
                "source": "s3://bucket/b_seg_001.mp4",
                "original_video": "s3://bucket/b.mp4",
                "segment_number": 1,
                "total_segments": 2,
                "is_public": True,
            },
            {
                "source": "s3://bucket/b_seg_002.mp4",
                "original_video": "s3://bucket/b.mp4",
                "segment_number": 2,
                "total_segments": 2,
                "is_public": True,
            },
        ]
        stats = build_dashboard_stats(rows)
        self.assertEqual(stats["overview"]["unique_videos"], 2)
        self.assertEqual(stats["overview"]["fully_indexed_videos"], 1)

    def test_stream_session_grouping(self):
        rows = [
            {
"source": "s3://bucket/chunk1_seg_001.mp4",
                "original_video": "s3://bucket/chunk1.mp4",
                "filename": "chunk1_seg_001.mp4",
                "segment_number": 1,
                "total_segments": 6,
                "extra_metadata": '{"stream_id":"s1","chunk_index":0,"chunk_start_sec":0,"stream_position_sec":0,"ingest_kind":"stream_chunk"}',
                "is_public": True,
            },
            {
"source": "s3://bucket/chunk2_seg_001.mp4",
                "original_video": "s3://bucket/chunk2.mp4",
                "filename": "chunk2_seg_001.mp4",
                "segment_number": 1,
                "total_segments": 6,
                "extra_metadata": '{"stream_id":"s1","chunk_index":1,"chunk_start_sec":30,"stream_position_sec":30,"ingest_kind":"stream_chunk"}',
                "is_public": True,
            },
        ]
        stats = build_dashboard_stats(rows)
        self.assertEqual(stats["overview"]["stream_sessions"], 1)
        self.assertEqual(len(stats["recent_videos"]), 1)
        self.assertEqual(stats["recent_videos"][0]["chunk_count"], 2)
        self.assertEqual(stats["recent_videos"][0]["indexed_clips"], 2)

    def test_empty_dashboard_stats_zeros(self):
        stats = empty_dashboard_stats()
        self.assertEqual(stats["overview"]["total_rows"], 0)
        self.assertEqual(stats["objects"], [])
        self.assertEqual(stats["recent_videos"], [])

    def test_attach_s3_inventory_alignment(self):
        payload = build_dashboard_stats(
            [
                {
    "source": "s3://b/a_seg_001.mp4",
                    "original_video": "s3://b/a.mp4",
                    "filename": "a_seg_001.mp4",
                    "segment_number": 1,
                    "total_segments": 1,
                    "is_public": True,
                },
                {
    "source": "s3://b/a_seg_001.mp4",
                    "original_video": "s3://b/a.mp4",
                    "filename": "a_seg_001.mp4",
                    "segment_number": 1,
                    "total_segments": 1,
                    "is_public": True,
                },
            ]
        )
        attach_s3_inventory(
            payload,
            upload_bucket="vss-chunks",
            segments_bucket="vss-chunks-segments",
            bucket_counts={
                "vss-chunks": 9,
                "vss-chunks-segments": 54,
            },
            bucket_errors={},
        )
        self.assertEqual(segmenter_output_bucket("vss-chunks"), "vss-chunks-segments")
        self.assertEqual(payload["s3_inventory"]["chunks_mp4"], 9)
        self.assertEqual(payload["pipeline_alignment"]["segments_s3_mp4"], 54)
        self.assertEqual(payload["pipeline_alignment"]["indexed_clips"], 1)
        self.assertEqual(payload["pipeline_alignment"]["pending_index"], 53)
        self.assertFalse(payload["pipeline_alignment"]["segments_bucket_matches_segmenter"])
        self.assertFalse(payload["pipeline_alignment"]["healthy"])

    def test_filter_rows_by_location_label(self):
        rows = [
            {"location": "midtown", "source": "a", "original_video": "v1"},
            {"location": "", "source": "b", "original_video": "v2"},
            {"location": "downtown", "source": "c", "original_video": "v3"},
        ]
        options = build_location_filter_options(rows)
        labels = {item["label"]: item["chunk_count"] for item in options}
        self.assertEqual(labels["midtown"], 1)
        self.assertEqual(labels["(empty)"], 1)
        self.assertEqual(labels["downtown"], 1)

        filtered = filter_rows_by_metadata_label(rows, "location", "midtown")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["source"], "a")

    def test_camera_filter_options(self):
        from src.utils.dashboard_stats import build_metadata_filter_options

        rows = [
            {"camera_id": "CAM-001", "original_video": "v1"},
            {"camera_id": "CAM-001", "original_video": "v1"},
            {"camera_id": "", "original_video": "v2"},
        ]
        options = build_metadata_filter_options(rows, "camera_id")
        labels = {item["label"]: item["chunk_count"] for item in options}
        self.assertEqual(labels["CAM-001"], 1)
        self.assertEqual(labels["(empty)"], 1)


if __name__ == "__main__":
    unittest.main()
