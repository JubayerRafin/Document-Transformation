"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { Eye, Loader2 } from "lucide-react";
import { ApiClient, type Job, type RedactedValues } from "@/lib/api";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";

/** A value as it appeared in the document; "↵" where a line break cut it. */
export function shown(value: string) {
  return value.split(/\s*\n\s*/).join(" ↵ ");
}

/** Pages of a value, "p. 1, 3" (or "p. 1–4, 9" for longer runs). */
export function pageList(pages: number[]) {
  if (!pages.length) return "";
  const runs: string[] = [];
  for (let i = 0; i < pages.length; ) {
    let j = i;
    while (j + 1 < pages.length && pages[j + 1] === pages[j] + 1) j++;
    runs.push(j - i >= 2 ? `${pages[i]}–${pages[j]}` : pages.slice(i, j + 1).join(", "));
    i = j + 1;
  }
  return `p. ${runs.join(", ")}`;
}

/**
 * The values a PII job removed, each with "Keep visible". Applying makes a new version of the job
 * from the same original with the chosen values left visible; this result stays as it is.
 */
export function RedactedValuesPanel({
  job,
  api,
  onError,
  onCreated,
  readOnly = false,
}: {
  job: Job;
  api: ApiClient;
  onError: (e: unknown) => void;
  onCreated: (id: string) => void;
  readOnly?: boolean;
}) {
  const [data, setData] = useState<RedactedValues | null>(null),
    [failed, setFailed] = useState(""),
    [keep, setKeep] = useState<Set<string>>(new Set()),
    [busy, setBusy] = useState(false),
    [open, setOpen] = useState(false),
    attempt = useRef<{ list: string; key: string } | null>(null);
  useEffect(() => {
    const c = new AbortController();
    setData(null);
    setFailed("");
    api
      .redactedValues(job.id, c.signal)
      .then((d) => {
        if (c.signal.aborted) return;
        setData(d);
        setKeep(new Set(d.kept));
      })
      .catch((e) => {
        if (!c.signal.aborted) setFailed(e instanceof Error ? e.message : "The list is unavailable.");
      });
    return () => c.abort();
  }, [api, job.id]);
  const before = useMemo(() => new Set(data?.kept || []), [data]);
  const changed =
    keep.size !== before.size || [...keep].some((v) => !before.has(v));
  // Values kept in this version that the engine therefore did not list as removed.
  const keptOnly = (data?.kept || []).filter(
    (k) => !data?.values.some((v) => v.value === k),
  );
  function toggle(value: string, on: boolean) {
    setKeep((current) => {
      const next = new Set(current);
      if (on) next.add(value);
      else next.delete(value);
      return next;
    });
  }
  async function apply() {
    const list = [...keep];
    const signature = JSON.stringify(list);
    // The same choice sent twice (a double click, a retry) makes one new version.
    if (attempt.current?.list !== signature)
      attempt.current = { list: signature, key: crypto.randomUUID().replace(/-/g, "") };
    setBusy(true);
    try {
      const { job_id } = await api.keepVisible(job.id, list, attempt.current.key);
      onCreated(job_id);
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }
  if (failed) return null;
  const count = data ? data.values.length : 0;
  return (
    <section
      aria-label="Removed values"
      className="rounded-xl border border-border bg-background px-4 py-3 sm:px-6"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="mb-0 text-sm font-medium">
            {data
              ? `${count} value${count === 1 ? "" : "s"} removed${data.kept.length ? ` · ${data.kept.length} kept visible` : ""}`
              : "Loading the removed values…"}
          </p>
          <p className="mb-0 text-xs text-muted-foreground">
            Something removed that should stay? Tick “Keep visible” and make a new version.
          </p>
        </div>
        {data && (count > 0 || keptOnly.length > 0) && (
          <Button size="sm" variant="outline" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
            <Eye />
            {open ? "Hide the list" : "Show what was removed"}
          </Button>
        )}
      </div>
      {open && data && (
        <div className="mt-3">
          <ul className="m-0 max-h-96 list-none space-y-1 overflow-y-auto p-0" data-testid="removed-values">
            {[...data.values.map((v) => ({ ...v, keptOnly: false })),
              ...keptOnly.map((k) => ({ value: k, category: "Kept visible", occurrences: 0, pages: [] as number[], parts: [] as string[], keptOnly: true }))].map((v) => {
              const id = `keep-${job.id}-${v.value}`;
              return (
                <li key={v.value} className="flex items-start gap-3 rounded-md px-2 py-1.5 hover:bg-muted">
                  <Checkbox
                    id={id}
                    className="mt-0.5"
                    checked={keep.has(v.value)}
                    disabled={readOnly || busy}
                    onCheckedChange={(on) => toggle(v.value, on === true)}
                    aria-label={`Keep visible: ${shown(v.value)}`}
                  />
                  <label htmlFor={id} className="min-w-0 flex-1 cursor-pointer text-sm">
                    <span className="break-words font-medium">{shown(v.value)}</span>
                    <span className="block text-xs text-muted-foreground">
                      {[v.category,
                        pageList(v.pages),
                        v.occurrences > 1 ? `${v.occurrences} times` : "",
                        v.parts.length ? `also as ${v.parts.map((p) => `“${p}”`).join(", ")}` : "",
                        v.keptOnly ? "untick to remove it again" : ""]
                        .filter(Boolean)
                        .join(" · ")}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
          {data.truncated && (
            <p className="mb-0 mt-2 text-xs text-muted-foreground">
              Only the first 400 values are listed.
            </p>
          )}
          {!readOnly && (
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3">
              <p className="mb-0 max-w-prose text-xs text-muted-foreground">
                The new version is made again from the original document, with the ticked values left visible.
                This result stays in your documents.
              </p>
              <Button size="sm" onClick={apply} disabled={!changed || busy}>
                {busy && <Loader2 className="animate-spin" />}
                Make a new version
              </Button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
