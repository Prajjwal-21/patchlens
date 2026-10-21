import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // The FastAPI backend is reached through the same-origin proxy in
  // app/api/backend/[...path]/route.ts; no browser CORS or token exposure.
};

export default nextConfig;
