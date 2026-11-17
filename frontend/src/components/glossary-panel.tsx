"use client";
import { useEffect, useRef, useState } from "react";
import {
  BookText,
  Download,
  Loader2,
  Plus,
  Save,
  Trash2,
  Upload,
} from "lucide-react";
import { ApiClient } from "@/lib/api";
import {
  GLOSSARY_LIMIT,
  TERM_LIMIT,
  glossaryCsv,
  glossaryProblem,
  parseGlossaryCsv,
  type GlossaryEntry,
  type GlossarySummary,
} from "@/lib/glossary";
import { LANGUAGES, languageName } from "@/lib/languages";
import { Button } from "./ui/button";
import { Input, Label } from "./ui/input";
import { Badge } from "./ui/badge";

type Pair = { source: string; target: string };
const blank = (): GlossaryEntry => ({ source: "", target: "" });
const filled = (rows: GlossaryEntry[]) =>
  rows
    .map((r) => ({ source: r.source.trim(), target: r.target.trim() }))
    .filter((r) => r.source || r.target);

export function GlossaryPanel({
  api,
  onError,
  readOnly = false,
}: {
  api: ApiClient;
  onError: (e: unknown) => void;
  readOnly?: boolean;
}) {
  const [pair, setPair] = useState<Pair>({ source: "en", target: "de" }),
    [pending, setPending] = useState<Pair | null>(null),
    [rows, setRows] = useState<GlossaryEntry[]>([blank()]),
    [saved, setSaved] = useState<GlossaryEntry[]>([]),
    [summaries, setSummaries] = useState<GlossarySummary[]>([]),
    [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [error, setError] = useState(""),
    file = useRef<HTMLInputElement>(null);
  const dirty = JSON.stringify(filled(rows)) !== JSON.stringify(saved),
    problem = glossaryProblem(rows),
    count = filled(rows).length;

  async function refreshSummaries() {
    try {
      setSummaries(await api.glossaries());
    } catch (e) {
      onError(e);
    }
  }
  useEffect(() => {
    void refreshSummaries();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    const control = new AbortController();
    setLoading(true);
    setNotice("");
    setError("");
    api
      .glossary(pair.source, pair.target, control.signal)
      .then((g) => {
        setSaved(g.entries);
        setRows(g.entries.length ? g.entries : [blank()]);
      })
      .catch((e) => {
        if (!(e instanceof DOMException && e.name === "AbortError")) onError(e);
      })
      .finally(() => setLoading(false));
    return () => control.abort();
  }, [api, pair, onError]);

  function choose(next: Pair) {
    if (next.source === next.target) return;
    if (dirty) setPending(next);
    else setPair(next);
  }
  function edit(i: number, key: keyof GlossaryEntry, value: string) {
    setRows((r) => r.map((row, j) => (j === i ? { ...row, [key]: value } : row)));
    setNotice("");
  }
  async function save() {
    if (problem) return;
    setBusy(true);
    setError("");
    try {
      const g = await api.saveGlossary(pair.source, pair.target, filled(rows));
      setSaved(g.entries);
      setRows(g.entries.length ? g.entries : [blank()]);
      setNotice(
        g.entries.length
          ? `Saved. New ${languageName(pair.source)} → ${languageName(pair.target)} translations will use these ${g.entries.length} terms.`
          : "Saved. This language pair has no glossary now.",
      );
      await refreshSummaries();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Saving failed.");
      onError(e);
    } finally {
      setBusy(false);
    }
  }
  async function importCsv(f: File) {
    try {
      const entries = parseGlossaryCsv(await f.text());
      if (!entries.length) {
        setError("No terms found. Use two columns: the original term and its translation.");
        return;
      }
      // Imported terms replace rows with the same term; others are added.
      setRows((current) => {
        const merged = new Map(filled(current).map((r) => [r.source.toLowerCase(), r]));
        for (const e of entries) merged.set(e.source.toLowerCase(), e);
        return [...merged.values()];
      });
      setNotice(
        `${entries.length} ${entries.length === 1 ? "term" : "terms"} imported. Review them, then save.`,
      );
      setError("");
    } catch {
      setError("That file could not be read. Choose a CSV file.");
    }
  }
  function exportCsv() {
    const url = URL.createObjectURL(
      new Blob([glossaryCsv(filled(rows))], { type: "text/csv;charset=utf-8" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = `glossary-${pair.source}-${pair.target}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return (
    <div className="space-y-6">
      <section className="rounded-2xl border border-border bg-background p-6 sm:p-8">
        <h2 className="mb-2 text-lg font-semibold">Your terms, translated your way</h2>
        <p className="mb-6 max-w-2xl text-sm leading-6 text-muted-foreground">
          Add the translations your organization requires: product and
          department names, legal terms, words that must stay in English. Every
          new translation for this language pair uses them exactly, in every
          document. Matching ignores capitals and only whole words are replaced.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <div className="field w-44">
            <Label htmlFor="glossary-source">From</Label>
            <select
              id="glossary-source"
              className="flex h-11 w-full rounded-lg border border-input bg-background px-3 text-sm"
              value={pair.source}
              onChange={(e) =>
                choose({
                  source: e.target.value,
                  target: e.target.value === pair.target ? (e.target.value === "en" ? "de" : "en") : pair.target,
                })
              }
            >
              {LANGUAGES.map(([code, name]) => (
                <option key={code} value={code}>
                  {name}
                </option>
              ))}
            </select>
          </div>
          <div className="field w-52">
            <Label htmlFor="glossary-target">To</Label>
            <select
              id="glossary-target"
              className="flex h-11 w-full rounded-lg border border-input bg-background px-3 text-sm"
              value={pair.target}
              onChange={(e) => choose({ ...pair, target: e.target.value })}
            >
              {LANGUAGES.filter(([code]) => code !== pair.source).map(([code, name]) => (
                <option key={code} value={code}>
                  {name}
                </option>
              ))}
            </select>
          </div>
        </div>
        {summaries.length > 0 && (
          <div className="mt-5 flex flex-wrap gap-2" aria-label="Your glossaries">
            {summaries.map((g) => (
              <button
                key={`${g.source_language}-${g.target_language}`}
                type="button"
                onClick={() => choose({ source: g.source_language, target: g.target_language })}
                aria-pressed={g.source_language === pair.source && g.target_language === pair.target}
                className="rounded-full border border-border px-3 py-1.5 text-xs hover:bg-muted aria-pressed:border-primary aria-pressed:text-primary"
              >
                {languageName(g.source_language)} → {languageName(g.target_language)} · {g.terms}{" "}
                {g.terms === 1 ? "term" : "terms"}
              </button>
            ))}
          </div>
        )}
        {pending && (
          <div role="alert" className="mt-5 flex flex-wrap items-center gap-3 rounded-xl border border-border bg-muted p-4 text-sm">
            <span className="flex-1">You have unsaved changes for this language pair.</span>
            <Button variant="outline" size="sm" onClick={() => setPending(null)}>
              Keep editing
            </Button>
            <Button
              size="sm"
              variant="destructive"
              onClick={() => {
                setPair(pending);
                setPending(null);
              }}
            >
              Discard changes
            </Button>
          </div>
        )}
      </section>

      <section className="overflow-hidden rounded-2xl border border-border bg-background">
        <div className="flex flex-wrap items-center gap-3 border-b border-border p-6">
          <BookText className="size-5 text-primary" />
          <div className="min-w-0 flex-1">
            <h2 className="mb-1 text-base font-semibold">
              {languageName(pair.source)} → {languageName(pair.target)}
            </h2>
            <p className="mb-0 text-xs text-muted-foreground">
              {count} of {GLOSSARY_LIMIT} terms
              {dirty ? " · unsaved changes" : ""}
            </p>
          </div>
          {!readOnly && (
            <>
              <input
                ref={file}
                type="file"
                accept=".csv,.tsv,.txt,text/csv"
                className="hidden"
                aria-label="Import glossary CSV"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void importCsv(f);
                  e.target.value = "";
                }}
              />
              <Button variant="outline" size="sm" onClick={() => file.current?.click()}>
                <Upload />
                Import CSV
              </Button>
            </>
          )}
          <Button variant="outline" size="sm" onClick={exportCsv} disabled={!count}>
            <Download />
            Export CSV
          </Button>
        </div>
        {loading ? (
          <div role="status" className="flex items-center justify-center gap-3 p-12 text-sm">
            <Loader2 className="size-5 animate-spin text-primary" />
            Loading your glossary…
          </div>
        ) : (
          <div className="p-6">
            <div className="mb-2 hidden grid-cols-[1fr_1fr_40px] gap-3 text-xs font-medium text-muted-foreground sm:grid">
              <span>Term in {languageName(pair.source)}</span>
              <span>Required {languageName(pair.target)} translation</span>
              <span />
            </div>
            <ul className="m-0 list-none space-y-3 p-0">
              {rows.map((row, i) => (
                <li key={i} className="grid grid-cols-1 gap-2 sm:grid-cols-[1fr_1fr_40px] sm:gap-3">
                  <Input
                    aria-label={`Term ${i + 1} in ${languageName(pair.source)}`}
                    value={row.source}
                    maxLength={TERM_LIMIT}
                    placeholder="e.g. redaction"
                    disabled={readOnly}
                    onChange={(e) => edit(i, "source", e.target.value)}
                  />
                  <Input
                    aria-label={`Translation ${i + 1} in ${languageName(pair.target)}`}
                    value={row.target}
                    maxLength={TERM_LIMIT}
                    placeholder={pair.target === "de" ? "e.g. Schwärzung" : "Required translation"}
                    disabled={readOnly}
                    dir={["ar"].includes(pair.target) ? "rtl" : undefined}
                    onChange={(e) => edit(i, "target", e.target.value)}
                  />
                  {!readOnly && (
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Remove term ${i + 1}`}
                      onClick={() => setRows((r) => (r.length > 1 ? r.filter((_, j) => j !== i) : [blank()]))}
                    >
                      <Trash2 />
                    </Button>
                  )}
                </li>
              ))}
            </ul>
            {!readOnly && (
              <div className="mt-5 flex flex-wrap items-center gap-3">
                <Button
                  variant="outline"
                  onClick={() => setRows((r) => [...r, blank()])}
                  disabled={rows.length >= GLOSSARY_LIMIT}
                >
                  <Plus />
                  Add term
                </Button>
                <Button onClick={() => void save()} disabled={busy || !!problem || !dirty}>
                  {busy ? <Loader2 className="animate-spin" /> : <Save />}
                  Save glossary
                </Button>
                {problem && (
                  <p role="alert" className="mb-0 text-sm text-destructive">
                    {problem}
                  </p>
                )}
              </div>
            )}
            {notice && (
              <p role="status" className="mb-0 mt-4 text-sm text-primary">
                {notice}
              </p>
            )}
            {error && (
              <p role="alert" className="mb-0 mt-4 text-sm text-destructive">
                {error}
              </p>
            )}
          </div>
        )}
      </section>
      <p className="flex flex-wrap items-center gap-2 text-xs leading-5 text-muted-foreground">
        <Badge>Tip</Badge>
        To keep a name or brand in its original form, enter it as both the term
        and the translation (for example “HP ENTERPRISE” → “HP ENTERPRISE”).
        Changes apply to new translations; finished documents are not changed.
      </p>
    </div>
  );
}
