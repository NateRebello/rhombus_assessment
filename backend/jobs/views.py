"""REST API views for job lifecycle: create, poll, paginated results, cancel, PII suggestions."""
import logging
import uuid
from pathlib import Path

import pyarrow.parquet as pq
from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .file_samples import extract_column_samples
from .models import Job, JobStatus
from .serializers import JobCreateSerializer, JobStatusSerializer

logger = logging.getLogger(__name__)


def _stream_upload_to_disk(file_obj, dest_path: Path) -> None:
    """Chunked write to the shared volume so web and worker resolve the same path."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "wb") as out:
        for chunk in file_obj.chunks(chunk_size=8 * 1024 * 1024):
            out.write(chunk)


class JobCreateView(APIView):
    parser_classes = [MultiPartParser]

    def post(self, request: Request) -> Response:
        serializer = JobCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        uploaded_file = serializer.validated_data["file"]
        job_id = uuid.uuid4()

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

        # Only job_id crosses the Celery boundary — file bytes would bloat Redis and
        # couple message size to upload size.
        from tasks.pipeline import process_job

        process_job.apply_async((str(job.id),), task_id=str(job.id))

        return Response({"job_id": str(job.id)}, status=status.HTTP_202_ACCEPTED)


class JobStatusView(APIView):
    def get(self, request: Request, job_id: str) -> Response:
        try:
            job = Job.objects.get(id=job_id)
        except Job.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response(JobStatusSerializer(job).data)


class JobResultView(APIView):
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

        parquet_files = sorted(result_path.glob("*.parquet"))
        if not parquet_files:
            return Response({"results": [], "total_rows": 0, "page": page, "page_size": page_size})

        total_rows = job.row_count or 0
        total_pages = max(1, -(-total_rows // page_size))

        if page < 1 or page > total_pages:
            return Response(
                {"detail": f"Page {page} out of range (1–{total_pages})."},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
    parser_classes = [MultiPartParser, JSONParser]

    def post(self, request: Request) -> Response:
        uploaded = request.FILES.get("file")
        if uploaded:
            try:
                sanitised = extract_column_samples(uploaded)
            except ValueError as exc:
                return Response({"suggestions": [], "error": str(exc)})
            except Exception as exc:
                logger.warning("extract_column_samples failed: %s", exc)
                return Response({"suggestions": [], "error": str(exc)})
        else:
            columns = request.data.get("columns", {})
            if not isinstance(columns, dict) or not columns:
                return Response({"suggestions": []})

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
            logger.warning("classify_columns failed: %s", exc)
            return Response({"suggestions": [], "error": str(exc)})

        return Response({"suggestions": suggestions})


class JobCancelView(APIView):
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

        from config.celery import app as celery_app

        celery_app.control.revoke(str(job.id), terminate=False)

        return Response({"job_id": str(job.id), "status": job.status})
