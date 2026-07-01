"""Shared Celery retry limits. Retryable: transient network/LLM errors. Terminal: bad input data."""

MAX_RETRIES = 5
RETRY_BACKOFF_MAX = 300  # seconds — cap exponential backoff
