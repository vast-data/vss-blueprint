# Video Frontend

Angular web UI for the VSS Blueprint: **Search**, **Explore**, and **Dashboard** modes from the toolbar.

## Application Modes

Three top-level modes share the same scope pills (**All Videos** / **My Videos** / **Public Only**) and a dedicated **Refresh** button (reloads the current view without switching mode).

| Mode | Route | Purpose |
|------|-------|---------|
| **Search** | `/search` | Hybrid semantic search; clip cards with match timeline; Cosmos-Reason2 synthesis |
| **Explore** | `/explore` | Browse uploads by day and location; stream chunks, delete, summarize on demand |
| **Dashboard** | `/dashboard` | VastDB stats, ingest quality, object **instance** heatmap (peak count sums), grounded key events |

### Search

- Empty state shows **search suggestions** from `GET /api/v1/suggestions` (prompt-suggester grounded rephrases → `vss-events-collection`). Clicking a suggestion fills the search bar only.
- Clip cards: **upload time** badge (config timezone), **% match** badge, match timeline with segment jumps, reasoning caption and class chips.
- Search animation phases follow the live request; model names come from `GET /api/v1/config` (Cosmos-Embed1, Cosmos-Reason2).
- After a search, results persist when navigating away and back.

### Explore

- Hero: **Browse by upload date** — clips day by day.
- **Date rail** and **location rail** filter the list.
- **Complete / Incomplete** pills. Complete is the default (`indexed=complete`): every segment slot is in VastDB. Incomplete (`indexed=partial`) shows chunks that are still missing slots, so they can be deleted or watched while indexing finishes. The card line reads `N of M segments` until the chunk is complete.
- Cards: upload time badge, segment timeline (jump to moment), metadata and object chips, **Play chunk N/M**, **Summarize**, and **Delete**.
- Files that share a `stream_id` are one stream. **Play chunk N/total** uses the 1-based chunk number. Total is the highest chunk number seen for that stream.
- Previous and Next are inside the player, centered as **Previous · Chunk N/total · Next**. They load the neighbor with `GET /api/v1/videos/chunk`. The Explore grid does not show those buttons.
- **Delete** confirms, then `DELETE /api/v1/videos?original_video=`. One card is one upload. Other chunks of the same stream stay.
- Player plays the **full parent chunk** (`original_video`); segment timeline and bbox overlay (OFF/ON toggle in the timeline header).

### Video playback (Search + Explore)

- **Search:** segment MP4s for hover preview; player opens at best match; match timeline with query highlights.
- **Explore:** full-chunk stream; segment clicks seek within the parent file.
- **Bbox overlay:** `GET /api/v1/videos/detections?source=…` sidecar; toggle persisted in `localStorage`.
- Segment list under the player: selectable reasoning text; object chips show peak counts when present (`person 3`).
- Autoplay is **muted** by default; users can unmute via native controls.
- Only the video preview on result cards opens the player (description is selectable).

### Dashboard

- Scope-scoped VastDB statistics; KPI panels hidden when the collection is unreadable.
- **Object heatmap** sums YOLO `object_counts` (instance totals); tooltips include segment presence.
- **Key events** table: grounded ≤8-word phrases; search icon opens the event clip; upload time uses `display_timezone` from config.

## Other Features

- **Video Upload / Streaming / Batch Sync**: shared metadata form — `GET /api/v1/metadata/ingest-config` with defaults fallback ([`ingest_metadata.defaults.ts`](src/app/shared/utils/ingest-metadata.defaults.ts))
- **Upload stream**: optional **Stream ID** plus **First chunk number** (default 1). Files in that dialog are numbered in list order (`start + position`). A later upload of the same id continues from the number you set. Up to 100 files per dialog. Explore uses that id for **Play chunk N/total** and Previous/Next.
- **Authentication**: VAST username + password → app JWT
- **Settings**: Advanced Search & AI, system prompt, streaming, batch sync
- **Blueprint diagram**: Settings → Show Blueprint Diagram (`src/assets/blueprint.html`)

## Configuration

| Endpoint | Purpose |
|----------|---------|
| `GET /api/v1/config` | Embedding/synthesis endpoints, `app.display_timezone`, default system prompt |
| `GET /api/v1/metadata/ingest-config` | Upload dialog field definitions |
| `GET /api/v1/metadata/schema` | Search filter columns |
| `GET /api/v1/suggestions` | Search prompts + key events (polls every 5 min on Search + Dashboard) |

User settings in browser `localStorage`: Advanced Search & AI, system prompt, bbox overlay preference, time filters.

## Stack

- Angular 18 · Angular Material · RxJS services · JWT in localStorage
- Shared utilities: `video-hover-preview.util.ts`, `detection-overlay.util.ts`, `time.util.ts`
- Deployed as static files behind Nginx — see [the site deploy](../../../deployments/vss-deploy/README.md) or `source-code/scripts/build-retrieval-images.sh`
