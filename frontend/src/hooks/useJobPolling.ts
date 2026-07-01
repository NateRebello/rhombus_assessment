/**
 * Polls job status on a fixed interval until SUCCESS, FAILED, or CANCELLED.
 * Polling avoids adding Django Channels for a job that typically runs seconds to minutes.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getJobStatus, JobStatusResponse } from "../api/client";

const TERMINAL_STATES = new Set(["SUCCESS", "FAILED", "CANCELLED"]);

export interface UseJobPollingOptions {
  intervalMs?: number;
}

export interface UseJobPollingResult {
  jobStatus: JobStatusResponse | null;
  isPolling: boolean;
  error: string | null;
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

    setJobStatus(null);
    setError(null);
    setIsPolling(true);

    const poll = async () => {
      try {
        const status = await getJobStatus(jobId);
        setJobStatus(status);
        setError(null);

        if (TERMINAL_STATES.has(status.status)) {
          stopPolling();
        }
      } catch (err) {
        const message =
          err instanceof ApiError
            ? `API error ${err.status}: ${err.message}`
            : "Unexpected error while polling job status.";
        setError(message);
      }
    };

    poll();
    intervalRef.current = setInterval(poll, intervalMs);

    return () => {
      if (intervalRef.current !== null) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [jobId, intervalMs, stopPolling]);

  return { jobStatus, isPolling, error, stopPolling };
}
