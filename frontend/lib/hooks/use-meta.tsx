"use client";

import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";

import { useApiQuery } from "@/lib/hooks/use-api";
import type { Meta } from "@/lib/types";

interface MetaContextValue {
  meta: Meta | null;
  error: Error | null;
  loading: boolean;
  reload: () => void;
}

const MetaContext = createContext<MetaContextValue | null>(null);

export function MetaProvider({ children }: { children: ReactNode }) {
  const query = useApiQuery<Meta>("/api/v1/meta");
  const reload = useCallback(() => query.reload(), [query]);
  const value = useMemo(
    () => ({ meta: query.data, error: query.error, loading: query.loading, reload }),
    [query.data, query.error, query.loading, reload],
  );
  return <MetaContext.Provider value={value}>{children}</MetaContext.Provider>;
}

export function useMeta(): MetaContextValue {
  const context = useContext(MetaContext);
  if (!context) {
    throw new Error("useMeta must be used inside MetaProvider");
  }
  return context;
}
