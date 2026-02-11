# App Creator

Self-hosted service for generating and deploying static web apps from natural-language briefs.

- Frontend: Vue 3 dashboard
- Backend: Flask API + RQ worker
- Queue/session store: Redis
- Job metadata: SQLite
- Deployment target: user's own GitHub account + GitHub Pages

## Architecture

- `web` serves the Vue UI and `/api/*` routes.
- `worker` processes queued jobs and performs generation/deployment.
- `redis` stores queue data and ephemeral session credentials.

Sensitive data policy:
- GitHub App user token and LLM API key are stored only in Redis with TTL.
- Secrets are not persisted in SQLite.
- Job secret snapshots are deleted after job completion/failure.

## Core Flow

1. Browser gets a session cookie from `/api/session`.
2. User connects GitHub via GitHub App user authorization.
3. User configures LLM provider (currently Perplexity) using an API key.
4. User submits a job (brief + optional attachments).
5. Worker generates files with the LLM, creates a GitHub repo, pushes code, enables Pages.
6. UI polls job status and event logs.

## API (v1)

- `GET /api/health`
- `GET /api/session`
- `POST /api/session/reset`
- `GET /api/integrations`
- `POST /api/integrations/llm`
- `GET /api/auth/github/start`
- `GET /api/auth/github/callback`
- `POST /api/auth/github/disconnect`
- `POST /api/jobs`
- `GET /api/jobs`
- `GET /api/jobs/<job_id>`
- `GET /api/jobs/<job_id>/events?after=<id>`
- `GET /api/jobs/<job_id>/download` (for ZIP delivery jobs)

`POST /api/jobs` expects `multipart/form-data`:
- `payload`: JSON string
- `files`: zero or more attachments

Payload schema:

```json
{
  "title": "my-app",
  "brief": "Build a static app that ...",
  "delivery_mode": "github",
  "repo": {
    "name": "my-app",
    "visibility": "public"
  },
  "deployment": {
    "enable_pages": true,
    "branch": "main",
    "path": "/"
  }
}
```

`delivery_mode` options:
- `github`: deploy to connected GitHub account (requires GitHub integration)
- `zip`: generate downloadable ZIP package (GitHub not required)

## Local Development

### 1) Backend

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set GitHub App values in `.env`:
- `GITHUB_APP_CLIENT_ID`
- `GITHUB_APP_CLIENT_SECRET`
- `GITHUB_APP_CALLBACK_URL`
- optional: `GITHUB_APP_SLUG` (enables the frontend "Install App" button)

Run Redis locally, then:

```bash
source .venv/bin/activate
python worker.py
```

In another shell:

```bash
source .venv/bin/activate
gunicorn --bind 0.0.0.0:8000 wsgi:app
```

### 2) Frontend

```bash
cd frontend
npm install
npm run dev
```

Vite proxies `/api` to `http://localhost:8000`.

## Docker Deployment

Use Docker Compose (recommended):

```bash
cp .env.example .env
# update .env secrets first
docker compose up --build -d
```

Services:
- Web API + frontend: `http://localhost:8000`
- Worker: background queue processor
- Redis: ephemeral in-memory store (persistence disabled)

## GitHub App Setup

Create a GitHub App with user authorization enabled:
- Homepage URL: your app URL (example `http://localhost:8000`)
- Callback URL: `http://localhost:8000/api/auth/github/callback`
- Permissions: repository write permissions required for creating repos, pushing code, and managing Pages

Then copy:
- App client ID -> `GITHUB_APP_CLIENT_ID`
- App client secret -> `GITHUB_APP_CLIENT_SECRET`
- Callback URL -> `GITHUB_APP_CALLBACK_URL`
- App slug -> `GITHUB_APP_SLUG` (optional)

For production behind Cloudflare, set callback URL to your public domain path.

## Project Structure

- `app/` Flask app, services, storage, workflows
- `frontend/` Vue app
- `worker.py` RQ worker entrypoint
- `wsgi.py` web entrypoint
- `docker-compose.yml` multi-container runtime
