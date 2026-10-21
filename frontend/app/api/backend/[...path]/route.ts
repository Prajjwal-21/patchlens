import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

/**
 * Same-origin proxy to the PatchLens FastAPI backend.
 *
 * Why this exists:
 * - `API_AUTH_TOKEN` stays on the Next.js server; it is never in browser JS.
 * - The dashboard does not need CORS on the backend.
 * - Only known backend prefixes are proxied, and path traversal is rejected.
 *
 * This is not a replacement for deployment-level authentication. In production the
 * dashboard should sit behind your normal SSO/reverse proxy and the backend must
 * have API_AUTH_TOKEN set.
 */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const ALLOWED_METHODS = new Set(["GET", "POST", "PUT", "PATCH", "DELETE"]);

function upstreamBase(): string {
  const raw = process.env.PATCHLENS_API_URL?.trim() || "http://127.0.0.1:8000";
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error("PATCHLENS_API_URL is not a valid URL");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("PATCHLENS_API_URL must use http or https");
  }
  return url.toString().replace(/\/+$/, "");
}

export function pathAllowed(segments: string[]): boolean {
  if (segments.length === 0) return false;
  for (const segment of segments) {
    if (!segment || segment === "." || segment === ".." || segment.includes("\\")) {
      return false;
    }
    // Encoded traversal or null bytes should never be forwarded.
    if (/%2e|%2f|%5c|%00/i.test(segment)) return false;
  }
  const path = `/${segments.join("/")}`;
  if (path === "/healthz" || path === "/readyz") return true;
  return path === "/api/v1" || path.startsWith("/api/v1/");
}

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  if (!ALLOWED_METHODS.has(request.method)) {
    return NextResponse.json(
      { error: { code: "method_not_allowed", message: "Method is not proxied", details: {} } },
      { status: 405 },
    );
  }
  const { path } = await context.params;
  if (!pathAllowed(path)) {
    return NextResponse.json(
      { error: { code: "not_found", message: "Proxy path is not allowed", details: {} } },
      { status: 404 },
    );
  }

  const target = new URL(`${upstreamBase()}/${path.map(encodeURIComponent).join("/")}`);
  target.search = request.nextUrl.search;

  const headers = new Headers();
  const accept = request.headers.get("accept");
  if (accept) headers.set("Accept", accept);
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);
  const requestId = request.headers.get("x-request-id");
  if (requestId) headers.set("X-Request-Id", requestId);

  const token = process.env.PATCHLENS_API_TOKEN?.trim();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const body =
    request.method === "GET" || request.method === "HEAD"
      ? undefined
      : Buffer.from(await request.arrayBuffer());

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      redirect: "manual",
    });
  } catch {
    return NextResponse.json(
      {
        error: {
          code: "backend_unreachable",
          message: "The PatchLens backend is not reachable from the dashboard server.",
          details: {},
        },
      },
      { status: 502 },
    );
  }

  const responseHeaders = new Headers();
  for (const name of ["content-type", "x-request-id", "x-response-time-ms"]) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  responseHeaders.set("Cache-Control", "no-store");

  return new NextResponse(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}

export async function GET(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, context);
}

export async function POST(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, context);
}

export async function PUT(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, context);
}

export async function PATCH(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, context);
}

export async function DELETE(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, context);
}
