/**
 * useJobPolling — polls job status on a fixed interval, stops automatically
 * when the job reaches a terminal state (SUCCESS, FAILED, CANCELLED).
 *
 * Why polling instead of WebSockets:
 *   - The Django backend is stateless HTTP; adding WebSocket support requires
 *     Django Channels + ASGI, a significant addition.  For job durations of
 *     seconds to minutes, polling every N seconds is perfectly acceptable and
 *     far simpler to operate.
 *   - The hook cleans up its own interval, so there are no memory leaks even
 *     if the component unmounts mid-job.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getJobStatus, JobStatusResponse } from "../api/client";

const TERMINAL_STATES = new Set(["SUCCESS", "FAILED", "CANCELLED"]);

export interface UseJobPollingOptions {
  /** Polling interval in milliseconds (default: 2000). */
  intervalMs?: number;
}

export interface UseJobPollingResult {
  jobStatus: JobStatusResponse | null;
  isPolling: boolean;
  error: string | null;
  /** Manually stop polling (e.g. when the user navigates away). */
  stopPolling: () => void;
}

export function useJobPolling(
  jobId: string | null,
  options: UseJobPollingOptions = {}
): UseJobPollingResult {
  const { intervalMs = 2000 } = options;

  const [jobStatus, setJobStatus] = useState<JobStatusResponse | null>(null);
  const [isPolling, setIsPolling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Store the interval id in a ref so stopPolling() always cancels the current
  // interval regardless of closure staleness.
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (intervalRef.current !== null) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    setIsPolling(false);
  }, []);

  useEffect(() => {
    if (!jobId) return;

    // Reset state when the job id changes (new submission).
    setJobStatus(null);
    setError(null);
    setIsPolling(true);

    const poll = async () => {
      try {
        const status = await getJobStatus(jobId);
        setJobStatus(status);
        setError(null);

        if (TERMINAL_STATES.has(status.status)) {
          // Job is done — stop polling so we don't keep hitting the API.
          stopPolling();
        }
      } catch (err) {
        const message =
          err instanceof ApiError
            ? `API error ${err.status}: ${err.message}`
            : "Unexpected error while polling job status.";
        setError(message);
        // Don't stop polling on transient errors — the next tick may succeed.
        // After 5 consecutive 404s (job deleted?) we could stop, but that
        // logic is left as a future enhancement.
      }
    };

    // Poll immediately, then on every interval.
    poll();
    intervalRef.current = setInterval(poll, intervalMs);

    return () => {
      // Cleanup on unmount or jobId change.
      if (intervalRef.current !== null) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [jobId, intervalMs, stopPolling]);

  return { jobStatus, isPolling, error, stopPolling };
}
