import uuid

from django.db import models


class JobStatus(models.TextChoices):
    QUEUED = "QUEUED", "Queued"
    RUNNING = "RUNNING", "Running"
    SUCCESS = "SUCCESS", "Success"
    FAILED = "FAILED", "Failed"
    CANCELLED = "CANCELLED", "Cancelled"


class Job(models.Model):
    """
    Represents a single pattern-matching job submitted by a user.

    The job lifecycle is:
        QUEUED → RUNNING → SUCCESS
                        → FAILED
        (any state) → CANCELLED (if requested before completion)

    Heavy data (the uploaded file, the Parquet results) live on a shared
    volume and are referenced here by path only.  The Django web process
    never reads those files in full — it streams uploads to disk and reads
    Parquet row-groups one page at a time.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(
        max_length=16,
        choices=JobStatus.choices,
        default=JobStatus.QUEUED,
        db_index=True,
    )
    # 0–100 percentage; updated by the Celery task during processing.
    progress = models.PositiveSmallIntegerField(default=0)

    # --- Input ---
    # Path on the shared volume where the uploaded file was saved.
    # Storing a path (not the bytes) keeps this row small and avoids holding
    # large blobs in Postgres.
    file_path = models.TextField()
    original_filename = models.CharField(max_length=512)
    # The column in the uploaded file that the regex will be applied to.
    target_column = models.CharField(max_length=256)
    # Free-text description of the pattern the user wants to match.
    prompt = models.TextField()
    # The value to substitute when a match is found (may be empty string for
    # deletion, or a backreference like \1 for capture-group replacement).
    replacement_value = models.CharField(max_length=512, default="")

    # --- Transform mode ---
    # "none"    → literal regexp_replace (original behaviour, default)
    # "e164"    → normalise matched phone numbers to E.164
    # "iso8601" → normalise matched dates to YYYY-MM-DD
    normalize_mode = models.CharField(max_length=16, default="none")

    # --- LLM output (cached separately in Redis, stored here for auditability) ---
    generated_regex = models.CharField(max_length=1024, blank=True, default="")

    # --- Output ---
    # Path to the directory containing Parquet partition files written by Spark.
    result_path = models.TextField(blank=True, default="")
    # Total number of rows processed (set by Spark transform).
    row_count = models.BigIntegerField(null=True, blank=True)
    # Human-readable error; only populated when status=FAILED.
    error_message = models.TextField(blank=True, default="")

    # --- Timestamps ---
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"Job({self.id}, {self.status})"

    @property
    def is_terminal(self) -> bool:
        """True once the job can no longer transition to another state."""
        return self.status in (
            JobStatus.SUCCESS,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        )
