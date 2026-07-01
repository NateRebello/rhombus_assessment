"""Celery task orchestration: LLM regex generation → Spark transform → Job status updates.

PySpark is imported inside process_job(), not at module level, to avoid starting a JVM
on every worker boot. Celery messages carry only job_id — file bytes stay on the volume.
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
from tasks.retry_policy import MAX_RETRIES, RETRY_BACKOFF_MAX

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(APITimeoutError, APIConnectionError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def process_job(self, job_id: str) -> dict:
    logger.info(
        "process_job started: %s (attempt %d/%d)",
        job_id, self.request.retries + 1, MAX_RETRIES + 1,
    )

    try:
        job = Job.objects.get(id=job_id)
    except Job.DoesNotExist:
        logger.error("Job %s not found in database; aborting.", job_id)
        return {"job_id": job_id, "status": "NOT_FOUND"}

    if job.status == JobStatus.CANCELLED:
        logger.info("Job %s was cancelled before execution; skipping.", job_id)
        return {"job_id": job_id, "status": JobStatus.CANCELLED}

    job.status = JobStatus.RUNNING
    job.started_at = dj_timezone.now()
    job.progress = 0
    job.save(update_fields=["status", "started_at", "progress", "updated_at"])

    try:
        _set_progress(job, 10)

        normalize_mode = job.normalize_mode or "none"
        if normalize_mode not in ALLOWED_NORMALIZE:
            logger.warning(
                "Job %s: unknown normalize_mode %r, falling back to 'none'",
                job_id, normalize_mode,
            )
            normalize_mode = "none"

        if normalize_mode == "none":
            regex = generate_regex(job.prompt)
        else:
            spec = generate_transform_spec(job.prompt)
            regex = spec["pattern"]
            logger.info(
                "Job %s: standardise mode=%r  LLM suggested normalize=%r",
                job_id, normalize_mode, spec.get("normalize"),
            )

        logger.info("Job %s using regex: %r  normalize: %r", job_id, regex, normalize_mode)

        job.refresh_from_db(fields=["status"])
        if job.status == JobStatus.CANCELLED:
            logger.info("Job %s cancelled after LLM call; aborting.", job_id)
            return {"job_id": job_id, "status": JobStatus.CANCELLED}

        job.generated_regex = regex
        job.save(update_fields=["generated_regex", "updated_at"])

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
        _fail_job(job, f"Regex safety check failed: {exc}")
        return {"job_id": job_id, "status": JobStatus.FAILED}

    except (ValueError, KeyError, FileNotFoundError) as exc:
        _fail_job(job, str(exc))
        return {"job_id": job_id, "status": JobStatus.FAILED}

    except Exception as exc:
        logger.exception("Unexpected error in process_job %s", job_id)
        # Re-raise only retryable errors with retries remaining; otherwise mark FAILED
        # so the Job row does not stay stuck in RUNNING forever.
        is_retryable = isinstance(
            exc, (APITimeoutError, APIConnectionError, ConnectionError, TimeoutError)
        )
        if is_retryable and self.request.retries < MAX_RETRIES:
            raise
        _fail_job(
            job,
            f"Max retries exceeded: {exc}" if self.request.retries >= MAX_RETRIES else str(exc),
        )
        return {"job_id": job_id, "status": JobStatus.FAILED}


def _set_progress(job: Job, pct: int) -> None:
    job.progress = min(max(pct, 0), 99)
    job.save(update_fields=["progress", "updated_at"])


def _fail_job(job: Job, message: str) -> None:
    logger.error("Job %s FAILED: %s", job.id, message)
    job.status = JobStatus.FAILED
    job.error_message = message[:2000]
    job.completed_at = dj_timezone.now()
    job.save(update_fields=["status", "error_message", "completed_at", "updated_at"])


def _result_dir_for_job(job_id: str) -> str:
    from django.conf import settings
    from pathlib import Path

    path = Path(settings.RESULT_DIR) / job_id
    path.mkdir(parents=True, exist_ok=True)
    return str(path)
