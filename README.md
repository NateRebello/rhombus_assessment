# Rhombus — Distributed CSV Pattern-Matching Platform

Upload a CSV or Excel file, describe a pattern in plain English, and the system
uses an LLM to generate a regex, applies it at scale via PySpark, and returns
paginated results.

## Setup and run

### Prerequisites

- Docker and Docker Compose
- An OpenAI API key

### Local development

```bash
cp .env.example .env
# Set OPENAI_API_KEY in .env

docker compose up --build
```

| URL | Purpose |
|-----|---------|
| http://localhost:3000 | React frontend |
| http://localhost:8000/api/ | Django REST API |

Migrations run automatically when the `web` container starts.

### Async / Spark stack

One `docker compose up --build` starts six services on a shared Docker network:

| Service | Role |
|---------|------|
| **web** | Django (DRF). Accepts uploads, writes files to a shared volume, creates a Job row in Postgres, enqueues Celery with **only the job ID**, returns **202 Accepted**. |
| **worker** | Celery worker. Loads job metadata from Postgres, calls the LLM (or Redis regex cache), runs **PySpark** on the shared upload file, writes Parquet results to a shared volume, updates job status/progress in Postgres. |
| **db** | Postgres — job metadata only (not file bytes). |
| **redis** | Celery broker, result backend, and LLM regex cache (sha256 of prompt). |
| **frontend** | React dev server (Vite). Polls `/api/jobs/{id}/status/` every 2 s until a terminal state. |

**Request flow:** Browser → Django → Celery (job_id) → Worker → LLM + Spark → Parquet on volume → Browser polls status → Django reads Parquet pages via PyArrow.

**Why async:** Uploads can be hundreds of MB and Spark jobs run for tens of seconds. The web process must not block on LLM or JVM work.

**Spark configuration:** Set in `.env` — `SPARK_DRIVER_MEMORY`, `SPARK_EXECUTOR_MEMORY`, `SPARK_MASTER_URL=local[*]`. The worker container has a 4 GB memory limit; tune Spark to stay under it.

### Production (single host)

See `docker-compose.prod.yml` and `.env.production.example`. Caddy serves the built React app and proxies `/api/*` to gunicorn on the same origin.

## Architecture

```
Browser (React)
    │  HTTP  (job create / status poll / result fetch)
    ▼
Django (DRF)  ──── Postgres (job metadata)
    │  apply_async(job_id)      ▲ status updates
    ▼                           │
Celery Worker ──── Redis (broker + regex cache)
    │  spark_jobs.transform
    ▼
PySpark  ──── Shared volume (uploads + Parquet results)
```

### Reasoning

| Decision | Why |
|----------|-----|
| Only `job_id` in Celery messages | File bytes can be hundreds of MB. Passing them through Redis would bloat the broker and couple message size to upload size. The worker re-reads from Postgres + the shared volume. |
| `spark_jobs/` isolated from Django | PySpark starts a JVM. Importing it in the web process would start a JVM on every gunicorn worker. The transform module can also be run standalone for debugging. |
| PySpark imported inside the Celery task | Avoids JVM startup on worker boot; only tasks that need Spark pay the cost. |
| Parquet output + paginated reads | Results can be millions of rows. Loading them into the web process would OOM. PyArrow reads one slice per page request. |
| Redis regex cache | LLM calls add latency and cost. Identical prompts (sha256 of normalised text) skip the LLM for ~1 hour (`REGEX_CACHE_TTL_SECONDS`). |
| Static ReDoS validation before Spark | Nested quantifiers and timeout-tested patterns are rejected so a bad LLM regex cannot hang the worker on large data. |
| Same-origin frontend + API in production | No CORS; the browser calls relative `/api/` paths on the same host Caddy serves. |

## Notes and trade-offs

**Excel uploads:** `.xlsx` files are read with pandas/openpyxl inside the worker and converted to a Spark DataFrame. Very large Excel files are slower and more memory-intensive than CSV; CSV is preferred at scale.

**Cancellation:** The cancel endpoint sets the job to CANCELLED and revokes the Celery task, but a task already running Spark in a JVM cannot be interrupted reliably. Cached jobs often finish before cancel takes effect.

**Regex cache:** Stored in Redis without persistence. `docker compose down` clears cached regexes.

**LLM variability:** Regex quality depends on prompt wording. Domain-specific prompts (e.g. “domain is company.com”) work better than vague suffix phrasing. Post-processing corrects common off-by-one phone patterns and apex-domain email mistakes.

**First result page latency:** The first paginated read of a large Parquet output scans file metadata; subsequent pages are faster.

**Testing:** No automated test suite is included. Manual verification steps are in `TESTING_CHECKLIST.md`. Sample files are in `test_data/` (CSV and Excel).
