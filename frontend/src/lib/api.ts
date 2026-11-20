import type { Glossary, GlossaryEntry, GlossarySummary } from "./glossary";
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
  options: Omit<TransformOptions, "operation"> & { operation: Operation | "manual_redaction" };
  metrics: {
    server_seconds?: number;
    total_seconds?: number;
    blockers?: number;
    evaluation_status?: string;
    acceptance_checked?: boolean;
    gpu_verified?: boolean;
    checks?: JobChecks;
    review_items?: ReviewItem[];
    redaction_map?: RedactionBox[];
  };
  source_sha256: string;
  assigned_worker_id?: string;
  artifacts: Artifact[];
  error_code?: string;
};
/** A place in the redacted PDF a person should look at; a box only, never text. */
export type ReviewItem = {
  page: number;
  x: number;
  y: number;
  width: number;
  height: number;
  category: string;
  reason: "unverified_answer" | "whole_line" | "rule_overruled_model" | "unreadable_text" | "final_check";
  state: "masked" | "suggested";
};
/** One automatically drawn box: where it is, what type it was and how it was found. */
export type RedactionBox = {
  page: number;
  x: number;
  y: number;
  width: number;
  height: number;
  category: string;
  method: string;
};
const METHOD_GROUPS: [string, string[]][] = [
  ["field label or format", ["field_label", "pattern", "credential_label_pattern", "address_block"]],
  ["language model", ["model_proposal"]],
  ["name list or consistency", ["document_name_sweep", "name_variant", "document_consistency"]],
  ["precaution or final check", ["unverifiable_model_answer", "final_check"]],
];
/** "14 areas masked: 6 Names, 4 Identifiers · found by: language model 8, ..." or "". */
export function redactionSummary(boxes?: RedactionBox[]) {
  if (!boxes?.length) return "";
  const count = (keys: string[]) => boxes.filter((b) => keys.includes(b.method)).length;
  const byCategory = new Map<string, number>();
  for (const b of boxes) byCategory.set(b.category, (byCategory.get(b.category) || 0) + 1);
  const categories = [...byCategory].sort((a, b) => b[1] - a[1]).map(([c, n]) => `${n} ${c}`);
  const methods = METHOD_GROUPS.map(([label, keys]) => [label, count(keys)] as const)
    .filter(([, n]) => n > 0)
    .map(([label, n]) => `${label} ${n}`);
  return `${boxes.length} area${boxes.length > 1 ? "s" : ""} masked: ${categories.join(", ")} · found by: ${methods.join(", ")}`;
}
/** One-line summary of the review queue, or "" when it is empty. */
export function reviewSummary(items?: ReviewItem[]) {
  if (!items?.length) return "";
  const suggested = items.filter((i) => i.state === "suggested").length;
  const masked = items.length - suggested;
  return [
    suggested && `${suggested} possible miss${suggested > 1 ? "es" : ""} to check`,
    masked && `${masked} area${masked > 1 ? "s" : ""} masked as a precaution`,
  ]
    .filter(Boolean)
    .join(" · ");
}
/** Counts of automated-check findings reported by the worker; no document content. */
export type JobChecks = {
  acceptable: boolean;
  privacy_verification: "passed" | "failed" | "not_run";
  known_sensitive_residuals: number;
  partial_name_residuals?: number;
  geometry_blockers: number;
  layout_review_candidates: number;
  uninspected_content: number;
  scan_acceptance_blockers: number;
  translation_errors: number;
};
const FAILURES: Record<string, string> = {
  WORKER_HEARTBEAT_LOST:
    "The processing server stopped responding (for example, the pod was stopped or restarted). Submit the document again when the server is ready.",
  ENGINE_TIMEOUT:
    "Processing exceeded the server’s time limit and was stopped. Try fewer pages or raise the server’s time limit.",
  INPUT_REJECTED:
    "The server could not open this PDF. Encrypted PDFs and documents over 200 pages are not supported.",
  OUTPUT_INVALID:
    "The server produced an output that failed basic checks, so it was not released.",
  RESULT_UPLOAD_FAILED:
    "Processing finished, but the result could not be uploaded after several attempts. The output remains on the processing server.",
  REDACTION_REVIEW_REQUIRED:
    "Some sensitive values could not be located precisely enough to remove safely, so no output was released.",
  PRIVACY_CHECK_BLOCKED:
    "A selected sensitive value was still found in the output, so it was blocked.",
  MANUAL_REDACTION_FAILED:
    "The selected areas could not be removed and verified, so no redacted copy was released.",
  TRANSLATION_MODEL_UNAVAILABLE:
    "The chosen translation model is no longer set up on this server. Choose another model and start again.",
  PII_MODEL_UNAVAILABLE:
    "The chosen PII model is no longer set up on this server. Choose another model and start again.",
};
export function failureMessage(code?: string) {
  return (
    (code && FAILURES[code]) ||
    "The engine or connection did not complete successfully. Your original document remains available."
  );
}
const CHECK_LABELS: [keyof JobChecks, string][] = [
  ["known_sensitive_residuals", "sensitive values remaining"],
  ["partial_name_residuals", "partly masked names"],
  ["geometry_blockers", "layout problems"],
  ["layout_review_candidates", "possible overlaps"],
  ["uninspected_content", "images or attachments not checked automatically"],
  ["scan_acceptance_blockers", "scanned regions needing review"],
  ["translation_errors", "untranslated passages"],
];
export function checkFindings(checks?: JobChecks) {
  if (!checks) return [];
  return CHECK_LABELS.filter(([k]) => Number(checks[k]) > 0).map(
    ([k, label]) => `${checks[k]} ${label}`,
  );
}
export type Worker = {
  id: string;
  name: string;
  enabled: boolean;
  online: boolean;
  ready: boolean;
  capabilities?: {
    gpu?: string;
    operations?: Operation[];
    note?: string;
    /** Translation models this server offers (its settings file); "default" is its own. */
    translation_models?: TranslationModel[];
    /** PII models this server offers (its settings file); "default" is its own. */
    pii_models?: TranslationModel[];
  };
};
export type TranslationModel = {
  id: string;
  label: string;
  /** Runs on another machine: document text is sent there. */
  external?: boolean;
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
  /** How removed text looks: black box, box with the type ([NAME]) or a consistent placeholder. */
  pii_style?: PiiStyle;
  /** Detected values that should stay visible (e.g. the customer's own company name). */
  pii_keep?: string[];
  /** One of the chosen server's translation models; omitted for its default. */
  translation_model?: string;
  /** One of the server's PII models; omitted for its default. */
  pii_model?: string;
};
export type PiiStyle = "box" | "label" | "pseudonym";
export const PII_CATEGORIES = [
  "Names",
  "Email addresses",
  "Phone numbers",
  "Addresses",
  "Identifiers",
  "Bank accounts",
  "Dates of birth",
] as const;

