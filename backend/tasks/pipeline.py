"""
Celery task: process_job

This module is the orchestration layer.  Its job is to:
  1. Update Job state in Postgres.
  2. Obtain a validated regex (from cache or LLM).
  3. Delegate data processing to spark_jobs.transform (a hard module boundary).
  4. Record the outcome back in Postgres.

What this module deliberately does NOT do:
  - Import pyspark at module level.  PySpark starts a JVM on import; doing
    that here would make every Celery worker boot ~5–10 seconds slower and
    would fail if Java is not in PATH at worker startup on some machines.
    Instead, spark_jobs.transform is imported inside the task body.
  - Touch file bytes.  All file I/O is delegated to spark_jobs.transform.
  - Handle HTTP concerns.  This is a pure Celery task; it knows nothing about
    request/response cycles.

Retry policy:
  Retryable:  LLM timeouts / connection errors (transient infrastructure).
  Terminal:   Invalid file, column not found, bad regex (the same input will
              always fail — retrying wastes resources and delays user feedback).
              These are caught, logged, and stored in Job.error_message without
              calling self.retry().
"""
import logging

from celery import shared_task
from django.utils import timezone as dj_timezone
from openai import APIConnectionError, APITimeoutError

from jobs.models import Job, JobStatus
from tasks.llm_regex import (
    ALLOWED_NORMALIZE,
    RegexSafetyError,
    generate_regex,
    generate_transform_spec,
)
from tasks.retry_policy import (
    MAX_RETRIES,
    RETRY_BACKOFF_MAX,
)

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    # Automatic retries for retryable exceptions.
    # retry_backoff=True activates Celery's built-in exponential backoff.
    # retry_backoff_max caps the delay so retries never wait more than 5 min.
    # max_retries prevents infinite retry loops.
    autoretry_for=(APITimeoutError, APIConnectionError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    # Use task id = job_id so Celery's task store and our Job table share a key.
    # Set via .apply_async(task_id=str(job_id)) or .delay() with the task id
    # embedded at dispatch time.
    acks_late=True,  # ack only after the task body completes (safer with Redis)
)
def process_job(self, job_id: str) -> dict:
    """
    Orchestrate end-to-end processing for a single job.

    Args:
        job_id: UUID string matching a Job row in Postgres.

    Returns:
        dict with keys: job_id, status, row_count (for Celery result backend).
    """
    logger.info("process_job started: %s (attempt %d/%d)", job_id, self.request.retries + 1, MAX_RETRIES + 1)

    # ── 1. Load the Job row ───────────────────────────────────────────────────
    try:
        job = Job.objects.get(id=job_id)
    except Job.DoesNotExist:
        # The job was deleted between dispatch and execution — nothing to do.
        logger.error("Job %s not found in database; aborting.", job_id)
        return {"job_id": job_id, "status": "NOT_FOUND"}

    # Honour cancellation requests that arrived before the task started.
    if job.status == JobStatus.CANCELLED:
        logger.info("Job %s was cancelled before execution; skipping.", job_id)
        return {"job_id": job_id, "status": JobStatus.CANCELLED}

    # ── 2. Mark as RUNNING ────────────────────────────────────────────────────
    job.status = JobStatus.RUNNING
    job.started_at = dj_timezone.now()
    job.progress = 0
    job.save(update_fields=["status", "started_at", "progress", "updated_at"])

    try:
        # ── 3. Generate / fetch regex (or transform spec) ─────────────────────
        # RegexSafetyError is terminal — do not retry.
        # APITimeoutError / APIConnectionError are retryable and handled by
        # autoretry_for above (Celery re-raises them after backoff).
        _set_progress(job, 10)

        normalize_mode = job.normalize_mode or "none"
        # Secondary guard: if an invalid value somehow slipped past the
        # serialiser, fall back to "none" rather than crashing.
        if normalize_mode not in ALLOWED_NORMALIZE:
            logger.warning("Job %s: unknown normalize_mode %r, falling back to 'none'", job_id, normalize_mode)
            normalize_mode = "none"

        if normalize_mode == "none":
            regex = generate_regex(job.prompt)
        else:
            spec = generate_transform_spec(job.prompt)
            regex = spec["pattern"]
            # Trust the user's explicit UI choice over the LLM's inferred mode.
            # The LLM's normalize field is informational; the Job row is authoritative.
            logger.info(
                "Job %s: standardise mode=%r  LLM suggested normalize=%r",
                job_id, normalize_mode, spec.get("normalize"),
            )

        logger.info("Job %s using regex: %r  normalize: %r", job_id, regex, normalize_mode)

        # Check if cancelled between LLM call and Spark dispatch.
        job.refresh_from_db(fields=["status"])
        if job.status == JobStatus.CANCELLED:
            logger.info("Job %s cancelled after LLM call; aborting.", job_id)
            return {"job_id": job_id, "status": JobStatus.CANCELLED}

        # Store the regex for auditability even though it's also in Redis cache.
        job.generated_regex = regex
        job.save(update_fields=["generated_regex", "updated_at"])

        # ── 4. Delegate to PySpark ────────────────────────────────────────────
        _set_progress(job, 20)

        from spark_jobs.transform import run as spark_run

        result = spark_run(
            file_path=job.file_path,
            target_column=job.target_column,
            regex=regex,
            replacement=job.replacement_value,
            result_base_dir=_result_dir_for_job(job_id),
            progress_callback=lambda pct: _set_progress(job, 20 + int(pct * 0.78)),
            normalize=normalize_mode,
        )

        # ── 5. Persist success ────────────────────────────────────────────────
        job.status = JobStatus.SUCCESS
        job.progress = 100
        job.result_path = result["result_path"]
        job.row_count = result["row_count"]
        job.completed_at = dj_timezone.now()
        job.save(
            update_fields=[
                "status", "progress", "result_path", "row_count",
                "completed_at", "updated_at",
            ]
        )
        logger.info("Job %s completed: %d rows → %s", job_id, result["row_count"], result["result_path"])
        return {"job_id": job_id, "status": JobStatus.SUCCESS, "row_count": result["row_count"]}

    except RegexSafetyError as exc:
        # Terminal — the same prompt will always produce an unsafe regex.
        _fail_job(job, f"Regex safety check failed: {exc}")
        return {"job_id": job_id, "status": JobStatus.FAILED}

    except (ValueError, KeyError, FileNotFoundError) as exc:
        # Terminal — bad input data (wrong column name, corrupt file, etc.).
        _fail_job(job, str(exc))
        return {"job_id": job_id, "status": JobStatus.FAILED}

    except Exception as exc:
        logger.exception("Unexpected error in process_job %s", job_id)
        # Only re-raise (triggering Celery's autoretry_for backoff) for
        # exceptions that are both retryable AND have retries remaining.
        # For everything else — including non-retryable unknown exceptions —
        # we must call _fail_job() so the Job row doesn't stay stuck in
        # RUNNING state forever.  Without this guard, an OperationalError
        # from a flaky DB write would leave the job orphaned.
        is_retryable = isinstance(
            exc, (APITimeoutError, APIConnectionError, ConnectionError, TimeoutError)
        )
        if is_retryable and self.request.retries < MAX_RETRIES:
            raise  # Celery's autoretry_for will schedule the next retry
        _fail_job(
            job,
            f"Max retries exceeded: {exc}" if self.request.retries >= MAX_RETRIES else str(exc),
        )
        return {"job_id": job_id, "status": JobStatus.FAILED}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _set_progress(job: Job, pct: int) -> None:
    """Persist progress percentage without touching other fields."""
    job.progress = min(max(pct, 0), 99)  # never write 100 until fully done
    job.save(update_fields=["progress", "updated_at"])


def _fail_job(job: Job, message: str) -> None:
    logger.error("Job %s FAILED: %s", job.id, message)
    job.status = JobStatus.FAILED
    job.error_message = message[:2000]  # guard against enormous tracebacks
    job.completed_at = dj_timezone.now()
    job.save(update_fields=["status", "error_message", "completed_at", "updated_at"])


def _result_dir_for_job(job_id: str) -> str:
    from django.conf import settings
    from pathlib import Path

    path = Path(settings.RESULT_DIR) / job_id
    path.mkdir(parents=True, exist_ok=True)
    return str(path)
