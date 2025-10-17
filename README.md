# LLM Code Deployment Service

Flask-based service that receives structured task briefs, generates web apps with Perplexity, deploys them to GitHub Pages, and notifies an evaluation API. A manual testing harness is included for smoke-testing requests before sharing the endpoint.

## Architecture
- **Flask API** exposed at `/tasks` for round 1 and 2 task submissions.
- **RQ worker + Redis** handle LLM generation and deployment asynchronously.
- **SQLite** persists task metadata and job status.
- **Perplexity API** produces a JSON manifest describing generated files.
- **GitHub REST + Git CLI** create a unique repository per task, push generated assets, and enable GitHub Pages.
- **Evaluation callback** posts results back to the provided URL with repository metadata.

## Prerequisites
- Ubuntu LTS VM (provisioned manually on Linode Nano Node).
- Python 3.11+, git, Redis, nginx (optional reverse proxy).
- GitHub Personal Access Token with `repo`, `workflow`, and `pages` scopes.
- Perplexity API key (Pro account).

## Quick Start
1. **Clone** this repository onto the new VM:
   ```bash
   git clone https://github.com/aniketshedge/tds-project-1-app-creator
   cd tds-project-1-app-creator
   ```
2. **Bootstrap** the machine:
   ```bash
   chmod +x setup.sh
   ./setup.sh
   source .venv/bin/activate
   ```
3. **Configure secrets**:
   - Edit `.env` (copied from `.env.example`) with:
     - `ACCEPTED_SECRET`
     - `PERPLEXITY_API_KEY`
     - `GITHUB_TOKEN`
     - `GITHUB_USERNAME`, `GITHUB_EMAIL`, optional `GITHUB_ORG`
   - Adjust optional defaults (`REQUEST_TIMEOUT_SECONDS`, `MAX_RETRIES`, paths).
4. **Start services** (development view):
    ```bash
    source .venv/bin/activate
    nohup python worker.py > worker.log 2>&1 &  # background worker
    gunicorn --bind 0.0.0.0:8000 wsgi:app
    ```
  When ready for production, wrap `gunicorn` and `worker.py` with systemd units and (optionally) place nginx in front.

## API Usage
### POST `/tasks`
Submit JSON matching the brief structure (see `notes/project-llm-code-deployment.md`). Example payload:
```jsonc
{
  "email": "student@example.com",
  "secret": "<ACCEPTED_SECRET>",
  "task": "sum-of-sales",
  "round": 1,
  "nonce": "abc123",
  "brief": "Publish a single-page site...",
  "checks": ["final HTML contains #total-sales"],
  "evaluation_url": "https://example.com/notify",
  "attachments": [
    { "name": "data.csv", "url": "data:text/csv;base64,..." }
  ]
}
```

**Responses**
- `200 OK` with `{"job_id": "...", "status": "queued"}` when enqueued.
- `400 Bad Request` on validation issues.
- `403 Forbidden` if the `secret` mismatches.

### GET `/tasks/<job_id>`
Returns persisted job metadata (status, repository URLs, error messages if any).

## Job Flow
1. Request persisted to SQLite with status `queued`.
2. RQ worker pulls job, validates attachments (≤ 1 MB each), and requests a manifest from Perplexity.
3. Workspace is assembled from manifest + attachments; optional commands run.
4. GitHub repo name is generated from task ID, ensuring uniqueness (e.g., `sum-of-sales-a1b2c3`). Round > 1 jobs reuse the same repository and force-push updates.
5. Git CLI pushes initial commit, GitHub Pages enabled from `main`.
6. Evaluation callback receives repo URL, commit SHA, and Pages URL.
7. Status transitions: `queued` → `in_progress` → `deployed` → `completed` (or `failed` with error details).

## Testing Harness
Open `testing/index.html` locally in a browser. It provides:
- Form inputs for API URL, secret, and all payload fields.
- Prefilled defaults using Sample 1 (`sum-of-sales`) from `notes/task-brief.md`.
- Inline JavaScript to POST to the API and display raw JSON responses.

## Logging & Data
- Requests/responses (secret redacted) and workflow steps log to `server.log` (path configurable via `.env`).
- SQLite database path defaults to `./data/tasks.db`.
- Temporary build artifacts stored under `/tmp/task-runner/<job_id>` and removed after each job.

## Maintenance Notes
- Keep `.env` outside of version control; edit via SSH using your preferred editor.
- Run Redis locally on the same VM. A lightweight background worker (`python worker.py`) is sufficient.
- The service currently operates over HTTP; front with nginx/reverse proxy as needed.
- `notes/` is ignored by git—use it only for planning, not runtime assets.
- When updating this service itself, pull latest changes, reactivate the virtualenv, rerun `pip install -r requirements.txt` if dependencies changed, restart `worker.py` and your `gunicorn` process, and redeploy nginx config if modified.
- To restart quickly, terminate old processes and relaunch:
  ```bash
  source .venv/bin/activate
  pkill -f "python worker.py" || true
  pkill -f "gunicorn" || true
  nohup python worker.py > worker.log 2>&1 &
  nohup gunicorn --bind 0.0.0.0:8000 wsgi:app > gunicorn.log 2>&1 &
  ```

## Updating Generated Apps (Round ≥ 2)
- The API automatically detects existing repositories for a task and reuses them during later rounds.
- Round 2+ briefs trigger a fresh Perplexity run; the worker force-pushes updated assets to the original GitHub repository and reuses GitHub Pages.
- To perform manual tweaks after automation (if ever needed), clone the generated repo locally, apply fixes, `git commit`, and `git push origin main` to redeploy Pages.
