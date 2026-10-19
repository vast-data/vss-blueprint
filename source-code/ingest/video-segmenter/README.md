# Video Segmenter

A VAST DataEngine serverless function that splits uploaded videos into smaller segments for processing.

## What It Does

- Downloads video files from S3 when they are uploaded to the `video-chunks` bucket
- Splits videos into time-based segments (default: 5 seconds each)
- Converts videos to MP4 format (H.264 codec) if needed
- Uploads segments to the `video-chunks-segments` bucket
- Preserves metadata (camera_id, capture_type, location, etc.) from the original video
- Copies stream fields onto each segment: `stream_id`, `chunk_index`, `chunk_start_sec`, `ingest_kind`

## Easy to Adjust

Configure in `ingest/vss-video-ingest-secret-template.yaml`:

- **`segment_duration`**: Length of each segment in seconds (default: `5`)
- **`output_codec`**: Video codec (default: `libx264`)
- **`output_format`**: Output format (default: `mp4`)
- **`output_bucket_suffix`**: Suffix for output bucket (default: `-segments`)

## About the Function

- **Trigger**: S3 bucket event when video is uploaded to `video-chunks`
- **Input**: Video file from S3
- **Output**: Multiple video segments uploaded to `video-chunks-segments`
- **Processing**: Uses FFmpeg for video segmentation and format conversion. One chunk is cut at a time in the process, so a manual upload that publishes every chunk together does not start those jobs at once.
- **Idempotency**: Skips processing if segments already exist

## What Runs It

- **Runtime**: VAST DataEngine serverless runtime
- **Image**: `<registry-host>/<path>/vss2-segmenter:<image-tag>` (build from `source-code`; see [the site deploy](../../../deployments/vss-deploy/README.md#get-vastde-and-roll-function-images))
- **Resources**: Configure CPU/Memory in DataEngine UI pipeline settings
- **Dependencies**: Python 3.12 (builder `vastdataorg/vast-builder:v5.5.0-sp2`), moviepy 1.0.3 with imageio-ffmpeg 0.4.9, `setuptools==75.8.2` (provides `pkg_resources`), boto3 for S3

