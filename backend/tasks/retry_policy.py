"""
Shared retry / backoff configuration for Celery tasks.

Centralised here so retry behaviour is consistent across all tasks and easy
to tune in one place without hunting through every @app.task decorator.

Retry philosophy:
  RETRYABLE errors: transient infrastructure failures — network timeouts,
    connection refused, throttling.  These are worth retrying with exponential
    backoff because the failure is temporary and not caused by the input data.

  TERMINAL errors: problems with the data or configuration — invalid file
    format, column not found, regex compile error, LLM content-policy refusal.
    These should NOT be retried because they will always fail with the same
    input.  The task catches them, marks the Job as FAILED, and returns without
    calling self.retry().
"""

# Maximum number of automatic retries before Celery gives up and marks the
# task as FAILURE.
MAX_RETRIES = 5

# Initial countdown (seconds) before the first retry.  Subsequent retries
# are multiplied by retry_backoff (the Celery broker's built-in exponential
# backoff, enabled by retry_backoff=True on the task decorator).
RETRY_COUNTDOWN_BASE = 10  # seconds

# Hard ceiling on the backoff delay.  Without this, exponential growth would
# produce waits of hours for later retries.
RETRY_BACKOFF_MAX = 300  # seconds (5 minutes)

# Exceptions that are always retryable regardless of context.
RETRYABLE_EXCEPTIONS = (
    # Standard library networking / I/O transients
    ConnectionError,
    TimeoutError,
    OSError,
)
