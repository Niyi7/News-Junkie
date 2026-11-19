# Drift news aggregator (v0.2)

FastAPI + feedparser + SQLite (FTS5 search). Serves the Drift UI at `/`.
Fetches every feed in `feeds.py` on startup and every 15 minutes, dedupes by link,
keeps 14 days of articles, and exposes a JSON API.

## Run locally
    python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
    pip install -r requirements.txt
    uvicorn main:app --reload
Open http://localhost:8000. The first stories appear within a few seconds.

## API
- `GET /api/articles?category=&source=&q=&limit=30&offset=0`
- `GET /api/meta`  `POST /api/refresh`  `GET /healthz`  `GET /api/status` (per-feed HTTP status, entry counts, errors)

## Deploy
Environment: `DATA_DIR` = folder for `news.db` (mount a persistent volume there), `PORT` is set by the host.

### Option A: Render (easiest)
1. Push this folder to a GitHub repo.
2. Render dashboard → New → Blueprint → pick the repo (it reads `render.yaml`).
3. Free plan sleeps when idle and loses the SQLite file on redeploy; stories refill on wake.
   For always-on + persistence: plan `starter` and uncomment the `disk` block.

### Option B: Fly.io (persistent, always-on, about a few dollars a month)
    fly launch --no-deploy --copy-config     # accept fly.toml, rename the app
    fly volumes create drift_data --size 1 --region fra
    fly deploy
Open the URL it prints. `fly logs` shows `[refresh] N new articles` every 15 minutes.

### Option C: Railway / any Docker host
The `Dockerfile` works as-is. Mount a volume at `/data`.

## Customize
Edit `feeds.py` (one line per RSS/Atom source), redeploy.

## Next (roadmap phases 2+)
Cluster duplicate stories, rank by coverage and recency, accounts + synced saves,
digest emails, Postgres + worker when traffic grows.
