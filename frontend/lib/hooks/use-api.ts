"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch } from "@/lib/api";

export interface ApiQueryState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  /** True after the first attempt for the current path, successful or not. */
  settled: boolean;
  reload: () => void;
  setData: (value: T | null) => void;
}

/**
 * Fetch a path and keep it fresh.
 *
 * ``refreshIntervalMs`` is used only for live state (queued/running jobs and
 * reviews). Requests are aborted when the path changes or the component unmounts,
 * so a slow response can never overwrite a newer one.
 *
 * Loading is derived from which path has settled rather than stored: setting it
 * inside the fetch effect would cause a cascading render, and would also leave a
 * stale spinner when the path becomes null.
 */
export function useApiQuery<T>(
  path: string | null,
  options: { refreshIntervalMs?: number; enabled?: boolean } = {},
): ApiQueryState<T> {
  const { refreshIntervalMs = 0, enabled = true } = options;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [settledPath, setSettledPath] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const requestId = useRef(0);

  const activePath = path && enabled ? path : null;

  const reload = useCallback(() => {
    setSettledPath(null);
    setReloadToken((value) => value + 1);
  }, []);

  useEffect(() => {
    if (!activePath) return;
    const current = ++requestId.current;
    const controller = new AbortController();
    apiFetch<T>(activePath, { signal: controller.signal })
      .then((result) => {
        if (current !== requestId.current) return;
        setData(result);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        if (current !== requestId.current) return;
        setError(cause instanceof Error ? cause : new Error("Request failed"));
      })
      .finally(() => {
        if (current !== requestId.current) return;
        setSettledPath(activePath);
      });
    return () => controller.abort();
  }, [activePath, reloadToken]);

  useEffect(() => {
    if (!activePath || refreshIntervalMs <= 0) return;
    // Refreshes keep the previous data on screen, so they do not reset the
    // settled marker above.
    const timer = window.setInterval(() => setReloadToken((value) => value + 1), refreshIntervalMs);
    return () => window.clearInterval(timer);
  }, [activePath, refreshIntervalMs]);

  return {
    data,
    error,
    loading: activePath !== null && settledPath !== activePath,
    settled: activePath !== null && settledPath === activePath,
    reload,
    setData,
  };
}
