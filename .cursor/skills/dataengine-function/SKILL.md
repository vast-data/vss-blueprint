---
name: dataengine-function
description: >-
  Author VAST DataEngine serverless functions (init/handler, VastEvent, vss2-secret,
  VastDB SDK, OpenTelemetry). Use when creating or changing DataEngine functions,
  enrichment/ingest pipelines, vastde deploys, scheduled triggers, or prompt-suggester-style jobs.
---

# DataEngine serverless functions (VSS blueprint)

Follow existing functions under `source-code/ingest/*` and `source-code/enrichment/*` before inventing new structure.

## Layout

```
<function-name>/
├── main.py              # init(ctx), handler(ctx, event: VastEvent)
├── requirements.txt     # standard stack + function-specific deps
├── README.md            # secret keys, deploy notes
└── common/
    ├── models.py        # Settings.from_ctx_secrets, event/result models
    ├── vastdb_client.py # VastDB connect, select/insert (if needed)
    ├── vastdb_patch.py  # vector-column select patch (if reading collection table)
    └── …                # domain logic (LLM client, parsers, etc.)
```

## Entry points

```python
from opentelemetry import trace
from vast_runtime.vast_event import VastEvent  # type: ignore

def init(ctx):
    """Initialize the serverless function"""
    with ctx.tracer.start_as_current_span("<Name> Initialization"):
        settings = Settings.from_ctx_secrets(ctx.secrets)
        ctx.settings = settings
        ctx.vastdb_client = VastDBClient(settings)  # if VastDB
        ctx.logger.info("[INIT] …")

def handler(ctx, event: VastEvent):
    """Main handler function for vast serverless runtime"""
    with ctx.tracer.start_as_current_span("<Name> Handler") as handler_span:
        try:
            data = event.get_data()
            event_type = getattr(event, "get_type", lambda: "element_trigger")()
            handler_span.set_attribute("event_type", event_type)
            # pipeline: honor data.get("status") == "error"|"skipped" like vastdb-writer
            …
            return {"status": "success", …}
        except Exception as e:
            handler_span.set_attribute("error", True)
            handler_span.record_exception(e)
            ctx.logger.error(f"…: {e}")
            return {"status": "error", "error": str(e)}
```

- **Pipeline functions**: default `event_type` `element_trigger`; parse `event.get_data()` from upstream.
- **Scheduled enrichment**: default `scheduled_trigger`; may ignore empty `data`; use nested spans per phase (fetch → compute → VastDB write).

## Settings (`common/models.py`)

- Load from `secrets["vss2-secret"]` only.
- Field names match secret YAML keys (lowercase, no underscores): `vdbendpoint`, `vdbcollection`, `cosmos_host`, etc.
- Use `from_ctx_secrets` pattern:

```python
@classmethod
def from_ctx_secrets(cls, secrets: Dict[str, Any]) -> "Settings":
    raw = secrets["vss2-secret"]
    config = {k: raw[k] for k in cls.__annotations__ if k in raw}
    return cls(**config)
```

- Add function-specific keys to `deployments/vss-deploy/site.yaml`. The site deploy writes the live secret from that file. See `deployments/vss-deploy/README.md`.

## Standard `requirements.txt`

Start **every** DataEngine function (ingest, enrichment, new jobs) with this full block — copy verbatim, do not drop lines:

```
# Cloud events
cloudevents==1.10.1

# VastDB Python SDK and dependencies
vastdb==1.3.2
ibis-framework[duckdb]==9.0.0

# PyArrow for data handling
pyarrow

# Data validation
pydantic==2.5.2
pydantic-settings==2.1.0

# OpenTelemetry for tracing (all four required by DataEngine runtime)
opentelemetry-api==1.38
opentelemetry-sdk==1.38
opentelemetry-exporter-otlp==1.38
opentelemetry-processor-baggage==0.59b0
```

**OpenTelemetry:** include `opentelemetry-processor-baggage==0.59b0` on every function. Older ingest `requirements.txt` files often omitted it — add it whenever you touch deps.

Ingest functions that must carry the full OTel block: `video-segmenter`, `video-reasoner`, `video-embedder`, `vastdb-writer`.

Add only what the function needs below this block, with versions aligned to siblings:

| Need | Add |
|------|-----|
| S3 | `boto3`, `botocore` (see video-segmenter) |
| HTTP / LLM / NIM | `requests==2.31.0` (see video-reasoner) |
| Do **not** add | `httpx`, unpinned `vastdb`, `pandas` unless required |

## VastDB reads on `vss-collection`

Tables include `vectors` / `vectors_visual`. Import `common/vastdb_patch.py` before `table.select()` so vector columns are excluded from projections.

- Use `vastdb.connect(endpoint=…, access=…, secret=…, ssl_verify=False)`.
- Prefer `arrow.to_pylist()` over pandas.
- Client class name: **`VastDBClient`** with `self.bucket`, `self.schema_name`, `self.table_name`.
- Log `[VASTDB]` / `[COMPLETE]` like ingest functions.

## Pipeline YAML

- Site pipeline and secret: `deployments/vss-deploy/site.yaml`. See `deployments/vss-deploy/README.md`.
- Pattern: `secrets: [vss2-secret]`, `function_deployments`, `links` (trigger → function), `triggers` with Schedule VRN.

## Reference implementations

| Type | Example |
|------|---------|
| Pipeline write | `source-code/ingest/vastdb-writer/` |
| Pipeline LLM | `source-code/ingest/video-reasoner/` |
| Scheduled enrichment | `source-code/enrichment/prompt-suggester/`, `fraud-detection-blueprint/.../fraud-detector/` |

## Checklist for a new function

1. Copy layout + standard `requirements.txt` (including `opentelemetry-processor-baggage==0.59b0`).
2. `Settings` + secret template keys.
3. `init` stores clients on `ctx`; `handler` uses spans and structured return dict.
4. VastDB patch if selecting from collection table.
5. Pipeline YAML + DataEngine function register/build.
6. If output feeds UI/backend, add `vdb_*` / API read path in video-backend secret (`vdb_prompts_collection` pattern).
