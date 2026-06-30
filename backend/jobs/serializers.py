from rest_framework import serializers

from .models import Job


_ALLOWED_NORMALIZE = ("none", "e164", "iso8601")


class JobCreateSerializer(serializers.Serializer):
    """Validates the multipart/form-data payload for POST /api/jobs/."""

    file = serializers.FileField()
    prompt = serializers.CharField(max_length=2048)
    target_column = serializers.CharField(max_length=256)
    replacement_value = serializers.CharField(
        max_length=512,
        required=False,
        default="",
        allow_blank=True,
    )
    normalize_mode = serializers.ChoiceField(
        choices=_ALLOWED_NORMALIZE,
        required=False,
        default="none",
    )


class JobStatusSerializer(serializers.ModelSerializer):
    """Lightweight status response used by the polling endpoint."""

    class Meta:
        model = Job
        fields = [
            "id",
            "status",
            "progress",
            "normalize_mode",
            "generated_regex",
            "row_count",
            "error_message",
            "created_at",
            "updated_at",
            "started_at",
            "completed_at",
        ]
        read_only_fields = fields


class JobDetailSerializer(serializers.ModelSerializer):
    """Full job representation including input parameters."""

    class Meta:
        model = Job
        fields = [
            "id",
            "status",
            "progress",
            "original_filename",
            "target_column",
            "prompt",
            "replacement_value",
            "generated_regex",
            "result_path",
            "row_count",
            "error_message",
            "created_at",
            "updated_at",
            "started_at",
            "completed_at",
        ]
        read_only_fields = fields
