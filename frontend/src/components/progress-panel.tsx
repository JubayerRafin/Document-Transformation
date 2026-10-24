"use client";
import { useEffect, useState } from "react";
import {
  CheckCircle2,
  Clock3,
  Download,
  FileText,
  Loader2,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
  XCircle,
} from "lucide-react";
import {
  ApiClient,
  stageName,
  seconds,
  type Job,
  type JobEvent,
} from "@/lib/api";
import { Button } from "./ui/button";
import { StatusBadge } from "./history-panel";
export function ProgressPanel({
  job,
  api,
  onRefresh,
  onResult,
  onError,
}: {
  job: Job;
  api: ApiClient;
  onRefresh: () => Promise<void>;
  onResult: () => void;
  onError: (e: unknown) => void;
}) {
  const [events, setEvents] = useState<JobEvent[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    const c = new AbortController();
    let active = false;
    setEvents([]);
    async function tick() {
      if (active || document.hidden) return;
      active = true;
      try {
        const rows = await api.events(job.id, c.signal);
        if (!c.signal.aborted) {
          setEvents(rows);
          setError("");
        }
      } catch (e) {
        if (!c.signal.aborted) {
          setError(e instanceof Error ? e.message : "Progress unavailable.");
          onError(e);
        }
      } finally {
        active = false;
      }
    }
    void tick();
    const timer = ["uploading", "queued", "running"].includes(job.status)
      ? setInterval(tick, 5000)
      : null;
    return () => {
      c.abort();
      if (timer) clearInterval(timer);
    };
  }, [api, job.id, job.status, onError]);
  async function cancel() {
    setBusy(true);
    try {
      await api.cancel(job.id);
      await onRefresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Cancellation failed.");
      onError(e);
    } finally {
      setBusy(false);
    }
  }
  const failed = ["failed", "interrupted"].includes(job.status),
    complete = job.status === "succeeded";
  return (
    <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_290px]">
      <section className="rounded-2xl border border-border bg-background p-6 sm:p-9">
        <div className="mb-8 flex flex-wrap items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-3">
            <FileText className="size-6 text-primary" />
            <h2 className="mb-0 truncate text-base font-semibold">
              {job.filename}
            </h2>
          </div>
          <StatusBadge status={job.status} />
        </div>
        <div className="mb-9 rounded-xl bg-muted px-5 py-7">
          <div className="mb-3 flex items-center gap-3">
            {complete ? (
              <CheckCircle2 className="size-6 text-emerald-600" />
            ) : failed ? (
              <TriangleAlert className="size-6 text-destructive" />
            ) : job.status === "running" ? (
              <Loader2 className="size-6 animate-spin text-primary" />
            ) : (
              <Clock3 className="size-6 text-primary" />
            )}
            <h3 className="mb-0 text-lg font-semibold">
              {complete
                ? "Your result is ready for review"
                : job.status === "queued"
                  ? "Waiting for your processing server"
                  : job.status === "cancelled"
                    ? "Job cancelled"
                    : failed
                      ? "This job needs attention"
                      : stageName(job.stage)}
            </h3>
          </div>
          <p className="mb-0 text-sm leading-6 text-muted-foreground">
            {job.status === "queued"
              ? "Your job stays assigned to the server you selected. It will start when that server is available and ready."
              : complete
                ? "Generation is complete. Review the document before sharing it."
                : failed
                  ? "The engine or connection did not complete successfully. Your original document remains available."
                  : job.status === "cancelled"
                    ? "This queued job will not start."
                    : "Recorded stages appear below as the worker reports them. Closing this page does not stop processing."}
          </p>
        </div>
        <h3 className="mb-5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Processing activity
        </h3>
        {events.length ? (
          <ol className="m-0 list-none space-y-0 p-0" aria-live="polite">
            {events.map((e, i) => (
              <li key={e.id} className="flex gap-4">
                <div className="flex w-5 flex-col items-center">
                  <span
                    className={`mt-1 flex size-4 shrink-0 items-center justify-center rounded-full ${i === events.length - 1 && !complete ? "bg-accent text-primary" : "bg-muted text-muted-foreground"}`}
                  >
                    <span className="size-1.5 rounded-full bg-current" />
                  </span>
                  {i < events.length - 1 && (
                    <span className="min-h-8 w-px flex-1 bg-border" />
                  )}
                </div>
                <div className="flex flex-1 flex-wrap justify-between gap-3 pb-6 text-sm">
                  <span>{stageName(e.stage)}</span>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {seconds(e.elapsed_seconds)}
                  </span>
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-sm text-muted-foreground">
            No worker activity recorded yet.
          </p>
        )}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <div className="mt-3 flex flex-wrap gap-3">
          {complete && (
            <Button onClick={onResult}>
              Review your result <Download />
            </Button>
          )}
          {job.status === "queued" && !job.baseline && (
            <Button variant="outline" onClick={cancel} disabled={busy}>
              {busy ? <Loader2 className="animate-spin" /> : <XCircle />}Cancel
              queued job
            </Button>
          )}
          <Button variant="ghost" onClick={() => void onRefresh()}>
            <RefreshCw />
            Refresh status
          </Button>
        </div>
      </section>
      <aside className="rounded-2xl border border-border bg-background p-6">
        <h2 className="mb-5 text-sm font-semibold">Job details</h2>
        <dl className="space-y-5 text-sm">
          {[
            [
              "Transformation",
              job.options.operation === "pii"
                ? "PII redaction"
                : job.options.operation === "combined"
                  ? "Translation + PII"
                  : "Translation",
            ],
            ["Submitted", new Date(job.created * 1000).toLocaleString()],
            ["Server processing", seconds(job.metrics.server_seconds)],
            ["Worker-reported total", seconds(job.metrics.total_seconds)],
          ].map(([k, v]) => (
            <div key={k}>
              <dt className="mb-1 text-xs text-muted-foreground">{k}</dt>
              <dd className="m-0 break-words">{v}</dd>
            </div>
          ))}
        </dl>
        <p className="mb-0 mt-6 flex gap-2 border-t border-border pt-5 text-xs leading-5 text-muted-foreground">
          <ShieldCheck className="mt-1 size-4" /> Readiness is not a GPU
          inference guarantee. Actual device evidence belongs to the processing
          run.
        </p>
      </aside>
    </div>
  );
}
