# App Creator

Self-hosted service for generating and deploying static web apps from natural-language briefs.

- Frontend: Vue 3 dashboard
- Backend: Flask API + RQ worker
- Queue/session store: Redis
- Job metadata: SQLite
- Deployment target: user's own GitHub account + GitHub Pages
- Supported LLM providers: Perplexity, AI Pipe, OpenAI, Anthropic, Gemini

## Architecture

- `web` serves the Vue UI and API routes.
- `worker` processes queued jobs and performs generation/deployment.
- `redis` stores queue data and ephemeral session credentials.

Base-path support:
- Set `APP_BASE_PATH=/` (default) for root hosting.
- Set `APP_BASE_PATH=/app-creator` for subpath hosting.
- Build frontend with matching `VITE_APP_BASE_PATH` (same value as `APP_BASE_PATH`).

Sensitive data policy:
- GitHub App user token and LLM API key are stored only in Redis with TTL.
- Secrets are not persisted in SQLite.
- Job secret snapshots are deleted after job completion/failure.

## Core Flow

1. Browser gets a session cookie from the configured base path API endpoint.
2. User optionally connects GitHub via GitHub App user authorization.
3. User configures one LLM provider/model at a time using a session-scoped API key.
4. User submits a build job (title + app description).
5. Worker generates files and stores a downloadable ZIP artifact.
6. User can download the ZIP and/or trigger GitHub deployment from the same build.
7. UI polls job status and event logs.

## API (v1)

If `APP_BASE_PATH=/app-creator`, all endpoints are served under `/app-creator` (for example `/app-creator/api/health`).

- `GET /api/health`
- `GET /api/session`
- `POST /api/session/reset`
- `GET /api/integrations`
- `GET /api/integrations/llm/catalog`
- `POST /api/integrations/llm`
- `GET /api/auth/github/start`
- `GET /api/auth/github/callback`
- `POST /api/auth/github/disconnect`
- `POST /api/jobs`
- `POST /api/jobs/<job_id>/deploy`
- `POST /api/jobs/<job_id>/preview`
- `GET /api/jobs`
- `GET /api/jobs/<job_id>`
- `GET /api/jobs/<job_id>/events?after=<id>`
- `GET /api/jobs/<job_id>/download`
- `GET /preview/<token>/...`

`POST /api/jobs` expects JSON:

Payload schema:

```json
{
  "title": "my-app",
  "brief": "Build a static app that ..."
}
```

`POST /api/jobs/<job_id>/deploy` expects JSON:

```json
{
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

`POST /api/jobs/<job_id>/preview` creates a static preview URL from the generated ZIP build.
- Preview is static-file only (no server-side build/runtime execution)
- Preview URL expires automatically after `PREVIEW_TTL_SECONDS` (default `3600`)

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
- optional: `APP_BASE_PATH` (use `/app-creator` for subpath hosting)
- optional: `VITE_APP_BASE_PATH` (must match `APP_BASE_PATH` when building frontend assets)
- optional: `CORS_ALLOWED_ORIGINS` (comma-separated origins only if you intentionally need cross-origin API access)

For public same-origin hosting behind a reverse proxy, keep CORS disabled:
- `CORS_ALLOWED_ORIGINS=`
- `CORS_ALLOW_ORIGIN=`

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

Vite proxies `${VITE_APP_BASE_PATH}/api` to `http://localhost:8000`.

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
Example with subpath hosting: `https://apps.example.com/app-creator/api/auth/github/callback`.

## Project Structure

- `app/` Flask app, services, storage, workflows
- `frontend/` Vue app
- `worker.py` RQ worker entrypoint
- `wsgi.py` web entrypoint
- `docker-compose.yml` multi-container runtime
