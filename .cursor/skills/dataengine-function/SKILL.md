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

Aligned with DataEngine **5.5.0-sp2** (builder `vastdataorg/vast-builder:v5.5.0-sp2`, Python 3.12.13). `vast-constraints-buildpack` injects pins at build time. Lifecycle exit 51 is pip failing those constraints (`vastde functions build -v 5`; that failure does not write `build.log`).

Every function starts with:

```
# Cloud events
cloudevents==1.11.0

# Data validation
pydantic==2.10.6
pydantic-settings==2.7.1
```

`cloudevents==1.11.0` matches the constraint `>=1.11.0,<2`. `pydantic==2.10.6` is required because the runtime FastAPI imports `IncEx` from `pydantic.main` (present since pydantic 2.7). The constraint `pydantic>=2.1.1,<3` still accepts 2.5.x, and that pin lands in the pip layer ahead of the runtime copy. The pod then dies at import with `ImportError: cannot import name 'IncEx'`.

Leave OpenTelemetry out of `requirements.txt`. The buildpack installs `opentelemetry-api==1.39.0`, `opentelemetry-sdk==1.39.0`, `opentelemetry-exporter-otlp==1.39.0`, and `opentelemetry-processor-baggage==0.60b0`. `from opentelemetry import trace` still resolves. Pinning 1.38 fails resolution against those exact versions.

VastDB functions (`vastdb-writer`, `video-reasoner`, `video-embedder`, `prompt-suggester`) also include:

```
vastdb==1.3.2
ibis-framework[duckdb]==9.0.0
pyarrow
```

S3 functions pin `boto3` / `botocore`. HTTP/LLM functions pin `requests==2.31.0`. A boto3 pin is fine next to the runtime's own boto3 extra. Avoid `httpx`, unpinned `vastdb`, and pandas unless required.

`video-segmenter` is the only function that imports MoviePy. Add:

```
moviepy==1.0.3
imageio-ffmpeg==0.4.9
setuptools==75.8.2
```

`imageio-ffmpeg` 0.4.9 imports `pkg_resources` while loading `moviepy.editor`. The 3.12 runtime does not ship setuptools, so that import raises `ModuleNotFoundError: No module named 'pkg_resources'`. `setuptools==75.8.2` still provides `pkg_resources` (removed in setuptools 82). Only the segmenter needs this pin.

## Get vastde and roll function images

Aligned with DataEngine **5.5.0-sp2**: CLI `vastde` v5.5.0-sp2 and builder `vastdataorg/vast-builder:v5.5.0-sp2`. Run this on the Linux host that has Docker and the function source (`linux/amd64`). Pulling `vastdataorg/vast-builder` leaves the CLI on whatever builder it already had; `vastde builders set` is what selects it. `<image-tag>` is the tag you push for the function images. It is separate from the 5.5.0-sp2 CLI and builder versions. Full write-up: `deployments/vss-deploy/README.md`.

### 1. Put the CLI on `PATH`

```bash
mkdir -p "$HOME/bin"
curl -fsSL -o "$HOME/bin/vastde" \
  https://github.com/vast-data/dataengine-cli/releases/download/v5.5.0-sp2/vastde_linux_amd64
chmod +x "$HOME/bin/vastde"
case ":$PATH:" in
  *":$HOME/bin:"*) ;;
  *) echo 'export PATH="$HOME/bin:$PATH"' >> "$HOME/.bashrc" ;;
esac
export PATH="$HOME/bin:$PATH"
hash -r
vastde version
```

`vastde version` prints `v5.5.0-sp2`. The darwin binary is a different build; function images are built on this Linux host.

### 2. Log in and select the builder

Tenant, user, and VMS host come from `site.yaml` (`vms.tenant`, `vms.user`, `vms.host`). Type the password when asked. Leave it out of the repo.

```bash
vastde config init \
  --tenant <vms.tenant> \
  --username <vms.user> \
  --vms-url https://<vms.host> \
  --builder-image-url vastdataorg/vast-builder:v5.5.0-sp2
vastde config set --password
vastde builders set v5.5.0-sp2
vastde builders inspect
vastde config view
```

`builders inspect` shows `vastdataorg/vast-builder:v5.5.0-sp2`. If `~/.vast/config.toml` already exists, `config init` stops until you pass `--force`, or you set the same fields with `vastde config set`.

### 3. Build and push a new tag

From the `source-code` directory. `vastde functions build <name>` writes a local image `<name>:latest`. Push that to the artifact in `site.yaml` (`dev/solutions/vss2-*`). In zsh, write `"${name}:latest"`.

Use a new tag for every rebuild. Pushing a tag the pipeline already runs does not change the pods.

```bash
cd <checkout>/source-code
TAG=<image-tag>
REG=<registry-host>/<path>

build() {
  local dir="$1" name="$2"
  echo "==> ${name}"
  (cd "${dir}" && vastde functions build "${name}") || return 1
  docker tag "${name}:latest" "${REG}/${name}:${TAG}" || return 1
  docker push "${REG}/${name}:${TAG}"
}

build ingest/video-segmenter vss2-segmenter
build ingest/video-detector vss2-detector
build ingest/video-reasoner vss2-reasoner
build ingest/video-embedder vss2-embedder
build ingest/vastdb-writer vss2-vastdb
build enrichment/prompt-suggester vss2-events
```

Log in to that registry before `docker push`. A lifecycle exit 51 is the constraints buildpack. Rerun with `-v 5`. In `site.yaml`, write `tag: <image-tag>` (space after the colon).

`build-vastde-functions.sh` runs the same six builds. It reads `REGISTRY` and `TAG` instead of `REG`.

### 4. Publish the new revision

`./deploy.sh` skips a function that already exists and redeploys the pipeline on the revision it already has. Changing `tag` in `site.yaml` does not roll those pods.

```bash
for name in vss-segmenter vss-detector vss-reasoner vss-embedder vss-vastdb vss-suggester; do
  vastde functions update "$name" --image-tag "$TAG" --publish
done
```

In the pipeline builder, set every function to the revision just published and deploy `vss-pipeline`.

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

1. Copy layout + the standard `requirements.txt` block (`cloudevents==1.11.0`, `pydantic==2.10.6`, `pydantic-settings==2.7.1`). Leave OpenTelemetry unpinned. Segmenter also pins `setuptools==75.8.2`.
2. `Settings` + secret template keys.
3. `init` stores clients on `ctx`; `handler` uses spans and structured return dict.
4. VastDB patch if selecting from collection table.
5. Pipeline YAML + DataEngine function register/build.
6. If output feeds UI/backend, add `vdb_*` / API read path in video-backend secret (`vdb_prompts_collection` pattern).
