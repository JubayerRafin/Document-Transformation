"use client";
import { useEffect, useState } from "react";
import {
  Download,
  ExternalLink,
  FileText,
  Info,
  Loader2,
  RefreshCw,
} from "lucide-react";
import {
  ApiClient,
  checkFindings,
  reviewSummary,
  passedChecks,
  redactionSummary,
  downloadUrl,
  seconds,
  type Job,
} from "@/lib/api";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { RedactedValuesPanel } from "./redacted-values";
export function PreviewPanel({
  job,
  api,
  onError,
  readOnly = false,
  onCreated,
}: {
  job: Job;
  api: ApiClient;
  onError: (e: unknown) => void;
  readOnly?: boolean;
  /** A new version of this job was made (values kept visible): open it. */
  onCreated?: (id: string) => void;
}) {
  const [urls, setUrls] = useState<{ source?: string; result?: string }>({}),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [version, setVersion] = useState(0),
    [page, setPage] = useState(1);
  const source = job.artifacts.find((a) => a.kind === "source"),
    result = job.artifacts.find((a) => a.kind === "result");
  useEffect(() => {
    setPage(1);
  }, [job.id]);
  useEffect(() => {
    const c = new AbortController();
    setLoading(true);
    setUrls({});
    setError("");
    const items = [source, result].filter(
      (x): x is NonNullable<typeof x> => !!x,
    );
    Promise.all(
      items.map(async (a) => ({
        kind: a.kind,
        ...(await api.preview(job.id, a.id, c.signal)),
      })),
    )
      .then((rows) => {
        if (!c.signal.aborted)
          setUrls(Object.fromEntries(rows.map((r) => [r.kind, r.url])));
      })
      .catch((e) => {
        if (!c.signal.aborted) {
          setError(e instanceof Error ? e.message : "Preview unavailable.");
          onError(e);
        }
      })
      .finally(() => {
        if (!c.signal.aborted) setLoading(false);
      });
    return () => c.abort();
  }, [api, job.id, source?.id, result?.id, version, onError]);
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-border bg-background p-4 sm:px-6">
        <div className="min-w-0">
          <h2 className="mb-1 truncate text-base font-semibold">
            {job.filename}
          </h2>
          <p className="mb-0 text-xs text-muted-foreground">
            {job.options.operation === "manual_redaction" ? "Manual redaction" : job.options.operation === "pii"
              ? "PII redaction"
              : `${job.options.source_language.toUpperCase()} to ${job.options.target_language.toUpperCase()}`}{" "}
            · {job.pages || "Unknown"} pages
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {passedChecks(job.metrics.checks) && !job.metrics.review_items?.some((i) => i.state === "suggested") ? (
            <Badge className="border-emerald-800 bg-emerald-950 text-emerald-200">
              Checks passed
            </Badge>
          ) : (
            <Badge className="border-amber-800 bg-amber-950 text-amber-200">
              Review suggested
            </Badge>
          )}
          {source && result && (
            <Button asChild size="sm" variant="outline">
              <a href={`/redaction-review.html?job=${encodeURIComponent(job.id)}&compare=1`}>
                Full-screen comparison
              </a>
            </Button>
          )}
          {result && !readOnly && /\.pdf$/i.test(result.filename) && (
            <Button asChild size="sm" variant="outline">
              <a href={`/redaction-review.html?job=${encodeURIComponent(job.id)}&new=1`}>
                Review &amp; add redactions
              </a>
            </Button>
          )}
          {result && (
            <Button asChild size="sm">
              <a href={downloadUrl(job, result)}>
                <Download />
                Download output
              </a>
            </Button>
          )}
        </div>
      </div>
      {job.metrics.checks && (
        <div
          role="note"
          aria-label="Automated checks"
          className="rounded-xl border border-border bg-muted px-4 py-3 text-xs leading-5 text-muted-foreground sm:px-6"
        >
          <p className="mb-0 font-medium text-foreground">
            {checkFindings(job.metrics.checks).length
              ? "Automated checks flagged items for review"
              : job.metrics.checks.acceptable
                ? "Automated checks found no blocking issues"
                : "Automated checks are incomplete for this output"}
          </p>
          {checkFindings(job.metrics.checks).length > 0 && (
            <p className="mb-0">{checkFindings(job.metrics.checks).join(" · ")}</p>
          )}
          {redactionSummary(job.metrics.redaction_map) && (
            <p className="mb-0" data-testid="redaction-summary">
              {redactionSummary(job.metrics.redaction_map)}.
              {source && result && " Open the full-screen comparison and turn on “Show why” to see each one."}
            </p>
          )}
          {reviewSummary(job.metrics.review_items) && (
            <p className="mb-0" data-testid="review-queue">
              Review queue: {reviewSummary(job.metrics.review_items)}.{" "}
              {result && !readOnly && (
                <a
                  className="font-medium text-foreground underline"
                  href={`/redaction-review.html?job=${encodeURIComponent(job.id)}&new=1`}
                >
                  Open the review
                </a>
              )}
            </p>
          )}
          {job.options.operation !== "translation" && (
            <p className="mb-0">
              Rendered-output privacy check:{" "}
              {job.metrics.checks.privacy_verification === "passed"
                ? "passed"
                : job.metrics.checks.privacy_verification === "failed"
                  ? "found items to review"
                  : job.metrics.checks.privacy_verification === "not_needed"
                    ? "not needed (no pictures; all text was checked directly)"
                    : "not run"}
              . Automated checks do not replace a full human review.
            </p>
          )}
        </div>
      )}
      {job.parent_job_id && (job.options.pii_keep?.length || 0) > 0 && (
        <p className="mb-0 text-xs text-muted-foreground" data-testid="kept-version">
          New version made from the original, with {job.options.pii_keep!.length} value
          {job.options.pii_keep!.length === 1 ? "" : "s"} kept visible.
        </p>
      )}
      {result && onCreated && (job.options.operation === "pii" || job.options.operation === "combined") && (
        <RedactedValuesPanel job={job} api={api} onError={onError} onCreated={onCreated} readOnly={readOnly} />
      )}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="mb-0 text-xs text-muted-foreground">
          Compare the original document with the generated output.
        </p>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            Page
            <select
              aria-label="Preview page"
              value={page}
              onChange={(e) => setPage(Number(e.target.value))}
              className="select-input !h-9 !w-20"
            >
              {Array.from(
                { length: Math.max(1, Math.min(1000, job.pages || 1)) },
                (_, i) => (
                  <option key={i} value={i + 1}>
                    {i + 1}
                  </option>
                ),
              )}
            </select>
          </label>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setVersion((v) => v + 1)}
          >
            <RefreshCw />
            Reload previews
          </Button>
        </div>
      </div>
      {error && (
        <div
          role="alert"
          className="rounded-xl border border-red-800 bg-red-950 p-4 text-sm text-destructive"
        >
          {error}
        </div>
      )}
      <div className="grid gap-5 xl:grid-cols-2">
        {[
          ["source", "Original document", source],
          ["result", "Transformed document", result],
        ].map(([kind, label, a]) => {
          const artifact = typeof a === "object" ? a : undefined;
          const url = urls[kind as keyof typeof urls];
          return (
            <section
              key={kind as string}
              className="min-w-0 overflow-hidden rounded-xl border border-border bg-background"
            >
              <div className="flex items-center justify-between border-b border-border px-5 py-3.5">
                <h3 className="mb-0 flex items-center gap-2 text-xs font-medium">
                  <FileText className="size-4 text-muted-foreground" />
                  {label as string}
                </h3>
                {artifact && (
                  <a
                    href={downloadUrl(job, artifact)}
                    target="_blank"
                    rel="noreferrer"
                    aria-label={`Open ${label} PDF`}
                    className="inline-flex items-center gap-1 text-xs text-primary"
                  >
                    Open PDF <ExternalLink className="size-3" />
                  </a>
                )}
              </div>
              {loading ? (
                <div
                  className="flex h-[450px] items-center justify-center"
                  role="status"
                >
                  <Loader2 className="mr-2 size-5 animate-spin text-primary" />
                  <span className="text-sm text-muted-foreground">
                    Loading preview…
                  </span>
                </div>
              ) : url ? (
                <iframe
                  title={label as string}
                  src={`${url}#page=${page}`}
                  className="pdf-frame"
                />
              ) : (
                <div className="flex h-72 items-center justify-center px-5 text-center text-sm text-muted-foreground">
                  {kind === "result"
                    ? "No generated output is available for this job."
                    : "The original preview is unavailable."}
                </div>
              )}
            </section>
          );
        })}
      </div>
      <div className="grid gap-5 rounded-xl border border-border bg-background p-5 md:grid-cols-3">
        <div>
          <p className="mb-1 text-xs text-muted-foreground">
            Server processing
          </p>
          <p className="mb-0 text-sm font-medium tabular-nums">
            {seconds(job.metrics.server_seconds)}
          </p>
        </div>
        <div>
          <p className="mb-1 text-xs text-muted-foreground">Evaluation</p>
          <p className="mb-0 text-sm font-medium">
            {job.metrics.evaluation_status || "Pending"}
          </p>
        </div>
        <div>
          <p className="mb-1 text-xs text-muted-foreground">Run record</p>
          <a
            className="inline-flex items-center gap-2 text-sm font-medium text-primary"
            href={`/v1/jobs/${encodeURIComponent(job.id)}/report`}
          >
            Download JSON <Download className="size-3.5" />
          </a>
        </div>
      </div>
      <p className="mb-0 flex items-start gap-2 text-xs leading-5 text-muted-foreground">
        <Info className="mt-0.5 size-4" /> Check translation, layout and
        redaction before sharing. A generated PDF is not an approved result. If
        your browser cannot display PDFs, use the download links.
      </p>
    </div>
  );
}
