# Video Streaming Service

REST API service that captures YouTube videos and uploads segments to S3, triggering the ingest pipeline. Used to simulate realtime video streaming for demos.

## Features

- Captures YouTube streams and VOD videos
- Splits into configurable chunks (default: 30 seconds)
- Uploads to S3 with metadata
- Auto-triggers ingest pipeline
- VOD auto-stops when video ends; live streams run until stopped

## Usage via GUI

1. Navigate to **Settings → Start Video Stream**
2. Enter YouTube URL (S3 credentials are pre-filled from backend config)
3. Configure segment duration and metadata (camera_id, capture_type, location, scenario) — same options as Upload; labels from backend ingest-config
4. Click "Start Stream"
5. Use "Stop Stream" to stop capture

## API Endpoints

**Base URL**: `http://video-streamer.<cluster_name>.vastdata.com`

### POST /start

```json
{
  "youtube_url": "https://www.youtube.com/watch?v=...",
  "access_key": "...",
  "secret_key": "...",
  "s3_endpoint": "http://...",
  "bucket_name": "video-chunks",
  "capture_interval": 30,
  "camera_id": "cam-01",
  "capture_type": "traffic",
  "location": "downtown",
  "scenario": "surveillance",
  "custom_prompt": "Analyze safety...",
  "max_duration": 3600
}
```

### POST /stop

Stops the running capture.

### GET /status

Returns capture status and configuration.

### GET /ping

Health check.

## Technical Details

- **Format**: MP4 (H.264 + AAC for YouTube via yt-dlp)
- **Resolution**: Up to 1080p (YouTube path)
- **YouTube VOD**: full video downloaded once, then chunked locally with ffmpeg stream-copy; any chunk > `capture_interval` gets a fast tail trim (copy remux) before upload; metadata `chunk_duration_sec` = nominal interval; segmenter caps segmentation to the same value
- **YouTube live**: per-chunk yt-dlp with retries; one timeout does not stop the session; same trim/normalize before upload
- **Image**: `<registry>/<path>/vss2-streaming:latest` — build from `source-code/` (see [shared README](../shared/README.md#docker-builds))
- **Internal**: `video-stream-capture-service:5000`
- **S3 metadata**: built via `build_s3_ingest_metadata()` from [`shared/ingest_metadata.py`](../shared/ingest_metadata.py)
- **One capture is one stream**: each chunk object gets `stream_id`, 0-based `chunk_index`, and `ingest_kind=stream_chunk`. Explore lists them as **Play chunk N/total** with Previous/Next. Streaming does not record a username. Delete those chunks from Explore; an empty owner list is allowed.

### Optional env (streaming pod)

| Variable | Purpose |
|----------|---------|
| `YTDLP_COOKIES_FILE` | Netscape cookies for YouTube (datacenter / bot checks) |
| `YTDLP_SEGMENT_TIMEOUT_SEC` | Per-chunk yt-dlp timeout (live path); default scales with seek offset |
| `YTDLP_FULL_DOWNLOAD_TIMEOUT_SEC` | Full VOD download timeout before local ffmpeg chunking |
| `YTDLP_SEGMENT_MAX_RETRIES` | Retries per live chunk on timeout (default `2`) |
