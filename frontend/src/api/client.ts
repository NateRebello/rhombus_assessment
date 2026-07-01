/** Typed fetch wrapper for the Django REST API. Base URL from VITE_API_BASE_URL. */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      message = body?.detail ?? body?.message ?? JSON.stringify(body);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, message);
  }
  return res.json() as Promise<T>;
}

export type JobStatus =
  | "QUEUED"
  | "RUNNING"
  | "SUCCESS"
  | "FAILED"
  | "CANCELLED";

export interface JobCreateResponse {
  job_id: string;
}

export type NormalizeMode = "none" | "e164" | "iso8601";

export interface JobStatusResponse {
  id: string;
  status: JobStatus;
  progress: number;
  normalize_mode: NormalizeMode;
  generated_regex: string;
  row_count: number | null;
  error_message: string;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface PiiSuggestion {
  column: string;
  pii_type: string;
  confidence: number;
  suggested_prompt: string;
}

export interface SuggestPatternsResponse {
  suggestions: PiiSuggestion[];
  error?: string;
}

export interface JobResultResponse {
  results: Record<string, unknown[]>;
  total_rows: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export async function createJob(params: {
  file: File;
  prompt: string;
  targetColumn: string;
  replacementValue?: string;
  normalizeMode?: NormalizeMode;
}): Promise<JobCreateResponse> {
  const body = new FormData();
  body.append("file", params.file);
  body.append("prompt", params.prompt);
  body.append("target_column", params.targetColumn);
  body.append("replacement_value", params.replacementValue ?? "");
  body.append("normalize_mode", params.normalizeMode ?? "none");

  const res = await fetch(`${BASE_URL}/api/jobs/`, {
    method: "POST",
    body,
  });
  return handleResponse<JobCreateResponse>(res);
}

export async function suggestPatterns(
  columns: Record<string, string[]>
): Promise<SuggestPatternsResponse> {
  const res = await fetch(`${BASE_URL}/api/jobs/suggest-patterns/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ columns }),
  });
  return handleResponse<SuggestPatternsResponse>(res);
}

export async function getJobStatus(jobId: string): Promise<JobStatusResponse> {
  const res = await fetch(`${BASE_URL}/api/jobs/${jobId}/status/`);
  return handleResponse<JobStatusResponse>(res);
}

export async function getJobResults(
  jobId: string,
  page = 1,
  pageSize = 100
): Promise<JobResultResponse> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  const res = await fetch(`${BASE_URL}/api/jobs/${jobId}/results/?${params}`);
  return handleResponse<JobResultResponse>(res);
}

export async function cancelJob(
  jobId: string
): Promise<{ job_id: string; status: JobStatus }> {
  const res = await fetch(`${BASE_URL}/api/jobs/${jobId}/cancel/`, {
    method: "POST",
  });
  return handleResponse(res);
}
