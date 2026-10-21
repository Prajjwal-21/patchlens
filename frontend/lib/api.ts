/**
 * Browser API client.
 *
 * Every request goes to the same-origin proxy, never directly to FastAPI. The
 * proxy (app/api/backend/[...path]/route.ts) injects the bearer token on the
 * server, which keeps credentials out of JavaScript bundles and avoids CORS.
 */

import type { ApiErrorBody } from "@/lib/types";

export const API_PROXY_PREFIX = "/api/backend";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(
    message: string,
    options: { status: number; code: string; details?: Record<string, unknown> },
  ) {
    super(message);
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code;
    this.details = options.details ?? {};
  }
}

export type QueryValue = string | number | boolean | null | undefined;

export function buildQuery(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const query = search.toString();
  return query ? `?${query}` : "";
}

export function formatApiError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "Unexpected error";
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (!path.startsWith("/")) {
    throw new Error("API path must be absolute");
  }
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(`${API_PROXY_PREFIX}${path}`, {
      ...init,
      headers,
      cache: "no-store",
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") {
      throw cause;
    }
    throw new ApiError("The PatchLens API could not be reached. Is the backend running?", {
      status: 0,
      code: "network_error",
    });
  }

  if (!response.ok) {
    let body: ApiErrorBody | null = null;
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      body = null;
    }
    throw new ApiError(
      body?.error?.message || `Request failed with HTTP ${response.status}`,
      {
        status: response.status,
        code: body?.error?.code || "http_error",
        details: body?.error?.details,
      },
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }
  const text = await response.text();
  if (!text) {
    return undefined as T;
  }
  return JSON.parse(text) as T;
}

export function postJson<T>(path: string, body: unknown = {}): Promise<T> {
  return apiFetch<T>(path, { method: "POST", body: JSON.stringify(body) });
}

export function deleteResource<T>(path: string): Promise<T> {
  return apiFetch<T>(path, { method: "DELETE" });
}
