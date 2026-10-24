"use client";
import { useEffect, useRef, useState } from "react";
import { ApiClient, ApiError } from "@/lib/api";
export type LandingSession = "checking" | "signed-in" | "guest";

// Static marketing HTML stays public; only the browser checks its private cookie.
export function useLandingSession() {
  const [api] = useState(() => new ApiClient());
  const [session, setSession] = useState<LandingSession>("checking");
  const [signingOut, setSigningOut] = useState(false);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);
  const generation = useRef(0), busy = useRef(false), mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    const refresh = () => {
      if (busy.current) return;
      const current = ++generation.current;
      active.current?.abort();
      const controller = new AbortController();
      active.current = controller;
      void api.me(controller.signal).then(() => {
        if (mounted.current && current === generation.current && !controller.signal.aborted) setSession("signed-in");
      }).catch((failure: unknown) => {
        if (!mounted.current || current !== generation.current || controller.signal.aborted) return;
        setSession(failure instanceof ApiError && failure.status === 401 ? "guest" : "checking");
      });
    };
    const visible = () => { if (!document.hidden) refresh(); };
    refresh();
    window.addEventListener("focus", refresh);
    window.addEventListener("pageshow", refresh);
    document.addEventListener("visibilitychange", visible);
    return () => {
      mounted.current = false;
      generation.current++;
      active.current?.abort();
      window.removeEventListener("focus", refresh);
      window.removeEventListener("pageshow", refresh);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [api]);
  async function signOut() {
    if (busy.current) return;
    busy.current = true;
    generation.current++;
    active.current?.abort();
    setSigningOut(true); setError("");
    try {
      await api.logout();
      if (mounted.current) setSession("guest");
    } catch (failure) {
      if (!mounted.current) return;
      if (failure instanceof ApiError && failure.status === 401) {
        api.csrf = ""; setSession("guest");
      } else setError(failure instanceof Error ? failure.message : "Sign out failed. Please try again.");
    } finally {
      busy.current = false;
      if (mounted.current) setSigningOut(false);
    }
  }
  return { session, signOut, signingOut, error };
}
