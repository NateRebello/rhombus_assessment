"""Job model: tracks upload metadata, LLM output, and processing state. File bytes live on the volume."""
import uuid

from django.db import models


class JobStatus(models.TextChoices):
    QUEUED = "QUEUED", "Queued"
    RUNNING = "RUNNING", "Running"
    SUCCESS = "SUCCESS", "Success"
    FAILED = "FAILED", "Failed"
    CANCELLED = "CANCELLED", "Cancelled"


class Job(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(
        max_length=16,
        choices=JobStatus.choices,
        default=JobStatus.QUEUED,
        db_index=True,
    )
    progress = models.PositiveSmallIntegerField(default=0)

    file_path = models.TextField()
    original_filename = models.CharField(max_length=512)
    target_column = models.CharField(max_length=256)
    prompt = models.TextField()
    replacement_value = models.CharField(max_length=512, default="")
    normalize_mode = models.CharField(max_length=16, default="none")

    generated_regex = models.CharField(max_length=1024, blank=True, default="")

    result_path = models.TextField(blank=True, default="")
    row_count = models.BigIntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")

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
        return self.status in (
            JobStatus.SUCCESS,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        )
