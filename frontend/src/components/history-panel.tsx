"use client";
import { useMemo, useState } from "react";
import { ArrowRight, FilePlus2, FileText, Search } from "lucide-react";
import type { Job } from "@/lib/api";
import { stageName, seconds } from "@/lib/api";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Badge } from "./ui/badge";
export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge
      className={
        status === "succeeded"
          ? "border-emerald-800 bg-emerald-950 text-emerald-200"
          : status === "running"
            ? "border-primary/25 bg-accent text-primary"
            : status === "failed" || status === "interrupted"
              ? "border-red-800 bg-red-950 text-red-200"
              : "bg-muted text-muted-foreground"
      }
    >
      <span
        className={`size-1.5 rounded-full ${status === "succeeded" ? "bg-emerald-400" : status === "running" ? "bg-primary" : status === "failed" || status === "interrupted" ? "bg-red-400" : "bg-slate-400"}`}
      />
      {status === "succeeded" ? "Generated" : stageName(status)}
    </Badge>
  );
}
export function HistoryPanel({
  jobs,
  onSelect,
  onNew,
  readOnly = false,
}: {
  jobs: Job[];
  onSelect: (j: Job) => void;
  onNew: () => void;
  readOnly?: boolean;
}) {
  const [search, setSearch] = useState(""),
    [filter, setFilter] = useState("all");
  const filtered = useMemo(
    () =>
      jobs.filter(
        (j) =>
          j.filename.toLocaleLowerCase().includes(search.toLocaleLowerCase()) &&
          (filter === "all" || j.status === filter),
      ),
    [jobs, search, filter],
  );
  return (
    <section className="overflow-hidden rounded-2xl border border-border bg-background">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border p-5">
        <div className="relative w-full sm:max-w-xs">
          <Search className="absolute left-3 top-3.5 size-4 text-muted-foreground" />
          <Input
            aria-label="Search documents"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9"
            placeholder="Search your documents…"
          />
        </div>
        <select
          aria-label="Filter job status"
          className="select-input max-w-[180px]"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        >
          <option value="all">All statuses</option>
          <option value="queued">Queued</option>
          <option value="running">Processing</option>
          <option value="succeeded">Generated</option>
          <option value="failed">Failed</option>
          <option value="interrupted">Interrupted</option>
          <option value="cancelled">Cancelled</option>
        </select>
      </div>
      {filtered.length ? (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">
              Your latest available document transformation jobs
            </caption>
            <thead className="border-b border-border bg-muted/60 text-[11px] uppercase tracking-wider text-muted-foreground">
              <tr>
                {[
                  "Document",
                  "Transformation",
                  "Status",
                  "Server time",
                  "",
                ].map((t, i) => (
                  <th
                    key={i}
                    scope="col"
                    className="whitespace-nowrap px-5 py-3.5 font-medium"
                  >
                    {t || <span className="sr-only">Open job</span>}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {filtered.map((j) => (
                <tr
                  key={j.id}
                  className="transition-colors hover:bg-muted/70"
                >
                  <td className="max-w-[310px] px-5 py-5">
                    <div className="flex items-center gap-3">
                      <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-accent text-primary">
                        <FileText className="size-5" strokeWidth={1.5} />
                      </div>
                      <div className="min-w-0">
                        <button
                          className="block max-w-[240px] truncate text-left font-medium hover:text-primary"
                          onClick={() => onSelect(j)}
                        >
                          {j.filename}
                        </button>
                        <div className="mt-1 text-xs text-muted-foreground">
                          {new Date(j.created * 1000).toLocaleDateString(
                            undefined,
                            { month: "short", day: "numeric" },
                          )}
                          {j.pages > 0 ? ` · ${j.pages} pages` : ""}
                          {j.baseline ? " · Saved baseline" : ""}
                        </div>
                      </div>
                    </div>
                  </td>
                  <td className="whitespace-nowrap px-5 py-5 text-muted-foreground">
                    {j.options.operation === "pii"
                      ? "PII redaction"
                      : j.options.operation === "combined"
                        ? "Translation + PII"
                        : "Translation"}
                  </td>
                  <td className="whitespace-nowrap px-5 py-5">
                    <StatusBadge status={j.status} />
                  </td>
                  <td className="whitespace-nowrap px-5 py-5 text-muted-foreground tabular-nums">
                    {seconds(j.metrics.server_seconds)}
                  </td>
                  <td className="px-5 py-5">
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Open ${j.filename}`}
                      onClick={() => onSelect(j)}
                    >
                      <ArrowRight />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="flex flex-col items-center px-6 py-16 text-center">
          <div className="empty-illustration mb-4 flex size-20 items-center justify-center text-primary">
            <FileText className="size-9" strokeWidth={1.2} />
          </div>
          <h2 className="mb-2 text-lg font-semibold">
            {search || filter !== "all"
              ? "No matching documents"
              : "Your documents start here"}
          </h2>
          <p className="mb-5 max-w-sm text-sm leading-6 text-muted-foreground">
            {search || filter !== "all"
              ? "Try a different search or status filter."
              : "Upload a PDF to translate it, redact sensitive information and review the result."}
          </p>
          {!search && filter === "all" && !readOnly && (
            <Button onClick={onNew}>
              <FilePlus2 />
              New transformation
            </Button>
          )}
        </div>
      )}
      <div className="border-t border-border px-5 py-3 text-xs text-muted-foreground">
        {filtered.length} shown · Latest available records, up to 100
      </div>
    </section>
  );
}
