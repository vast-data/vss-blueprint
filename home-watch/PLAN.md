# Three-camera attention board (UI + thin backend)

**Backend is in scope. User-facing login is not.** The browser talks only to our app. Our app talks to VSS (login, explore, search, stream) using team creds from the deploy Secret. No username/password screen.

Work out of `home-watch/` in this repo. Do **not** modify the official Angular retrieval frontend under `source-code/retrieval/`.

```mermaid
flowchart LR
  UI[Next.js_frontend]
  App[Flask_backend]
  VSS[Team_VSS]
  UI -->|GET /app/api/cameras /alerts /clip| App
  App -->|login explore search stream JWT| VSS
```

## Layout

- `home-watch/backend/` — Flask API (`main.py`). Does not serve UI.
- `home-watch/frontend/` — Next.js App Router (`basePath: /app`). Local `next dev` rewrites `/app/api/*` and `/app/health` to Flask on `:8080`.

## Backend

Flask (`backend/main.py`) on `0.0.0.0:$PORT`. Env: `VSS_URL`, `VSS_USERNAME`, `VSS_PASSWORD`. Cache a JWT; re-login on 401. Do not log tokens or passwords.

| Route | Behavior |
|-------|----------|
| `GET /health` | Liveness |
| `GET /api/cameras` | Latest indexed clip per pinned `camera_id` via Explore; returns labels + clip source |
| `GET /api/alerts` | Two VSS searches, merged: house presence (`neighborhood_cam-1`), dashcam hazard (`pie_cam-3`) |
| `GET /api/clip` | Proxy `GET /api/v1/videos/stream?source=…&token=JWT` so the `<video>` tag never calls VSS with a raw token |

There is no VSS `/alerts` route. Alerts are our ranking of search `chunk_results` (`reasoning_content`, timestamps, camera, clip). JWT stays on the server (clip URL is our route).

Optional later: `POST /agent/search-and-answer` for a one-line blurb. v1 stays on search so the page stays fast.

## UX

```text
[ Home Watch          team-44  healthy ]
[ Indoor ceiling ] [ Dashcam ] [ House exterior ]
     player            player        player
[ Needs attention ]
  - House · person walking dog on sidewalk · clip
  - Car   · pedestrian crossing while SUV braking · clip
```

- Three cards: Indoor / In the car / Outside the house.
- Alert feed from `/api/alerts`; click loads that segment on the matching camera.
- Indoor is display-only in v1 (no occupancy alerts).
- Dark compact ops-board. One page; opens straight into cameras.

Pinned cameras:

| Card | `camera_id` | `location` | Seed clip |
|------|-------------|------------|-----------|
| Indoor | `smartspace_cam-1` | `indoor` | `...Warehouse_017_Camera_chunk_0009.mp4` |
| Dashcam | `pie_cam-3` | `toronto` | `...set06_video_chunk_0014.mp4` |
| House | `neighborhood_cam-1` | `neighborhood` | `...neighborhood_20260901_chunk_0007.mp4` |

Alert queries (server-side):

- House: `camera_id=neighborhood_cam-1`, person / dog / vehicle at driveway or sidewalk
- Dashcam: `camera_id=pie_cam-3`, pedestrian in roadway, brake lights, road work, sudden stop

## Deploy

Follow the deploy-app-no-registry skill. Backend: ConfigMap of `home-watch/backend/`, Secret from `/config/team-44.config`, Deployment `python:3.12-slim`. Frontend: Next.js (`node:22-slim`) with Ingress `/app` on `video-lab-team-44.cosmos.vastdata.com`. Namespace `team-44`. Kubeconfig: `/config/team-44-k8s.yaml`. Locate `kubectl` before apply. Do not put `node_modules` or `.next` in a ConfigMap. Flask + `requests` on the API; Next.js is the UI.

Public URL: `http://video-lab-team-44.cosmos.vastdata.com/app`

## Out of scope

- Login / signup UI, user accounts, or SSO
- Chat / agent ask box
- Indoor occupancy alerts
- Push / SMS
- Re-ingest or new home-interior footage
- Editing the official VSS Angular app
