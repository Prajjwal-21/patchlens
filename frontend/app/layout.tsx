import type { Metadata, Viewport } from "next";

import { AppShell } from "@/components/app-shell";
import { MetaProvider } from "@/lib/hooks/use-meta";

import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "PatchLens · Review intelligence",
    template: "%s · PatchLens",
  },
  description:
    "Repository-aware code review with evidence-grounded findings, hybrid retrieval, test intelligence and reproducible evaluation.",
  applicationName: "PatchLens",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  colorScheme: "light",
  themeColor: "#f5f6f8",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <MetaProvider>
          <AppShell>{children}</AppShell>
        </MetaProvider>
      </body>
    </html>
  );
}
