"""Server-side upload uses the shared 30s splitter and does not re-cut a short file."""
import os
import tempfile
import unittest
from dataclasses import dataclass

from src.utils import upload_chunks
from src.utils.upload_chunks import split_and_upload


@dataclass
class _Chunk:
    path: str
    chunk_index: int
    chunk_start_sec: float
    chunk_duration_sec: float
    owns_path: bool


class _S3:
    def __init__(self, fail_on=None):
        self.calls = []
        self.deleted = []
        self.fail_on = fail_on
        self.settings = type("Settings", (), {"s3_upload_bucket": "chunks"})()

    def put_local_video(self, user, path, filename, **kwargs):
        if self.fail_on is not None and kwargs["chunk_index"] == self.fail_on:
            raise RuntimeError("put failed")
        self.calls.append((path, filename, kwargs))
        return f"alice/clip_chunk_{kwargs['chunk_index']:04d}.mp4"

    def delete_keys(self, bucket, keys):
        self.deleted.extend(keys)
        return len(list(keys)), []


class ChunkMetadataTests(unittest.TestCase):
    def test_fields_match_explore(self):
        meta = upload_chunks.chunk_s3_metadata(
            stream_id="abc",
            chunk_index=2,
            chunk_start_sec=60,
            chunk_duration_sec=15.5,
            capture_interval_sec=30,
            ingest_kind="upload_chunk",
            original_filename="game.mp4",
        )
        self.assertEqual(meta["stream_id"], "abc")
        self.assertEqual(meta["chunk_index"], "2")
        self.assertEqual(meta["chunk_start_sec"], "60.000")
        self.assertEqual(meta["chunk_duration_sec"], "15.500")
        self.assertEqual(meta["capture_interval"], "30.000")
        self.assertEqual(meta["ingest_kind"], "upload_chunk")
        self.assertEqual(meta["original-filename"], "game.mp4")

    def test_object_key_groups_chunks(self):
        try:
            from src.services.s3_service import S3Service
        except ModuleNotFoundError as exc:
            self.skipTest(str(exc))
        key = S3Service.object_key_for(
            None,
            "alice",
            "clip.mov",
            chunk_index=3,
            stamp="20260101_000000",
            unique_id="abcd1234",
        )
        self.assertEqual(key, "alice/20260101_000000_abcd1234_chunk_0003.mov")


class IterChunksTests(unittest.TestCase):
    def setUp(self):
        self.mod = upload_chunks._chunker
        self.probe = self.mod.probe_duration_sec
        self.extract = self.mod.extract_chunk

    def tearDown(self):
        self.mod.probe_duration_sec = self.probe
        self.mod.extract_chunk = self.extract

    def test_short_file_is_not_cut(self):
        def extract(*args, **kwargs):
            raise AssertionError("short file must not be cut")

        self.mod.probe_duration_sec = lambda path: 12.0
        self.mod.extract_chunk = extract
        chunks = list(self.mod.iter_video_chunks("/tmp/in.mp4", 30))
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].path, "/tmp/in.mp4")
        self.assertEqual(chunks[0].chunk_index, 0)
        self.assertFalse(chunks[0].owns_path)
        self.assertAlmostEqual(chunks[0].chunk_duration_sec, 12.0)

    def test_long_file_is_cut_at_default_size(self):
        calls = []

        def extract(source, start, duration, output):
            calls.append((start, duration))
            return True

        self.mod.probe_duration_sec = lambda path: 75.0
        self.mod.extract_chunk = extract
        with tempfile.TemporaryDirectory() as work:
            chunks = list(self.mod.iter_video_chunks("/tmp/in.mp4", 30, work_dir=work))
        self.assertEqual([(c.chunk_index, c.owns_path) for c in chunks], [(0, True), (1, True), (2, True)])
        self.assertEqual(calls, [(0.0, 30.0), (30.0, 30.0), (60.0, 15.0)])


class SplitAndUploadTests(unittest.TestCase):
    def test_uploads_each_piece_and_keeps_source_chunk(self):
        fd, path = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)
        with open(path, "wb") as handle:
            handle.write(b"video")
        s3 = _S3()

        def fake_iter(source, duration, work_dir=None):
            self.assertEqual(duration, 30.0)
            yield _Chunk(path, 0, 0.0, 10.0, False)

        original = upload_chunks.iter_video_chunks
        upload_chunks.iter_video_chunks = fake_iter
        try:
            stream_id, keys = split_and_upload(
                s3,
                path,
                "clip.mp4",
                user=object(),
                is_public=True,
                tags=[],
                allowed_users=[],
            )
        finally:
            upload_chunks.iter_video_chunks = original
            os.remove(path)

        self.assertEqual(len(keys), 1)
        self.assertTrue(stream_id)
        extra = s3.calls[0][2]["extra_metadata"]
        self.assertEqual(extra["ingest_kind"], "upload_chunk")
        self.assertEqual(extra["capture_interval"], "30.000")
        self.assertEqual(extra["stream_id"], stream_id)
        self.assertEqual(s3.calls[0][2]["chunk_index"], 0)
        self.assertEqual(s3.deleted, [])

    def test_failed_piece_deletes_uploaded_keys(self):
        fd, path = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)
        s3 = _S3(fail_on=1)

        def fake_iter(source, duration, work_dir=None):
            yield _Chunk(path, 0, 0.0, 30.0, False)
            yield _Chunk(path, 1, 30.0, 10.0, False)

        original = upload_chunks.iter_video_chunks
        upload_chunks.iter_video_chunks = fake_iter
        try:
            with self.assertRaises(RuntimeError):
                split_and_upload(
                    s3,
                    path,
                    "clip.mp4",
                    user=object(),
                    is_public=False,
                    tags=[],
                    allowed_users=[],
                )
        finally:
            upload_chunks.iter_video_chunks = original
            os.remove(path)

        self.assertEqual(s3.deleted, ["alice/clip_chunk_0000.mp4"])


if __name__ == "__main__":
    unittest.main()
