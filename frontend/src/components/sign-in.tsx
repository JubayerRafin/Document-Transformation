"use client";
import { useState, type FormEvent } from "react";
import { Loader2, LockKeyhole } from "lucide-react";
import { ApiClient, type Account } from "@/lib/api";
import { Button } from "./ui/button";
import { Input, Label } from "./ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "./ui/dialog";
export function SignIn({
  open,
  onOpenChange,
  api,
  onAccount,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  api: ApiClient;
  onAccount: (u: Account) => void;
}) {
  const [email, setEmail] = useState(""),
    [password, setPassword] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const u = await api.login(email.trim(), password);
      setPassword("");
      onAccount(u);
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sign-in failed. Try again.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!busy) {
          onOpenChange(v);
          setPassword("");
          setError("");
        }
      }}
    >
      <DialogContent>
        <div className="flex size-11 items-center justify-center rounded-xl bg-accent text-primary">
          <LockKeyhole className="size-5" />
        </div>
        <div>
          <DialogTitle className="mb-2 text-2xl font-semibold tracking-tight">
            Welcome to your workspace
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            Sign in with your invited account to transform and review your
            documents.
          </DialogDescription>
        </div>
        <form onSubmit={submit} className="grid gap-5">
          <div className="field">
            <Label htmlFor="sign-in-email">Email address</Label>
            <Input
              id="sign-in-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="username"
              placeholder="you@company.com"
              maxLength={254}
            />
          </div>
          <div className="field">
            <Label htmlFor="sign-in-password">Password</Label>
            <Input
              id="sign-in-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="current-password"
              maxLength={200}
            />
          </div>
          {error && (
            <p role="alert" className="mb-0 text-sm text-destructive">
              {error}
            </p>
          )}
          <Button size="lg" disabled={busy} type="submit">
            {busy && <Loader2 className="animate-spin" />}
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </form>
        <p className="mb-0 text-center text-xs text-muted-foreground">
          Access is by invitation. Contact your workspace administrator.
        </p>
      </DialogContent>
    </Dialog>
  );
}
