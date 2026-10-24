export type Operation = "translation" | "pii" | "combined";
export type Role = "customer" | "mentor" | "admin" | "viewer";
export type Account = { name: string; role: Role; csrf: string };
export type Artifact = {
  id: string;
  kind: string;
  filename: string;
  sha256: string;
  size: number;
};
export type Job = {
  id: string;
  filename: string;
  status:
    | "uploading"
    | "queued"
    | "running"
    | "succeeded"
    | "failed"
    | "cancelled"
    | "interrupted";
  stage: string;
  quality: string;
  pages: number;
  created: number;
  updated: number;
  baseline: boolean;
  options: TransformOptions;
  metrics: {
    server_seconds?: number;
    total_seconds?: number;
    blockers?: number;
    evaluation_status?: string;
    acceptance_checked?: boolean;
    gpu_verified?: boolean;
  };
  source_sha256: string;
  assigned_worker_id?: string;
  artifacts: Artifact[];
  error_code?: string;
};
export type Worker = {
  id: string;
  name: string;
  enabled: boolean;
  online: boolean;
  ready: boolean;
  capabilities?: { gpu?: string; operations?: Operation[]; note?: string };
};
export type JobEvent = {
  id: number;
  stage: string;
  elapsed_seconds: number;
  created: number;
};
export type TransformOptions = {
  operation: Operation;
  source_language: string;
  target_language: string;
  output_format: "pdf";
  protected_values: string[];
  document_context: string;
  pii_categories: string[];
  worker_id: string;
};
export const PII_CATEGORIES = [
  "Names",
  "Email addresses",
  "Phone numbers",
  "Addresses",
  "Identifiers",
  "Bank accounts",
] as const;

const MESSAGES: Record<string, string> = {
  INVALID_LOGIN: "That email and password didn’t match. Please try again.",
  ACCOUNT_NOT_INVITED:
    "Your account needs an invitation. Contact your workspace administrator.",
  SIGN_IN_REQUIRED: "Your session has expired. Sign in again to continue.",
  SELECT_GPU_WORKER: "Choose a processing server before submitting.",
  WORKER_NOT_OWNED_OR_DISABLED:
    "That server is no longer available to your account. Choose another server.",
  HOSTED_BACKEND_NOT_CONFIGURED:
    "The service needs administrator configuration. Please try again later.",
  DATABASE_OR_STORAGE_REQUEST_FAILED:
    "The document service is unavailable. Try again without changing your document.",
  REQUEST_TOO_LARGE: "The file is too large. Choose a PDF under 50 MB.",
  INVALID_PDF: "Choose a valid PDF document.",
  CSRF_INVALID:
    "Your session needs refreshing. Reload this page and sign in again.",
  ONLY_QUEUED_CANCELLATION_SUPPORTED:
    "This job has already started or finished. Only queued jobs can be cancelled.",
  WORKER_BUSY_STOP_PROCESSING_FIRST:
    "This server is processing a job. Wait for it to finish before revoking access.",
};
export class ApiError extends Error {
  constructor(
    public code: string,
    public status: number,
  ) {
    super(
      MESSAGES[code] || "We couldn’t complete that request. Please try again.",
    );
  }
}
export class ApiClient {
  csrf = "";
  constructor(private transport: typeof fetch = (...args) => fetch(...args)) {}
  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    if (init.method && init.method !== "GET")
      headers.set("X-CSRF-Token", this.csrf);
    let response: Response;
    try {
      response = await this.transport(path, {
        ...init,
        headers,
        credentials: "same-origin",
        cache: "no-store",
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError")
        throw error;
      throw new ApiError("NETWORK_ERROR", 0);
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok)
      throw new ApiError(
        typeof data.error === "string" ? data.error : "REQUEST_FAILED",
        response.status,
      );
    return data as T;
  }
  async me(signal?: AbortSignal) {
    const user = await this.request<Account>("/v1/me", { signal });
    this.csrf = user.csrf;
    return user;
  }
  async login(email: string, password: string) {
    const user = await this.request<Account>("/v1/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: email, password }),
    });
    this.csrf = user.csrf;
    return user;
  }
  async logout() {
    await this.request("/v1/session", { method: "DELETE" });
    this.csrf = "";
  }
  jobs(signal?: AbortSignal) {
    return this.request<Job[]>("/v1/jobs", { signal });
  }
  workers(signal?: AbortSignal) {
    return this.request<Worker[]>("/v1/workers", { signal });
  }
  events(id: string, signal?: AbortSignal) {
    return this.request<JobEvent[]>(
      `/v1/jobs/${encodeURIComponent(id)}/events`,
      { signal },
    );
  }
  submit(file: File, options: TransformOptions, key: string) {
    const data = new FormData();
    data.append("file", file);
    data.append("options", JSON.stringify(options));
    return this.request<{ job_id: string }>("/v1/jobs", {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: data,
    });
  }
  cancel(id: string) {
    return this.request(`/v1/jobs/${encodeURIComponent(id)}/cancel`, {
      method: "POST",
    });
  }
  preview(id: string, artifactId: string, signal?: AbortSignal) {
    return this.request<{ url: string }>(
      `/v1/jobs/${encodeURIComponent(id)}/artifacts/${encodeURIComponent(artifactId)}/url`,
      { signal },
    );
  }
  registerWorker(name: string) {
    return this.request<{
      id: string;
      token: string;
      name: string;
      supabase_url: string;
    }>("/v1/workers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
  }
  revokeWorker(id: string) {
    return this.request(`/v1/workers/${encodeURIComponent(id)}/revoke`, {
      method: "POST",
    });
  }
}
export async function validatePdf(file: File) {
  if (!/\.pdf$/i.test(file.name) || file.size < 5)
    return "Choose a PDF document.";
  if (file.size > 50_000_000) return "Choose a PDF smaller than 50 MB.";
  try {
    if (
      new TextDecoder().decode(await file.slice(0, 5).arrayBuffer()) !== "%PDF-"
    )
      return "This file doesn’t appear to be a valid PDF.";
  } catch {
    return "We couldn’t read that file. Please choose it again.";
  }
  return null;
}
export function seconds(value?: number) {
  return typeof value === "number" ? `${value.toFixed(1)} s` : "Not measured";
}
export function stageName(stage: string) {
  return stage.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
export function downloadUrl(job: Job, artifact: Artifact) {
  return `/v1/jobs/${encodeURIComponent(job.id)}/artifacts/${encodeURIComponent(artifact.id)}`;
}
