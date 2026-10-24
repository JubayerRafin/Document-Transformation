"use client";
import { useRef, useState, type FormEvent, type DragEvent } from "react";
import { m, useReducedMotion } from "motion/react";
import {
  ArrowRight,
  Check,
  ChevronDown,
  FileText,
  Globe2,
  Info,
  Layers2,
  Loader2,
  ShieldCheck,
  UploadCloud,
  X,
} from "lucide-react";
import {
  ApiClient,
  PII_CATEGORIES,
  validatePdf,
  type Operation,
  type TransformOptions,
  type Worker,
} from "@/lib/api";
import { Button } from "./ui/button";
import { Label, Textarea } from "./ui/input";
import { Checkbox } from "./ui/checkbox";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";
export function UploadPanel({
  api,
  workers,
  initialMode = "translation",
  onSubmitted,
  onServers,
  onError,
  readOnly = false,
}: {
  api: ApiClient;
  workers: Worker[];
  initialMode?: Operation;
  onSubmitted: (id: string) => Promise<void>;
  onServers: () => void;
  onError: (e: unknown) => void;
  readOnly?: boolean;
}) {
  const [file, setFile] = useState<File | null>(null),
    [fileError, setFileError] = useState(""),
    [dragging, setDragging] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [mode, setMode] = useState<Operation>(initialMode),
    [source, setSource] = useState("en"),
    [target, setTarget] = useState("ko"),
    [server, setServer] = useState(""),
    [context, setContext] = useState(""),
    [protectedValues, setProtected] = useState(""),
    [categories, setCategories] = useState<string[]>([
      "Names",
      "Email addresses",
      "Phone numbers",
    ]),
    [consent, setConsent] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null),
    selection = useRef(0),
    attempt = useRef<{ file: File; options: string; key: string } | null>(null),
    reduced = useReducedMotion();
  const worker = workers.find((w) => w.id === server),
    eligible = workers.filter((w) => w.enabled);
  const select = async (f?: File) => {
    if (!f) return;
    const version = ++selection.current;
    setFileError("");
    const problem = await validatePdf(f);
    if (version !== selection.current) return;
    if (problem) {
      setFile(null);
      setFileError(problem);
      return;
    }
    setFile(f);
    setError("");
  };
  const drop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (!busy) {
      if (e.dataTransfer.files.length !== 1) {
        selection.current++;
        setFile(null);
        setFileError("Choose one PDF at a time.");
        return;
      }
      void select(e.dataTransfer.files[0]);
    }
  };
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setError("");
    if (!file) {
      setFileError("Choose a PDF document to continue.");
      return;
    }
    if (!worker?.enabled) {
      setError("Choose one of your processing servers.");
      return;
    }
    if (mode !== "translation" && !categories.length) {
      setError("Select at least one PII category.");
      return;
    }
    if (!consent) {
      setError("Confirm that you are authorized to process this document.");
      return;
    }
    const options: TransformOptions = {
      operation: mode,
      source_language: source,
      target_language: target,
      worker_id: server,
      output_format: "pdf",
      protected_values: protectedValues
        .split("\n")
        .map((v) => v.trim())
        .filter(Boolean),
      document_context: context,
      pii_categories: mode === "translation" ? [] : categories,
    };
    if (
      options.protected_values.length > 200 ||
      options.protected_values.some((v) => v.length > 500)
    ) {
      setError(
        "Use up to 200 protected values, each no longer than 500 characters.",
      );
      return;
    }
    const serialized = JSON.stringify(options);
    if (
      attempt.current?.file !== file ||
      attempt.current?.options !== serialized
    )
      attempt.current = { file, options: serialized, key: crypto.randomUUID() };
    const session = api.csrf;
    setBusy(true);
    try {
      const result = await api.submit(file, options, attempt.current.key);
      if (api.csrf !== session) return;
      await onSubmitted(result.job_id);
    } catch (e) {
      if (api.csrf !== session) return;
      setError(
        e instanceof Error ? e.message : "Submission failed. Try again.",
      );
      onError(e);
    } finally {
      setBusy(false);
    }
  }
  if (readOnly)
    return (
      <div className="rounded-xl border border-border bg-background p-8">
        <h2 className="text-lg font-semibold">
          Your account has read-only access
        </h2>
        <p className="mb-0 mt-2 text-sm text-muted-foreground">
          You can review shared results. Ask your administrator for a processing
          account to upload documents.
        </p>
      </div>
    );
  return (
    <div className="grid items-start gap-7 xl:grid-cols-[minmax(0,1fr)_280px]">
      <form onSubmit={submit} className="min-w-0 space-y-6">
        <fieldset
          disabled={busy}
          className="min-w-0 space-y-6 disabled:opacity-80"
        >
          <section className="rounded-2xl border border-border bg-background p-5 sm:p-7">
            <div className="mb-5 flex items-center gap-3">
              <span className="flex size-6 items-center justify-center rounded-full bg-accent text-xs font-semibold text-primary">
                1
              </span>
              <h2 className="mb-0 text-base font-semibold">
                Add your document
              </h2>
            </div>
            <input
              ref={fileInput}
              type="file"
              id="document-upload"
              className="sr-only"
              accept=".pdf,application/pdf"
              onChange={(e) => {
                void select(e.target.files?.[0]);
                e.target.value = "";
              }}
              aria-label="Choose a PDF"
              aria-describedby="upload-hint"
            />
            <div
              onDragOver={(e) => {
                e.preventDefault();
                if (!busy) setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={drop}
              className={`rounded-xl border-2 border-dashed p-7 text-center transition-colors sm:p-9 ${dragging ? "border-primary bg-accent" : "border-border bg-muted/60"}`}
            >
              {file ? (
                <div className="flex items-center gap-4 text-left">
                  <div className="flex size-12 shrink-0 items-center justify-center rounded-xl border border-primary/25 bg-background text-primary">
                    <FileText className="size-6" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="mb-1 truncate text-sm font-semibold">
                      {file.name}
                    </p>
                    <p className="mb-0 text-xs text-muted-foreground">
                      {(file.size / 1_000_000).toFixed(2)} MB · PDF document
                    </p>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    type="button"
                    aria-label="Remove document"
                    onClick={() => {
                      selection.current++;
                      setFile(null);
                    }}
                  >
                    <X />
                  </Button>
                </div>
              ) : (
                <>
                  <div className="mx-auto mb-4 flex size-12 items-center justify-center rounded-xl border border-border bg-background text-primary">
                    <UploadCloud className="size-6" strokeWidth={1.6} />
                  </div>
                  <p className="mb-1 text-sm font-medium">Drop your PDF here</p>
                  <p className="mb-4 text-xs text-muted-foreground">
                    or choose a file from your device
                  </p>
                  <Button
                    variant="outline"
                    type="button"
                    onClick={() => fileInput.current?.click()}
                  >
                    Choose a document
                  </Button>
                </>
              )}
            </div>
            <p
              id="upload-hint"
              className="mb-0 mt-3 text-xs leading-5 text-muted-foreground"
            >
              One PDF per job. Maximum 50 MB and 200 pages. Encrypted PDFs are
              not supported.
            </p>
            {fileError && (
              <p className="mb-0 mt-3 text-sm text-destructive" role="alert">
                {fileError}
              </p>
            )}
          </section>
          <section className="rounded-2xl border border-border bg-background p-5 sm:p-7">
            <div className="mb-5 flex items-center gap-3">
              <span className="flex size-6 items-center justify-center rounded-full bg-accent text-xs font-semibold text-primary">
                2
              </span>
              <h2 className="mb-0 text-base font-semibold">
                Choose your transformation
              </h2>
            </div>
            <Tabs value={mode} onValueChange={(v) => setMode(v as Operation)}>
              <TabsList
                aria-label="Transformation type"
                className="mb-6 flex w-full"
              >
                <TabsTrigger value="translation" disabled={busy}>
                  <Globe2 className="hidden size-4 sm:block" />
                  Translation
                </TabsTrigger>
                <TabsTrigger value="pii" disabled={busy}>
                  <ShieldCheck className="hidden size-4 sm:block" />
                  PII redaction
                </TabsTrigger>
                <TabsTrigger value="combined" disabled={busy}>
                  <Layers2 className="hidden size-4 sm:block" />
                  Both
                </TabsTrigger>
              </TabsList>
              {(["translation", "pii", "combined"] as const).map((value) => (
                <TabsContent key={value} value={value} className="sr-only">
                  {value === "translation"
                    ? "Translate your PDF into your selected language."
                    : value === "pii"
                      ? "Select the information you want to redact."
                      : "Translate your document and redact selected information."}
                </TabsContent>
              ))}
            </Tabs>
            {mode !== "pii" && (
              <m.div
                initial={reduced ? false : { opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                className="mb-6 grid gap-4 sm:grid-cols-2"
              >
                <div className="field">
                  <Label htmlFor="source-language">Source language</Label>
                  <select
                    id="source-language"
                    className="select-input"
                    value={source}
                    onChange={(e) => setSource(e.target.value)}
                  >
                    <option value="en">English</option>
                    <option value="ko">Korean</option>
                    <option value="ja">Japanese</option>
                  </select>
                </div>
                <div className="field">
                  <Label htmlFor="target-language">Target language</Label>
                  <select
                    id="target-language"
                    className="select-input"
                    value={target}
                    onChange={(e) => setTarget(e.target.value)}
                  >
                    <option value="ko">Korean</option>
                    <option value="en">English</option>
                    <option value="ja">Japanese</option>
                  </select>
                </div>
              </m.div>
            )}
            {mode !== "translation" && (
              <m.div
                initial={reduced ? false : { opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                className="mb-6"
              >
                <p className="mb-3 text-sm font-medium">
                  Information to redact
                </p>
                <div className="grid gap-3 sm:grid-cols-2">
                  {PII_CATEGORIES.map((c) => (
                    <Label
                      key={c}
                      className="flex cursor-pointer items-center gap-3 rounded-lg border border-border px-3 py-3 font-normal"
                    >
                      <Checkbox
                        checked={categories.includes(c)}
                        onCheckedChange={(checked) =>
                          setCategories((v) =>
                            checked ? [...v, c] : v.filter((x) => x !== c),
                          )
                        }
                      />
                      {c}
                    </Label>
                  ))}
                </div>
                <p className="mb-0 mt-3 text-xs leading-5 text-muted-foreground">
                  PII evaluation is still pending. Review for missed sensitive
                  content and unnecessary redaction.
                </p>
              </m.div>
            )}
            <div className="field">
              <Label htmlFor="document-context">
                Document context{" "}
                <span className="font-normal text-muted-foreground">
                  (optional)
                </span>
              </Label>
              <Textarea
                id="document-context"
                maxLength={8000}
                rows={3}
                value={context}
                onChange={(e) => setContext(e.target.value)}
                placeholder="Audience, subject area or terminology to keep in mind…"
              />
              <span className="text-right text-[11px] text-muted-foreground">
                {context.length.toLocaleString()} / 8,000
              </span>
            </div>
            <details className="mt-4 border-t border-border pt-4">
              <summary className="flex items-center justify-between text-sm font-medium">
                Protected names and values{" "}
                <ChevronDown className="size-4 text-muted-foreground" />
              </summary>
              <div className="field mt-4">
                <Label
                  htmlFor="protected-values"
                  className="font-normal text-muted-foreground"
                >
                  Keep these values unchanged during translation, one per line
                </Label>
                <Textarea
                  id="protected-values"
                  rows={3}
                  value={protectedValues}
                  onChange={(e) => setProtected(e.target.value)}
                  placeholder="Product names, references or identifiers"
                />
                <p className="mb-0 text-xs leading-5 text-muted-foreground">
                  Protecting a value for translation does not exempt it from
                  selected PII redaction.
                </p>
              </div>
            </details>
          </section>
          <section className="rounded-2xl border border-border bg-background p-5 sm:p-7">
            <div className="mb-5 flex items-center gap-3">
              <span className="flex size-6 items-center justify-center rounded-full bg-accent text-xs font-semibold text-primary">
                3
              </span>
              <h2 className="mb-0 text-base font-semibold">
                Select your processing server
              </h2>
            </div>
            <div className="field">
              <Label htmlFor="processing-server">GPU server</Label>
              <select
                id="processing-server"
                className="select-input"
                value={server}
                onChange={(e) => setServer(e.target.value)}
                required
              >
                <option value="">Choose a connected server</option>
                {eligible.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.name} ·{" "}
                    {w.ready ? "Ready" : w.online ? "Not ready" : "Offline"}
                  </option>
                ))}
              </select>
            </div>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
              <p className="mb-0 text-xs leading-5 text-muted-foreground">
                {worker?.ready
                  ? "This server reports its processing prerequisites are ready."
                  : worker
                    ? "Your job will wait for this server to become ready."
                    : "Jobs run on a private server registered to your account."}
              </p>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={onServers}
              >
                Manage servers <ArrowRight />
              </Button>
            </div>
            {worker?.ready &&
              !worker.capabilities?.operations?.includes(mode) && (
                <p className="mb-0 mt-3 text-xs text-amber-200">
                  This server does not currently report support for the selected
                  operation. The job will wait.
                </p>
              )}
          </section>
          <Label className="flex items-start gap-3 px-1 text-sm font-normal leading-6">
            <Checkbox
              className="mt-1"
              checked={consent}
              onCheckedChange={(v) => setConsent(v === true)}
            />
            I am authorized to process this document.
          </Label>
        </fieldset>
        {error && (
          <div
            role="alert"
            className="rounded-xl border border-red-800 bg-red-950 p-4 text-sm text-destructive"
          >
            {error}
          </div>
        )}
        <div className="flex flex-wrap items-center justify-between gap-4">
          <p className="mb-0 flex items-center gap-2 text-xs text-muted-foreground">
            <ShieldCheck className="size-4" /> Your files stay private to
            permitted accounts.
          </p>
          <Button type="submit" size="lg" disabled={busy}>
            {busy ? <Loader2 className="animate-spin" /> : <ArrowRight />}
            {busy ? "Uploading document…" : "Start transformation"}
          </Button>
        </div>
      </form>
      <aside className="space-y-5 xl:sticky xl:top-7">
        <div className="rounded-2xl border border-primary/25 bg-accent/70 p-6">
          <div className="mb-4 inline-flex size-9 items-center justify-center rounded-lg bg-background text-primary">
            <FileText className="size-5" />
          </div>
          <h2 className="mb-3 text-sm font-semibold">
            A little context goes a long way
          </h2>
          <p className="mb-0 text-sm leading-6 text-muted-foreground">
            Tell us who the document is for and which terminology matters. Your
            original document stays available for comparison.
          </p>
        </div>
        <div className="px-2">
          <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            What happens next
          </p>
          <ol className="space-y-4 text-xs text-muted-foreground">
            {[
              "Your document enters the selected server’s queue.",
              "The engine extracts, transforms and reconstructs your document.",
              "Your result appears here for review and download.",
            ].map((t) => (
              <li key={t} className="flex gap-2.5 leading-5">
                <Check className="mt-1 size-3.5 text-primary" />
                {t}
              </li>
            ))}
          </ol>
          <p className="mb-0 mt-5 flex items-start gap-2 border-t border-border pt-4 text-xs leading-5 text-muted-foreground">
            <Info className="mt-0.5 size-3.5" /> Generated output needs review.
            Processing speed depends on the document and server.
          </p>
        </div>
      </aside>
    </div>
  );
}
