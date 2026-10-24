"use client";
import { useCallback, useEffect, useState } from "react";
import {
  ApiClient,
  ApiError,
  type Account,
  type Job,
  type Worker,
} from "@/lib/api";
export function useWorkspace() {
  const [api] = useState(() => new ApiClient()),
    [account, setAccount] = useState<Account | null>(null),
    [loading, setLoading] = useState(true),
    [signIn, setSignIn] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]),
    [workers, setWorkers] = useState<Worker[]>([]),
    [error, setError] = useState("");
  const expire = useCallback(() => {
    api.csrf = "";
    setAccount(null);
    setJobs([]);
    setWorkers([]);
    setSignIn(true);
  }, [api]);
  const handleError = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) expire();
      setError(e instanceof Error ? e.message : "The service is unavailable.");
    },
    [expire],
  );
  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      const session = api.csrf;
      try {
        const [j, w] = await Promise.all([
          api.jobs(signal),
          api.workers(signal),
        ]);
        if (signal?.aborted || !session || api.csrf !== session) return;
        setJobs(j);
        setWorkers(w);
        setError("");
      } catch (e) {
        if (signal?.aborted || api.csrf !== session) return;
        handleError(e);
      }
    },
    [api, handleError],
  );
  useEffect(() => {
    const ctrl = new AbortController();
    api
      .me(ctrl.signal)
      .then((u) => {
        if (!ctrl.signal.aborted) setAccount(u);
      })
      .catch((e) => {
        if (ctrl.signal.aborted) return;
        if (e instanceof ApiError && e.status === 401) setSignIn(true);
        else handleError(e);
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
  }, [api, handleError]);
  useEffect(() => {
    if (!account) return;
    const ctrl = new AbortController();
    let busy = false;
    async function tick() {
      if (busy || document.hidden) return;
      busy = true;
      try {
        await refresh(ctrl.signal);
      } finally {
        busy = false;
      }
    }
    void tick();
    const timer = setInterval(tick, 5000);
    const wake = () => {
      if (!document.hidden) void tick();
    };
    document.addEventListener("visibilitychange", wake);
    return () => {
      ctrl.abort();
      clearInterval(timer);
      document.removeEventListener("visibilitychange", wake);
    };
  }, [account, refresh]);
  const logout = async () => {
    try {
      await api.logout();
      expire();
      setSignIn(false);
    } catch (e) {
      handleError(e);
    }
  };
  return {
    api,
    account,
    setAccount,
    loading,
    signIn,
    setSignIn,
    jobs,
    workers,
    error,
    setError,
    refresh,
    handleError,
    logout,
  };
}
