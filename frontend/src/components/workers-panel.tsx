"use client";
import { useState, type FormEvent } from "react";
import {
  Download,
  Loader2,
  Plus,
  Server,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { ApiClient, type Worker } from "@/lib/api";
import { Button } from "./ui/button";
import { Input, Label } from "./ui/input";
import { Badge } from "./ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "./ui/dialog";
export function WorkersPanel({
  api,
  workers,
  onRefresh,
  onError,
  readOnly = false,
}: {
  api: ApiClient;
  workers: Worker[];
  onRefresh: () => Promise<void>;
  onError: (e: unknown) => void;
  readOnly?: boolean;
}) {
  const [name, setName] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [config, setConfig] = useState<object | null>(null),
    [revoke, setRevoke] = useState<Worker | null>(null);
  async function register(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setConfig(null);
    try {
      const w = await api.registerWorker(name.trim());
      setConfig({
        url: location.origin,
        worker_id: w.id,
        token: w.token,
        supabase_url: w.supabase_url,
        engine_profile: {
          translation_endpoint: "http://127.0.0.1:11434/v1",
          pii_endpoint: "http://127.0.0.1:11434/v1",
          timeout_seconds: 3600,
          engine_options: {
            table_strategy: "auto",
            pdf_page_mode: "document",
            http_keepalive: true,
            nativeized_ir_mode: "reuse",
            babeldoc_dual_output: false,
            figure_engine: "ocr-slots",
            light_pdf_io: true,
            ocr_cache_mode: "reuse",
            translation_qps: 8,
          },
        },
      });
      setName("");
      await onRefresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Registration failed.");
      onError(e);
    } finally {
      setBusy(false);
    }
  }
  function download() {
    if (!config) return;
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(config, null, 2)], { type: "application/json" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "gpu.worker.local.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function confirmRevoke() {
    if (!revoke) return;
    setBusy(true);
    try {
      await api.revokeWorker(revoke.id);
      setRevoke(null);
      setConfig(null);
      await onRefresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Revocation failed.");
      onError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-6">
      {!readOnly && (
        <section className="rounded-2xl border border-border bg-background p-6 sm:p-8">
          <h2 className="mb-2 text-lg font-semibold">
            Connect your processing server
          </h2>
          <p className="mb-6 max-w-2xl text-sm leading-6 text-muted-foreground">
            Use your RunPod instance, university GPU server or another NVIDIA
            server. Register a credential here, then start the TransformX worker
            agent on that server.
          </p>
          <form onSubmit={register} className="flex flex-wrap items-end gap-3">
            <div className="field min-w-0 flex-1 sm:max-w-sm">
              <Label htmlFor="server-name">Server name</Label>
              <Input
                id="server-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Research GPU"
                required
                maxLength={80}
              />
            </div>
            <Button disabled={busy || !name.trim()} type="submit">
              {busy ? <Loader2 className="animate-spin" /> : <Plus />}Register
              server
            </Button>
          </form>
        </section>
      )}
      {config && (
        <section className="rounded-xl border border-primary/25 bg-accent p-6">
          <h2 className="mb-2 text-base font-semibold">
            Save your worker configuration now
          </h2>
          <p className="mb-4 text-sm leading-6 text-muted-foreground">
            The credential is shown only once and grants access to jobs assigned
            to this server. Keep this file private. Registration alone does not
            start processing.
          </p>
          <div className="flex flex-wrap gap-3">
            <Button onClick={download}>
              <Download />
              Download private configuration
            </Button>
            <Button variant="outline" onClick={() => setConfig(null)}>
              I’ve saved it
            </Button>
          </div>
          <p className="mb-0 mt-4 text-xs leading-5 text-muted-foreground">
            Use the existing server setup and worker-agent instructions supplied
            with your TransformX repository. Never upload this configuration
            into a document job.
          </p>
        </section>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <section className="overflow-hidden rounded-2xl border border-border bg-background">
        <div className="border-b border-border p-6">
          <h2 className="mb-1 text-base font-semibold">Your servers</h2>
          <p className="mb-0 text-xs text-muted-foreground">
            Jobs remain assigned to the server you select.
          </p>
        </div>
        {workers.length ? (
          <ul className="m-0 list-none divide-y divide-border p-0">
            {workers.map((w) => (
              <li key={w.id} className="flex flex-wrap items-center gap-4 p-6">
                <div className="flex size-11 items-center justify-center rounded-xl bg-muted text-primary">
                  <Server className="size-5" />
                </div>
                <div className="min-w-0 flex-1">
                  <h3 className="mb-1 break-words text-sm font-medium">
                    {w.name}
                  </h3>
                  <p className="mb-0 text-xs text-muted-foreground">
                    {w.capabilities?.gpu || "GPU not reported"} ·{" "}
                    {w.capabilities?.operations?.join(", ") ||
                      "Capabilities not reported"}
                  </p>
                </div>
                <Badge
                  className={
                    w.ready && w.enabled
                      ? "border-emerald-800 bg-emerald-950 text-emerald-200"
                      : ""
                  }
                >
                  {!w.enabled
                    ? "Revoked"
                    : w.ready
                      ? "Ready"
                      : w.online
                        ? "Connected, not ready"
                        : "Offline"}
                </Badge>
                {w.enabled && !readOnly && (
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`Revoke ${w.name}`}
                    onClick={() => setRevoke(w)}
                  >
                    <Trash2 />
                  </Button>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <div className="p-12 text-center">
            <Server className="mx-auto mb-4 size-8 text-primary" />
            <p className="mb-0 text-sm text-muted-foreground">
              No servers registered yet.
            </p>
          </div>
        )}
      </section>
      <p className="flex gap-2 text-xs leading-5 text-muted-foreground">
        <ShieldCheck className="mt-1 size-4" />
        Server readiness reflects its reported capabilities. Actual GPU
        execution is verified per processing run.
      </p>
      <Dialog
        open={!!revoke}
        onOpenChange={(open) => {
          if (!open && !busy) setRevoke(null);
        }}
      >
        <DialogContent>
          <DialogTitle className="text-xl font-semibold">
            Revoke server access?
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            Queued jobs assigned to {revoke?.name} will no longer run. Running
            work must finish before access can be revoked.
          </DialogDescription>
          <div className="flex justify-end gap-3">
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => setRevoke(null)}
            >
              Keep access
            </Button>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() => void confirmRevoke()}
            >
              Revoke access
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
