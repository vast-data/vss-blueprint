# Video Batch Sync Service

REST API service for batch ingesting MP4 video files from a source S3 bucket into the
destination ingest bucket (`vss-chunks`). Each source video can be split into fixed-duration
chunks (default **30 seconds**) before upload, matching the former live-streaming ingest path.

## Features

- Lists MP4 files in a source S3 bucket/prefix and uploads to the ingest bucket
- **Configurable chunk duration** (`chunk_duration_sec`, default 30; set `0` to copy whole files)
- Splits videos with ffmpeg and attaches stream-style S3 metadata (`stream_id`, `chunk_index`, `capture_interval`, …)
- Rate limiting via UI delay slider (`batch_size`): pause between **chunk** uploads and between **source** videos
- Applies ingest metadata (tags, privacy, camera/location/scenario, custom prompt)
- Real-time progress (chunks uploaded + source videos processed)

## API

`POST /start` accepts `chunk_duration_sec` (float, default 30). Example:

```json
{
  "username": "demo",
  "chunk_duration_sec": 30,
  "source_bucket": "my-bucket",
  ...
}
```

## Usage (UI)

Access via **S3 Batch Video Sync** in the toolbar:

1. Configure source S3 credentials and bucket/path
2. Click **Check Videos**
3. Set **Chunk duration** (seconds) and upload delay (same slider — applies between chunks and source videos)
4. Fill metadata and click **Start Batch Sync**
5. Monitor progress via the sync icon

## How It Works

1. Lists MP4 files in the source bucket/prefix
2. For each source file (when `chunk_duration_sec > 0`):
   - Downloads to a temp file
   - Splits with ffmpeg into `{duration}s` chunks
   - Uploads each chunk to `vss-chunks` with ingest metadata, waiting `batch_size` seconds between chunks (mirrors live-stream pacing)
3. When `chunk_duration_sec` is `0`, performs a server-side copy of the whole file instead
4. The DataEngine ingest pipeline processes each uploaded chunk object
5. Chunks of one source file share a `stream_id`. Explore shows them as one stream (**Play chunk N/total**, Previous/Next). Delete in Explore removes a single chunk, not the whole stream.

## Technical Notes

- Destination keys: `{username}/{timestamp}_{name}_chunk_NNNN.mp4` (chunked) or `{username}/{timestamp}_{name}.mp4` (whole file)
- Chunk duration clamped to 5–600 seconds when splitting
- Requires **ffmpeg/ffprobe** in the container image

## Deployment

Deployed as a Kubernetes pod accessible at:
- **Internal**: `video-batch-sync-service:5000`
- **External**: `http://video-batch-sync.<cluster_name>.vastdata.com`

Docker image: `your.registry/vss-video-batch-sync:v1` — build from `source-code/` (see [shared README](../shared/README.md#docker-builds)) and [the site deploy](../../deployments/vss-deploy/README.md).
