# Prompt suggester (DataEngine enrichment)

Reads recent `reasoning_content` from `vss-collection`, calls **Cosmos-Reason2**, and writes grounded search prompts + key events to `vss-events-collection`. Served by video-backend `GET /api/v1/suggestions` and shown in the UI:

- **Search** — suggestion chips on empty state
- **Dashboard** — key events table with in-place segment preview

For each sampled segment the model returns **one** short phrase (≤8 words) that only rephrases that segment’s reasoning — concrete details already present (colors, brands, streets, vehicle type, predicted next move). No domain priors or YOLO hints. Phrases that are not grounded in the source reasoning are dropped. Search chips are the deduped set of those phrases.

## Secret keys (`vss2-secret`)

DataEngine secret **name** stays `vss2-secret`; table/bucket **values** use the `vss-*` namespace:

| Key | Example value | Purpose |
|-----|---------------|---------|
| `vdbcollection` | `vss-collection` | Source segments |
| `vdbpromptscollection` | `vss-events-collection` | Output table |
| `vdbbucket` / `vdbschema` | `vss-vastdb-bucket` / `vss-schema` | VastDB location |
| `cosmos_host` / `cosmos_port` | same as video-reasoner | Cosmos-Reason2 API |

## Deploy

1. Build image: `source-code/scripts/build-vastde-functions.sh` (includes prompt-suggester)
2. The [site deploy](../../../deployments/vss-deploy/README.md) includes this function in the one pipeline.

Do **not** commit filled secret files — use `*-secret-file-template.yaml` and keep credentials local.
