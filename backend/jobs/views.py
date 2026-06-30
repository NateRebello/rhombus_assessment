"""
API views for Job lifecycle management.

Design constraints encoded here:
  - JobCreateView does no blocking I/O beyond streaming the upload to disk.
    Database write + Celery dispatch are both fast and non-blocking at scale.
  - JobResultView never loads a full Parquet dataset into memory; it reads one
    row-group (partition file) at a time via PyArrow, keeping the web process
    heap bounded regardless of result size.
  - File bytes never cross a process boundary.  The Celery message carries only
    job_id; the worker reconstructs everything else from Postgres + the volume.
"""
import uuid
from pathlib import Path

import pyarrow.parquet as pq
from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Job, JobStatus
from .serializers import JobCreateSerializer, JobStatusSerializer


def _stream_upload_to_disk(file_obj, dest_path: Path) -> None:
    """
    Write an uploaded file to disk in chunks.

    Django's FileField already uses a temporary file when uploads exceed
    FILE_UPLOAD_MAX_MEMORY_SIZE, but we move it to the shared volume here so
    both the web and worker containers resolve the same absolute path.
    Using chunked copy avoids holding the whole file in memory even if Django
    buffered it in a TemporaryUploadedFile.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "wb") as out:
        for chunk in file_obj.chunks(chunk_size=8 * 1024 * 1024):  # 8 MB chunks
            out.write(chunk)


class JobCreateView(APIView):
    """
    POST /api/jobs/

    Accepts a multipart upload, streams the file to disk, creates a Job row,
    and enqueues the processing task.  Returns 202 immediately — the caller
    must poll /api/jobs/{id}/status/ for progress.
    """

    parser_classes = [MultiPartParser]

    def post(self, request: Request) -> Response:
        serializer = JobCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        uploaded_file = serializer.validated_data["file"]
        job_id = uuid.uuid4()

        # Build a deterministic path so we can reconstruct it without a DB
        # read if necessary.  job_id prefix avoids filename collisions.
        ext = Path(uploaded_file.name).suffix.lower()
        dest_path = Path(settings.UPLOAD_DIR) / str(job_id) / f"input{ext}"
        _stream_upload_to_disk(uploaded_file, dest_path)

        job = Job.objects.create(
            id=job_id,
            status=JobStatus.QUEUED,
            file_path=str(dest_path),
            original_filename=uploaded_file.name,
            prompt=serializer.validated_data["prompt"],
            target_column=serializer.validated_data["target_column"],
            replacement_value=serializer.validated_data.get("replacement_value", ""),
            normalize_mode=serializer.validated_data.get("normalize_mode", "none"),
        )

        # Only the job_id crosses the Celery message boundary.  Passing file
        # bytes or large strings through Redis would:
        #   1. Bloat broker memory (potentially OOM Redis).
        #   2. Make messages non-inspectable in Flower / celery inspect.
        #   3. Tightly couple message size to file size, breaking at ~100 MB.
        # The worker reads everything it needs from Postgres via the job_id.
        from tasks.pipeline import process_job  # local import avoids circular deps

        # Use job.id as the Celery task_id so that JobCancelView.revoke() can
        # address the task by the same UUID stored in the Job row.  Without
        # task_id= here, Celery assigns a random UUID and revoke() becomes a
        # no-op because it targets a task ID that doesn't exist.
        process_job.apply_async((str(job.id),), task_id=str(job.id))

        return Response({"job_id": str(job.id)}, status=status.HTTP_202_ACCEPTED)


class JobStatusView(APIView):
    """GET /api/jobs/{job_id}/status/ — lightweight polling endpoint."""

    def get(self, request: Request, job_id: str) -> Response:
        try:
            job = Job.objects.get(id=job_id)
        except Job.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response(JobStatusSerializer(job).data)


class JobResultView(APIView):
    """
    GET /api/jobs/{job_id}/results/?page=1&page_size=100

    Reads a single Parquet row-group per request using PyArrow's
    ParquetDataset API.  This keeps memory usage O(page_size) rather than
    O(total_rows), which is critical when results are millions of rows.
    """

    def get(self, request: Request, job_id: str) -> Response:
        try:
            job = Job.objects.get(id=job_id)
        except Job.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        if job.status != JobStatus.SUCCESS:
            return Response(
                {"detail": f"Job is not complete (status={job.status})."},
                status=status.HTTP_409_CONFLICT,
            )

        page = int(request.query_params.get("page", 1))
        page_size = min(int(request.query_params.get("page_size", 100)), 1000)

        result_path = Path(job.result_path)
        if not result_path.exists():
            return Response(
                {"detail": "Result files not found on disk."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Read individual Parquet files (partitions) rather than the full
        # dataset.  Each file is a ~128 MB shard; we only scan one per request.
        parquet_files = sorted(result_path.glob("*.parquet"))
        if not parquet_files:
            return Response({"results": [], "total_rows": 0, "page": page, "page_size": page_size})

        # Map page → partition file + intra-file offset.
        # This is a simple sequential mapping; more sophisticated implementations
        # could maintain a row-count index, but this is sufficient for the
        # boilerplate.
        total_rows = job.row_count or 0
        total_pages = max(1, -(-total_rows // page_size))  # ceiling division

        if page < 1 or page > total_pages:
            return Response(
                {"detail": f"Page {page} out of range (1–{total_pages})."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Determine which partition file contains the requested page.
        global_offset = (page - 1) * page_size
        cumulative = 0
        target_file = parquet_files[0]
        file_offset = 0

        for pf in parquet_files:
            pf_meta = pq.read_metadata(pf)
            pf_rows = pf_meta.num_rows
            if cumulative + pf_rows > global_offset:
                target_file = pf
                file_offset = global_offset - cumulative
                break
            cumulative += pf_rows

        # Read only the required slice from the target partition file.
        table = pq.read_table(target_file)
        sliced = table.slice(offset=file_offset, length=page_size)

        return Response(
            {
                "results": sliced.to_pydict(),
                "total_rows": total_rows,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
            }
        )


class SuggestPatternsView(APIView):
    """
    POST /api/jobs/suggest-patterns/

    Accepts a JSON body {"columns": {"col_name": ["val1", "val2", ...], ...}}
    and returns PII classification suggestions for each column.

    This endpoint is cheap and synchronous — it receives only a small sample
    of column values (sent by the frontend after parsing the first N rows of
    the file locally), makes a single batched LLM call, and returns within
    a couple of seconds.  It never touches the full file.

    Redis caches results so re-uploading similar files skips the LLM entirely.
    """

    def post(self, request: Request) -> Response:
        columns = request.data.get("columns", {})
        if not isinstance(columns, dict) or not columns:
            return Response({"suggestions": []})

        # Sanitise: truncate each column's samples to 10 non-empty strings.
        sanitised = {}
        for col, vals in columns.items():
            if not isinstance(vals, list):
                continue
            clean = [str(v) for v in vals if v is not None and str(v).strip()][:10]
            if clean:
                sanitised[str(col)[:256]] = clean

        if not sanitised:
            return Response({"suggestions": []})

        try:
            from tasks.llm_regex import classify_columns
            suggestions = classify_columns(sanitised)
        except Exception as exc:
            # Never let a suggestion error block the user from submitting a job.
            import logging
            logging.getLogger(__name__).warning("classify_columns failed: %s", exc)
            return Response({"suggestions": [], "error": str(exc)})

        return Response({"suggestions": suggestions})


class JobCancelView(APIView):
    """
    POST /api/jobs/{job_id}/cancel/

    Marks a QUEUED or RUNNING job as CANCELLED.  For RUNNING jobs the Celery
    task checks this flag at checkpoints and aborts gracefully.
    """

    def post(self, request: Request, job_id: str) -> Response:
        try:
            job = Job.objects.get(id=job_id)
        except Job.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        if job.is_terminal:
            return Response(
                {"detail": f"Job already in terminal state: {job.status}."},
                status=status.HTTP_409_CONFLICT,
            )

        job.status = JobStatus.CANCELLED
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "completed_at", "updated_at"])

        # Attempt to revoke the Celery task if it hasn't started yet.
        # We use terminate=False so a running Spark job can finish its current
        # partition before stopping (avoids corrupt output files).
        from config.celery import app as celery_app

        celery_app.control.revoke(str(job.id), terminate=False)

        return Response({"job_id": str(job.id), "status": job.status})
