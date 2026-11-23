"use client";
import { useEffect, useState } from "react";
import {
  AnimatePresence,
  LazyMotion,
  domAnimation,
  m,
  useReducedMotion,
} from "motion/react";
import {
  ArrowLeft,
  BookText,
  FilePlus2,
  Files,
  Loader2,
  Menu,
  Server,
  ShieldCheck,
  X,
} from "lucide-react";
import { useWorkspace } from "@/hooks/use-workspace";
import { type Job, type Operation, type Worker } from "@/lib/api";
import { Brand } from "./brand";
import { Button } from "./ui/button";
import { UploadPanel } from "./upload-panel";
import { HistoryPanel } from "./history-panel";
import { ProgressPanel } from "./progress-panel";
import { PreviewPanel } from "./preview-panel";
import { GlossaryPanel } from "./glossary-panel";
type View = "new" | "history" | "progress" | "result" | "glossary";
const titles: Record<View, [string, string]> = {
  new: [
    "Make your document work for you",
    "Translate, redact and reconstruct your PDF, Word or PowerPoint document in one focused workflow.",
  ],
  history: ["Your documents", "Find your recent jobs and review the results."],
  progress: [
    "Your transformation",
    "Follow the stages reported by your processing server.",
  ],
  result: [
    "Review your result",
    "Compare the original and transformed document, side by side.",
  ],
  glossary: [
    "Your glossary",
    "Set how your own terms are translated, for each language pair.",
  ],
};
/** This server's GPU and whether its models are ready (the server checks itself every 45 s). */
function ServerStatus({ server }: { server?: Worker }) {
  if (!server) return null;
  return (
    <div className="flex min-w-0 items-center gap-2 text-xs" title={server.capabilities?.note || undefined}>
      <Server className="size-4 text-muted-foreground" />
      <span className="hidden max-w-56 truncate sm:inline">{server.name}</span>
      <span
        className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${server.ready ? "bg-emerald-950 text-emerald-300" : "bg-amber-950 text-amber-300"}`}
      >
        {server.ready ? "Ready" : "Not ready"}
      </span>
    </div>
  );
}
export function Workspace() {
  const s = useWorkspace(),
    [view, setView] = useState<View>("new"),
    [selected, setSelected] = useState<string | null>(null),
    [extra, setExtra] = useState<Job | null>(null),
    [mobile, setMobile] = useState(false),
    [initialMode, setInitialMode] = useState<Operation>("translation"),
    reduced = useReducedMotion();
  const job =
      s.jobs.find((j) => j.id === selected) ||
      (extra?.id === selected ? extra : null),
    readOnly = s.account?.role === "viewer";
  useEffect(() => {
    const q = new URLSearchParams(location.search);
    if (q.get("view") === "history") setView("history");
    const mode = q.get("mode");
    if (mode === "pii" || mode === "combined") setInitialMode(mode);
  }, []);
  useEffect(() => {
    if (!s.account) {
      setSelected(null);
      setExtra(null);
    }
  }, [s.account]);
  function navigate(v: View) {
    setView(v);
    setMobile(false);
  }
  function open(j: Job) {
    if (j.options.operation === "manual_redaction" && j.status === "uploading") {
      location.assign(`/redaction-review.html?job=${encodeURIComponent(j.id)}`);
      return;
    }
    setSelected(j.id);
    setExtra(j);
    navigate(j.status === "succeeded" ? "result" : "progress");
  }
  async function submitted(id: string) {
    const session = s.api.csrf;
    const record = await s.api.request<Job>(
      `/v1/jobs/${encodeURIComponent(id)}`,
    );
    if (s.api.csrf !== session) return;
    setSelected(id);
    setExtra(record);
    navigate("progress");
    await s.refresh();
  }
  const nav = (
    <>
      <nav aria-label="Workspace navigation" className="grid gap-1.5">
        {(
          [
            { id: "new", label: "New transformation", icon: FilePlus2 },
            { id: "history", label: "Your documents", icon: Files },
            { id: "glossary", label: "Glossary", icon: BookText },
          ] as const
        ).map((n) => (
          <m.button
            whileHover={reduced ? undefined : { x: 4 }}
            whileTap={reduced ? undefined : { scale: 0.98 }}
            transition={{ duration: 0.18 }}
            key={n.id}
            onClick={() => navigate(n.id)}
            aria-current={view === n.id ? "page" : undefined}
            className={`flex items-center gap-3 rounded-lg px-3 py-3 text-left text-sm font-medium transition-colors ${view === n.id ? "bg-accent text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground"}`}
          >
            <n.icon className="size-[18px]" />
            {n.label}
          </m.button>
        ))}
      </nav>
    </>
  );
  return (
    <LazyMotion features={domAnimation}>
      <div className="min-h-screen bg-muted/30">
        <aside className="fixed inset-y-0 left-0 z-30 hidden w-[238px] flex-col border-r border-border bg-background p-5 lg:flex">
          <div className="mb-10 mt-3 px-2">
            <Brand />
          </div>
          {nav}
          <div className="mt-auto rounded-xl bg-muted p-4">
            <ShieldCheck className="mb-3 size-5 text-primary" />
            <p className="mb-1 text-xs font-medium">
              Your documents, your control
            </p>
            <p className="mb-0 text-[11px] leading-5 text-muted-foreground">
              Choose your transformation and review the result before you share.
            </p>
          </div>
        </aside>
        <div className="lg:pl-[238px]">
          <header className="sticky top-0 z-20 flex min-h-[76px] items-center justify-between gap-3 border-b border-border bg-background/95 px-5 backdrop-blur-sm sm:px-8">
            <div className="flex items-center gap-3">
              <Button
                variant="ghost"
                size="icon"
                className="lg:hidden"
                aria-label={mobile ? "Close navigation" : "Open navigation"}
                aria-expanded={mobile}
                aria-controls="mobile-navigation"
                onClick={() => setMobile((v) => !v)}
              >
                {mobile ? <X /> : <Menu />}
              </Button>
              <div className="lg:hidden">
                <Brand />
              </div>
              <span className="hidden text-xs text-muted-foreground lg:block">
                Document workspace{" "}
                <span className="mx-2 text-muted-foreground">/</span>
                <span className="text-foreground">
                  {view === "new"
                    ? "New transformation"
                    : view === "glossary"
                        ? "Glossary"
                      : view === "history"
                        ? "Your documents"
                        : "Document details"}
                </span>
              </span>
            </div>
            <ServerStatus server={s.workers[0]} />
          </header>
          {mobile && (
            <m.div
              initial={reduced ? false : { opacity: 0, y: -12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.25 }}
              id="mobile-navigation"
              className="border-b border-border bg-background p-5 lg:hidden"
            >
              {nav}
            </m.div>
          )}
          <main
            id="main"
            className="mx-auto max-w-[1500px] px-5 py-8 sm:px-8 sm:py-10 xl:px-10"
          >
            <m.div key={view} initial={reduced ? false : {opacity:0,y:12}} animate={{opacity:1,y:0}} transition={{duration:0.32}} className="mb-8 flex flex-wrap items-start justify-between gap-4">
              <div>
                <p className="mb-2 text-[10px] font-semibold uppercase tracking-[.18em] text-primary">
                  TransformX / Documents
                </p>
                <h1 className="mb-2 text-2xl font-semibold tracking-tight sm:text-[28px]">
                  {titles[view][0]}
                </h1>
                <p className="mb-0 text-sm leading-6 text-muted-foreground">
                  {titles[view][1]}
                </p>
              </div>
              {s.account && view !== "new" && !readOnly && (
                <Button onClick={() => navigate("new")}>
                  <FilePlus2 />
                  New transformation
                </Button>
              )}
            </m.div>
            {s.error && (
              <div
                role="alert"
                className="mb-5 flex items-start justify-between gap-4 rounded-xl border border-red-800 bg-red-950 p-4 text-sm text-destructive"
              >
                <span>{s.error}</span>
                <button
                  aria-label="Dismiss notification"
                  onClick={() => s.setError("")}
                >
                  <X className="size-4" />
                </button>
              </div>
            )}
            {s.loading ? (
              <div
                role="status"
                className="flex items-center justify-center gap-3 rounded-2xl border border-border bg-background p-16"
              >
                <Loader2 className="size-5 animate-spin text-primary" />
                Loading your workspace…
              </div>
            ) : !s.account ? (
              <section className="rounded-2xl border border-border bg-background px-6 py-20 text-center">
                <Files className="mx-auto mb-5 size-10 text-primary" />
                <h2 className="mb-3 text-xl font-semibold">
                  The TransformX server is not answering
                </h2>
                <p className="mx-auto mb-6 max-w-md text-sm leading-6 text-muted-foreground">
                  Check that TransformX is running on the GPU server and that this
                  address still reaches it, then reload the page.
                </p>
                <Button onClick={() => location.reload()}>Reload</Button>
              </section>
            ) : (
              <AnimatePresence mode="wait" initial={false}>
                <m.div
                  key={`${view}-${view === "new" ? initialMode : selected || ""}`}
                  className="tx-workspace-view"
                  initial={reduced ? false : { opacity: 0, y: 18 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={reduced ? { opacity: 1 } : { opacity: 0 }}
                  transition={{ duration: 0.32, ease: [0.22, 1, 0.36, 1] }}
                >
                  {view === "new" && (
                    <UploadPanel
                      api={s.api}
                      workers={s.workers}
                      initialMode={initialMode}
                      onSubmitted={submitted}
                      onError={s.handleError}
                      readOnly={readOnly}
                    />
                  )}
                  {view === "history" && (
                    <HistoryPanel
                      jobs={s.jobs}
                      onSelect={open}
                      onNew={() => navigate("new")}
                      readOnly={readOnly}
                    />
                  )}
                  {view === "glossary" && (
                    <GlossaryPanel
                      api={s.api}
                      onError={s.handleError}
                      readOnly={readOnly}
                    />
                  )}
                  {(view === "progress" || view === "result") && (
                    <>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="mb-5 -ml-2"
                        onClick={() => navigate("history")}
                      >
                        <ArrowLeft />
                        Back to your documents
                      </Button>
                      {job ? (
                        view === "result" ? (
                          <PreviewPanel
                            job={job}
                            api={s.api}
                            onError={s.handleError}
                            readOnly={readOnly}
                          />
                        ) : (
                          <ProgressPanel
                            job={job}
                            api={s.api}
                            onRefresh={s.refresh}
                            onResult={() => navigate("result")}
                            onError={s.handleError}
                          />
                        )
                      ) : (
                        <p>Select a document from your history.</p>
                      )}
                    </>
                  )}
                </m.div>
              </AnimatePresence>
            )}
            <footer className="mt-12 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-5 text-[11px] text-muted-foreground">
              <span>TransformX · Document transformation with context</span>
              <a href="/" className="hover:text-primary">
                About TransformX
              </a>
            </footer>
          </main>
        </div>
      </div>
    </LazyMotion>
  );
}
