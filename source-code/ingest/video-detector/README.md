# Video detector (YOLO11)

Runs object detection on each segment MP4 before the VLM reasoner.

## Flow

1. Receive S3 segment land event (same shape as reasoner).
2. Download segment MP4 from S3 and call `POST <yolo_infer>/v1/infer` with `video_base64`, `filename`, and `include_frames`.
3. Write gzipped sidecar JSON to `detections/{segment_stem}.json.gz` on the segments bucket.
4. Normalize `object_counts` to **max concurrent per class** (peak boxes in any frame); fallback ≈ raw_sum / frame_count when frames are not stored. `detection_count` remains total raw boxes.
5. Emit summary fields (`object_classes`, `object_counts`, `perception_ok`, …) plus metadata pass-through to **video-reasoner**.

## Pipeline link

`video-segment-land-trigger → video-detector → video-reasoner → video-embedder → video-vastdb-writer`

## Secret keys (`vss2-secret`)

| Key | Description |
|-----|-------------|
| `yolo_infer_host` | GPU host or hosted API path for infer service |
| `yolo_infer_port` | Default `8022` (use `443` for HTTPS APIs) |
| `detector_authorization` | (optional) Bearer token sent as `Authorization` when set |
| `yolo_conf` | Confidence threshold (passed to infer service if supported) |
| `yolo_model` | Model id label (logging) |
| `yolo_presign_ttl` | Presigned GET TTL seconds |
| `detection_sidecar_prefix` | S3 key prefix, default `detections/` |
| `detection_store_frames` | `true` to write frame bboxes sidecar |

Also requires S3 keys (`s3accesskey`, `s3secretkey`, `s3endpoint`) and optional VastDB keys for idempotency skip.

## Build

From `<checkout>/source-code`, with `TAG=<image-tag>` and `REG=<registry-host>/<path>`:

```bash
(cd ingest/video-detector && vastde functions build vss2-detector)
docker tag "vss2-detector:latest" "${REG}/vss2-detector:${TAG}"
docker push "${REG}/vss2-detector:${TAG}"
```

All six functions: [Get vastde and roll function images](../../../deployments/vss-deploy/README.md#get-vastde-and-roll-function-images).
