# Video Reasoner

DataEngine function that analyzes video segments using **Cosmos-Reason3** to generate plain-text descriptions.

## What It Does

- Triggered when segments land in `video-chunks-segments` bucket
- Sends segment MP4 (base64) to Cosmos-Reason3
- Produces normalized `reasoning_content` (plain prose, max 1024 chars)
- Shared instruction asks for searchable atoms (color, brand/logo, vehicle type, sign text, clear counts, action, likely next move) when clearly visible — still plain prose, no inventing
- YOLO object classes from upstream detector are injected into the prompt
- Passes results to `video-embedder`

## Configuration

The [site deploy](../../../deployments/vss-deploy/README.md) writes these keys into the DataEngine secret from `site.yaml`:

### Cosmos-Reason3 Settings

| Setting | Default |
|---------|---------|
| `cosmos_host` | (required) — hostname or `hostname/path/prefix` for routed APIs |
| `cosmos_port` | 8001 |
| `cosmoshttpscheme` | `http` (set `https` for TLS-terminated endpoints) |
| `cosmos_authorization` | (optional) Bearer token sent as `Authorization` when set |
| `cosmos_model` | nvidia/cosmos3-nano-reasoner |
| `cosmos_max_tokens` | 4000 |
| `cosmos_temperature` | 0.2 |

Local stack guide: `docs/COSMOS_LOCAL_STACK.md`

---

## Analysis Scenarios

Set `scenario` in ingest secret or per-video via S3 metadata:

| Scenario | Use Case |
|----------|----------|
| `surveillance` | Security cameras, safety monitoring |
| `traffic` | Traffic cameras, vehicle detection |
| `live_driving` | Dashcam / patrol — violations, hazards, pedestrians, trucks, signals |
| `nhl` | Hockey game analysis |
| `sports` | General sports footage |
| `retail` | Store cameras, customer behavior |
| `warehouse` | Industrial safety, PPE compliance |
| `nyc_control` | NYC command-and-control / public-safety monitoring |
| `nyc_safety_surveillance` | NYC street surveillance — pedestrians, vehicles, signage, safety |
| `egocentric` | First-person perspective |
| `general` | Generic video description (default) |

GUI dropdown labels for all scenarios live in [`source-code/shared/ingest_metadata.py`](../../shared/ingest_metadata.py) (`ANALYSIS_SCENARIO_LABELS`).

### Per-Video Override

Set `scenario` in S3 object metadata when uploading:

```python
s3_client.put_object(
    Bucket=bucket, Key=key, Body=video_content,
    Metadata={"scenario": "traffic"}
)
```

---

## Custom Prompts

For full control, provide a custom prompt via S3 metadata (overrides scenario):

```python
Metadata={"custom-prompt": "Analyze safety violations..."}
```

Or use the GUI:
- **Manual Upload / Streaming / Batch Sync**: Check "Use custom prompt"

Max 800 characters (`CUSTOM_PROMPT_MAX_LENGTH` in [`ingest_metadata.py`](../../shared/ingest_metadata.py)). URL-encoded automatically.

### Adding New Scenarios

1. Edit `source-code/ingest/video-reasoner/common/prompts.py` — add prompt to `SCENARIO_PROMPTS`
2. Edit `source-code/shared/ingest_metadata.py` — add UI label to `ANALYSIS_SCENARIO_LABELS`
3. Rebuild **video-reasoner** (prompt), **video-backend**, **video-streaming**, **video-batch-sync**, **video-frontend**
4. Redeploy DataEngine + retrieval stack

Local Python dev: run [`link-ingest-metadata.sh`](../../scripts/link-ingest-metadata.sh) once to symlink `shared/ingest_metadata.py` into service `src/` directories.

---

## Runtime

- **Image**: `<registry-host>/<path>/vss2-reasoner:<image-tag>` (build from `source-code`; see [the site deploy](../../../deployments/vss-deploy/README.md#get-vastde-and-roll-function-images))
- **Trigger**: S3 bucket event on `video-chunks-segments`
