"use client";

import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";

export default function GlobalError({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <div className="mx-auto max-w-xl rounded-xl border border-danger/20 bg-surface p-8 text-center">
      <div className="mx-auto grid size-11 place-items-center rounded-full bg-danger-soft">
        <AlertTriangle className="size-5 text-danger" aria-hidden />
      </div>
      <h1 className="mt-4 text-lg font-semibold text-ink">This view failed to render</h1>
      <p className="mt-1 text-sm leading-6 text-muted">
        {error.message || "An unexpected client error occurred."}
      </p>
      <Button className="mt-5" onClick={reset}>
        Try again
      </Button>
    </div>
  );
}
