# Rhombus — Distributed CSV Pattern-Matching Platform

A production-grade web app where users upload a CSV/Excel file, describe a text
pattern in natural language, and the system uses an LLM to generate a regex,
applies it at scale via PySpark, and returns paginated results.

## Architecture

```
Browser (React)
    │  HTTP  (job create / status poll / result fetch)
    ▼
Django (DRF)  ──── Postgres (job metadata)
    │  .delay(job_id)           ▲ status updates
    ▼                           │
Celery Worker ──── Redis (broker + result backend + regex cache)
    │  subprocess / module call
    ▼
PySpark (transform.py)  ──── Shared volume (uploads + Parquet results)
```

### Key architectural decisions

| Boundary | Why |
|---|---|
| Only `job_id` crosses the Celery message | File bytes can be hundreds of MB. Passing them through Redis would blow the broker's memory limit and make the message non-inspectable. The worker re-reads everything it needs from Postgres + the shared volume. |
| `spark_jobs/` is a standalone module | PySpark starts a JVM. If that import were inside a Django view it would blow up the web process. Isolating it means you can also run `transform.py` standalone for debugging without touching Django. |
| Parquet output + partition reads | Results can be billions of rows. Loading them into memory to serve a page would OOM the web process. Reading one row-group at a time via PyArrow keeps the web process lean. |
| Redis regex cache (sha256 of prompt) | LLM calls cost money and add latency. Identical prompts should never hit the LLM twice. |

## Quick Start

```bash
cp .env.example .env
# Fill in OPENAI_API_KEY in .env

docker compose up --build
# Frontend: http://localhost:3000
# API:      http://localhost:8000/api/
# Admin:    http://localhost:8000/admin/  (create superuser below)

docker compose exec web python manage.py createsuperuser
```

## Generating test data

```bash
cd test_data
python generate_large_csv.py --rows 5000000 --out sample.csv
```

## Services

| Service | Port | Description |
|---|---|---|
| `web` | 8000 | Django REST API |
| `frontend` | 3000 | React dev server (Vite) |
| `db` | — | Postgres 16 (internal only) |
| `redis` | — | Redis 7 (internal only) |
| `worker` | — | Celery worker (Spark runs here) |

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/jobs/` | Upload file, create job → 202 |
| `GET` | `/api/jobs/{id}/status/` | Poll job status + progress |
| `GET` | `/api/jobs/{id}/results/?page=1` | Paginated Parquet results |
| `POST` | `/api/jobs/{id}/cancel/` | Request cancellation |

## Development

```bash
# Run Django tests
docker compose exec web python manage.py test

# Inspect Celery tasks
docker compose exec worker celery -A config inspect active

# Connect to Postgres
docker compose exec db psql -U rhombus rhombus
```
