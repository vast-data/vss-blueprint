# VastDB Writer

A VAST DataEngine serverless function that stores video embeddings and metadata in VastDB for vector search.

## What It Does

- Receives vector embeddings and metadata from the `video-embedder` function
- Stores embeddings in VastDB as vector columns
- Stores metadata (camera_id, capture_type, location, etc.) as regular columns
- Stores reasoning text for display and text search
- Stores YOLO `object_classes` and peak concurrent `object_counts`
- Creates database records for each video segment (timeline: `segment_start_sec`, `segment_end_sec`; grouping: `original_video` parent S3 URI)

## Easy to Adjust

Configure in `ingest/vss-video-ingest-secret-template.yaml`:

- **`vdbendpoint`**: VastDB endpoint URL (from QueryEngine VIP pool)
- **`vdbbucket`**: Database bucket name (e.g., `processed-videos-db`)
- **`vdbschema`**: Database schema name (e.g., `processed-videos-schema`)
- **`vdbcollection`**: Table/collection name (e.g., `processed-videos-collection`)
- **`vdbaccesskey`**: VastDB access key
- **`vdbsecretkey`**: VastDB secret key
- **`embeddingdimensions`**: Must match embedding model dimensions
- **`embeddingmodel`**: Embedding model name (for reference)

**To add custom metadata fields**, see [Dynamic Metadata Filters](../README.md#adding-custom-metadata-fields).

## About the Function

- **Trigger**: Receives events from `video-embedder` function
- **Input**: Vector embeddings, reasoning text, and metadata
- **Output**: Database records in VastDB
- **Processing**: Inserts data into VastDB using ADBC driver
- **Schema**: Automatically creates/updates table schema if needed
- **Validation**: Skips if embedding is invalid or empty

## What Runs It

- **Runtime**: VAST DataEngine serverless runtime
- **Image**: `<registry-host>/<path>/vss2-vastdb:<image-tag>` (build from `source-code`; see [the site deploy](../../../deployments/vss-deploy/README.md#get-vastde-and-roll-function-images))
- **Resources**: Configure CPU/Memory in DataEngine UI pipeline settings
- **Dependencies**: Python 3.12 (builder `vastdataorg/vast-builder:v5.5.0-sp2`), ADBC driver for VastDB, libadbc_driver_vastdb.so

