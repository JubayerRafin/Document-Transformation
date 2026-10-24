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
  ArrowUpRight,
  FilePlus2,
  Files,
  FlaskConical,
  Loader2,
  LogOut,
  Menu,
  Server,
  ShieldCheck,
  X,
} from "lucide-react";
import { useWorkspace } from "@/hooks/use-workspace";
import { type Job, type Operation } from "@/lib/api";
import { Brand } from "./brand";
import { Button } from "./ui/button";
import { SignIn } from "./sign-in";
import { UploadPanel } from "./upload-panel";
import { HistoryPanel } from "./history-panel";
import { ProgressPanel } from "./progress-panel";
import { PreviewPanel } from "./preview-panel";
import { WorkersPanel } from "./workers-panel";
type View = "new" | "history" | "progress" | "result" | "servers";
const titles: Record<View, [string, string]> = {
  new: [
    "Make your document work for you",
    "Translate, redact and reconstruct your PDF in one focused workflow.",
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
  servers: [
    "Your processing servers",
    "Connect and manage the GPU servers you use for your documents.",
  ],
};
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
            { id: "servers", label: "GPU servers", icon: Server },
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
      <div className="mt-7 border-t border-border pt-6">
        <p className="mb-3 px-3 text-[10px] font-semibold uppercase tracking-[.15em] text-muted-foreground">
          Our workspace
        </p>
        <a
          href="/studio"
          className="flex items-center gap-3 rounded-lg px-3 py-3 text-sm text-muted-foreground hover:bg-muted"
        >
          <FlaskConical className="size-[18px]" />
          TransformX Studio
          <ArrowUpRight className="ml-auto size-3.5" />
        </a>
      </div>
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
                    : view === "servers"
                      ? "GPU servers"
                      : view === "history"
                        ? "Your documents"
                        : "Document details"}
                </span>
              </span>
            </div>
            {s.account ? (
              <div className="flex min-w-0 items-center gap-3">
                <div className="hidden min-w-0 text-right sm:block">
                  <p className="mb-0 max-w-52 truncate text-xs font-medium">
                    {s.account.name}
                  </p>
                  <p className="mb-0 mt-0.5 text-[10px] capitalize text-muted-foreground">
                    {s.account.role}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Sign out"
                  onClick={() => void s.logout()}
                >
                  <LogOut />
                </Button>
              </div>
            ) : (
              <Button size="sm" onClick={() => s.setSignIn(true)}>
                Sign in
              </Button>
            )}
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
                  Your next document starts here
                </h2>
                <p className="mx-auto mb-6 max-w-md text-sm leading-6 text-muted-foreground">
                  Sign in to upload documents, choose your processing server and
                  review your saved results.
                </p>
                <Button onClick={() => s.setSignIn(true)}>
                  Sign in to continue
                </Button>
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
                      onServers={() => navigate("servers")}
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
                  {view === "servers" && (
                    <WorkersPanel
                      api={s.api}
                      workers={s.workers}
                      onRefresh={s.refresh}
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
                Back to the website
              </a>
            </footer>
          </main>
        </div>
        <SignIn
          open={s.signIn}
          onOpenChange={s.setSignIn}
          api={s.api}
          onAccount={s.setAccount}
        />
      </div>
    </LazyMotion>
  );
}
