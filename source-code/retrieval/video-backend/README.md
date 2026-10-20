# Video Backend

REST API for video search, explore browse, dashboard stats, suggestions, authentication, and agent tools.

## Table of Contents

- [User Authentication](#user-authentication)
- [Local NIM vs NVIDIA Cloud](#local-nim-vs-nvidia-cloud)
- [GUI Settings](#gui-settings)
- [Custom AI Prompts](#custom-ai-prompts)
- [Ingest Metadata API](#ingest-metadata-api)
- [Upload, streams, and delete](#upload-streams-and-delete)
- [Explore & Synthesize](#explore--synthesize)
- [Suggestions & Dashboard](#suggestions--dashboard)
- [Agent APIs](#agent-apis)
- [Performance](#performance)

---

## User Authentication

Users authenticate with their **VAST username + password** (same as VMS login).

### Setup

The [site deploy](../../../deployments/vss-deploy/README.md) writes this secret from `site.yaml`:

```yaml
vast_host: "<vms-ip-or-hostname>"
tenant_name: "default"
jwt_secret: "<openssl rand -hex 32>"
```

### How It Works

1. User enters username and password on the login page
2. Backend validates against VMS `POST /api/token/{tenant}/`
3. On success, backend issues a signed app JWT for the session

---

## Local NIM vs NVIDIA Cloud

| Setting | `true` (Local NIM) | `false` (NVIDIA Cloud) |
|---------|-------------------|------------------------|
| `embedding_local_nim` | No API key sent | Sends `nvidia_api_key` |

Search / explore **synthesis** uses **Cosmos-Reason3** (`cosmos_host`, `cosmos_port`, `cosmoshttpscheme`) — text-only over indexed evidence. **Cosmos-Embed1** is still required for hybrid vector search.

---

## GUI Settings

### Advanced Search & AI Settings

| Setting | Description | Default |
|---------|-------------|---------|
| Max Clip Cards | Grouped upload cards (`top_k`) | 15 |
| Clips for synthesis | Clip cards sent to Cosmos-Reason3 | 3 |
| Caption vs Video Weight | Hybrid blend (`hybrid_text_weight`) | 0.6 |
| Minimum Similarity | Score threshold | 0.1 |

### Display timezone

Set `display_timezone` in the backend secret (IANA name, e.g. `Asia/Jerusalem`, `UTC`). Exposed as `app.display_timezone` on `GET /api/v1/config`. The UI uses it for explore/search upload badges and explore day labels.

### Time filtering

Presets (5m, 15m, 1h, 24h, 7d) or custom date range on upload timestamp.

---

## Custom AI Prompts

Per-upload custom reasoning prompt in Upload, Streaming, and Batch Sync dialogs. Max length and labels come from [`ingest_metadata.py`](../../shared/ingest_metadata.py) (exposed via `GET /api/v1/metadata/ingest-config`).

---

## Ingest metadata API

Canonical upload/streaming/batch-sync field definitions (capture types, scenario labels, placeholders):

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/metadata/ingest-config` | Labels, dropdown options, custom-prompt max length — used by the frontend shared metadata form (**no auth required**) |
| `GET /api/v1/metadata/schema` | Filterable VastDB columns + distinct values for Search filters |
| `GET /api/v1/metadata/values?field=…` | Autocomplete for a filter field |

Source file: [`source-code/shared/ingest_metadata.py`](../../shared/ingest_metadata.py) — copied into the image at Docker build (see [shared README](../../shared/README.md)).

---

## Upload, streams, and delete

### `POST /api/v1/videos/upload`

Multipart upload. Existing fields stay the same (`file`, `is_public`, `tags`, `allowed_users`, `scenario`, `custom_prompt`, `camera_id`, `capture_type`, `location`).

A file sent without `stream_id` is split on the server at the default chunk size (30 seconds), the same ffmpeg stream-copy used by batch sync (`source-code/shared/video_chunker.py`). A file of 30 seconds or less is stored as chunk 0 with no extra cut. The response includes `stream_id`, `chunk_count`, `object_key` (the first piece), and `object_keys`. Each piece is its own ingest object with `ingest_kind=upload_chunk`, `chunk_index`, `chunk_start_sec`, `chunk_duration_sec`, and `capture_interval`. Explore walks them with Previous/Next.

Two optional form fields skip that split when the caller already cut the file:

| Field | Rule |
|-------|------|
| `stream_id` | Shared id for every chunk of one video. Starts with a letter or number; then letters, numbers, `.`, `_`, `-`; max 128 characters. |
| `chunk_number` | 1-based position in that stream. Required when `stream_id` is set, and `stream_id` is required when `chunk_number` is set. |

Stored S3 metadata is then `stream_id`, `chunk_index` (`chunk_number - 1`), and `ingest_kind=stream_chunk`. The segmenter copies those onto each segment. VastDB keeps them in `extra_metadata`.

The response adds `stream_id` and `chunk_number` when they were set. Explore labels the card **Play chunk {chunk_number}/{max chunk_number}**. The total is the highest `chunk_index` in that stream plus one, so a later batch can continue at chunk 4 and the label becomes `N/4` once that file is indexed.

There is no folder upload. Send the files with the same `stream_id` and sequential `chunk_number` values. The upload dialog does that from **First chunk number** (default 1) in the order the files are listed. The backend image needs `ffmpeg` and `ffprobe` for the server-side split.

### `DELETE /api/v1/videos?original_video=`

Deletes one Explore card (one parent upload), not the rest of its `stream_id`.

Removes, in order:

1. Key-event rows in `vss-events-collection` for that `original_video`
2. Collection rows for that `original_video` (reasoning, embeddings, tags, metadata)
3. The source object, its segment objects, and detection sidecars (`detections/…`)

Allowed buckets are the upload bucket, the segments bucket, and `{upload}-segments`. The response is `VideoDeleteResponse`: `segments_deleted`, `prompts_deleted`, `objects_deleted`, `object_errors`.

| Result | When |
|--------|------|
| `404` | No VastDB rows for that `original_video` (the S3 source is left in place) |
| `403` | `allowed_users` names owners and the caller is not one of them, and the object key does not start with `{username}/` |
| `200` | Rows and objects removed. `object_errors` lists S3 leftovers; retry the same delete to finish them |

Uploads record the uploader in `allowed_users`. Streaming captures do not record a username (`allowed_users` empty, keys under `captures/`). An empty `allowed_users` list means no owner was stored, so a caller who can see the row may delete it.

---

## Explore & Synthesize

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/videos/explore` | Browse parent videos by upload date and **location** (`scope`, `date`, `location`, `indexed`, `limit`, `offset`) |
| `GET /api/v1/videos/chunk` | One stream chunk for Previous/Next (`stream_id`, `chunk_index` 0-based, `scope`) |
| `POST /api/v1/videos/synthesize` | On-demand Cosmos-Reason3 summary over all segments for one `original_video` |
| `DELETE /api/v1/videos` | Delete one parent video (`original_video`) |

`indexed=complete` (default) lists parents whose segment rows cover every slot `1..total_segments`. `indexed=partial` lists parents that have some rows and are still missing slots. Response field `indexed` echoes the filter. Other fields: `uploads_by_day`, `locations[]`, `selected_location`, and `table_available` / `table_message` when VastDB is empty or unreadable.

Stream cards include `stream_id`, `chunk_index`, `stream_chunk_total`, `prev_chunk_index`, and `next_chunk_index`. Neighbors are the nearest lower and higher index in the same stream; gaps are skipped. `GET /videos/chunk` reads that neighbor, including incomplete chunks, so Previous/Next still works from the Incomplete view.

Explore list building uses a **single cached VastDB scan** per TTL window and builds chunk cards in memory (`build_browse_chunk_from_rows`) to avoid N+1 segment queries per video.

---

## Suggestions & Dashboard

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/suggestions` | Grounded search chips + key events from `vss-events-collection` (prompt-suggester rephrases `reasoning_content`) |
| `GET /api/v1/dashboard/stats` | VastDB overview, ingest quality, object instance heatmap (`object_counts`), S3 inventory, recent videos (`scope`) |

Suggestions filters key events to videos the user can access. Returns `prompts_table_available` / `table_message` when the prompts table is missing. Timeline segments include `object_counts` for player chips (`person 3`).

---

## Agent APIs

Thin wrappers for external agents (AgentEngine, custom automation). All require the same JWT as the UI.

### Tools — `/api/v1/tools`

| Endpoint | Agent tool | Description |
|----------|------------|-------------|
| `GET /tools/segments?original_video=…` | `list_segments` | All segment rows for a parent video |
| `GET /tools/segment?source=…` | `get_segment_json` | One segment by clip URI |
| `GET /tools/detections?source=…` | `get_detections` | YOLO bbox sidecar JSON for overlay |
| `POST /tools/search` | `search_hybrid` | Same body as `POST /search` |
| `GET /tools/explore` | `explore_timeline` | Same params as `GET /videos/explore` |
| `POST /tools/synthesize` | `synthesize_video` | Same body as `POST /videos/synthesize` |

### Agent — `/api/v1/agent`

| Endpoint | Description |
|----------|-------------|
| `POST /agent/ask` | `{ question, original_video?, top_k }` — video-specific segments + LLM, or global hybrid search |
| `POST /agent/search-and-answer` | Full `VideoSearchRequest` body → answer + chunk evidence |

**Agent response shape:**

```json
{
  "answer": "…",
  "tool_used": "search_hybrid | video_segments",
  "evidence": { }
}
```

Search is **always hybrid** (caption + video vectors). ACL, time filters, tags, and metadata filters apply.

---

## Performance

Short-lived in-process caches in `src/utils/row_cache.py` reduce repeated VastDB and S3 work:

| Cache | TTL | Used for |
|-------|-----|----------|
| VastDB row scan | 45s | Explore browse, dashboard distinct values, suggestions ACL |
| S3 object counts | 5m | Dashboard segment/chunk inventory |
| Distinct metadata values | 45s | Search filter dropdowns (`GET /metadata/schema`) |
| Prompts / key events | 45s | `GET /suggestions` |

Search explore chunk assembly uses in-memory segment rows from one scan instead of `list_segments_for_video` per parent video.