const MESSAGES: Record<string, string> = {
  INVALID_LOGIN: "That email and password didn’t match. Please try again.",
  ACCOUNT_NOT_INVITED:
    "Your account needs an invitation. Contact your workspace administrator.",
  SIGN_IN_REQUIRED: "Your session has expired. Sign in again to continue.",
  SELECT_GPU_WORKER: "Choose a processing server before submitting.",
  INVALID_PII_STYLE: "Choose how removed text should look.",
  INVALID_PII_KEEP:
    "Use up to 100 keep-visible values, each no longer than 200 characters.",
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
  INVALID_GLOSSARY:
    "Every glossary term needs a translation, on one line, of at most 200 characters (100 terms at most).",
  DUPLICATE_GLOSSARY_TERM: "A term appears twice in this glossary.",
  INVALID_LANGUAGE: "Choose two different languages.",
  TRANSLATION_MODEL_NOT_OFFERED: "That translation model is not available on this server right now. Reload the page.",
  PII_MODEL_NOT_OFFERED: "That PII model is not available on this server right now. Reload the page.",
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
  glossaries(signal?: AbortSignal) {
    return this.request<GlossarySummary[]>("/v1/glossaries", { signal });
  }
  glossary(source: string, target: string, signal?: AbortSignal) {
    const q = new URLSearchParams({ source, target });
    return this.request<Glossary>(`/v1/glossary?${q}`, { signal });
  }
  saveGlossary(source: string, target: string, entries: GlossaryEntry[]) {
    return this.request<Glossary>("/v1/glossary", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_language: source, target_language: target, entries }),
    });
  }
}
export async function validatePdf(file: File, facts: (file: File) => Promise<{ pages?: number; encrypted?: boolean }> = pdfFacts) {
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
  const { pages, encrypted } = await facts(file);
  if (encrypted)
    return "This PDF is password-protected. Remove the password, then upload it again.";
  if (pages !== undefined && pages > MAX_PAGES)
    return `This PDF has ${pages} pages; the limit is ${MAX_PAGES}. Split it into parts of up to ${MAX_PAGES} pages.`;
  return null;
}
export const MAX_PAGES = 200;
type PdfJs = {
  GlobalWorkerOptions: { workerSrc: string };
  getDocument: (src: { data: ArrayBuffer; isEvalSupported: boolean }) => {
    promise: Promise<{ numPages: number; destroy: () => Promise<void> }>;
  };
};
/**
 * Page count and password protection, read in the browser with the site's own copy of pdf.js
 * (published at /pdfjs/), so a file the processing server would refuse is caught before upload.
 * Anything that cannot be read here is left to the server's own check.
 */
export async function pdfFacts(
  file: File,
  load: () => Promise<PdfJs> = () => {
    // A runtime address, never bundled: the site serves its own copy of pdf.js.
    const url = ["", "pdfjs", "pdf.mjs"].join("/");
    return import(/* webpackIgnore: true */ /* turbopackIgnore: true */ /* @vite-ignore */ url) as Promise<PdfJs>;
  },
): Promise<{ pages?: number; encrypted?: boolean }> {
  try {
    const pdfjs = await load();
    pdfjs.GlobalWorkerOptions.workerSrc = "/pdfjs/pdf.worker.mjs";
    const doc = await pdfjs.getDocument({ data: await file.arrayBuffer(), isEvalSupported: false }).promise;
    const pages = doc.numPages;
    await doc.destroy();
    return { pages };
  } catch (error) {
    if ((error as { name?: string })?.name === "PasswordException") return { encrypted: true };
    return {};
  }
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
