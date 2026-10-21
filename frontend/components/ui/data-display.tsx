import type { ReactNode } from "react";

export function ProgressBar({
  value,
  label,
  tone = "accent",
}: {
  value: number;
  label?: string;
  tone?: "accent" | "success" | "danger";
}) {
  const bounded = Math.min(100, Math.max(0, value));
  const bar = {
    accent: "bg-accent",
    success: "bg-success",
    danger: "bg-danger",
  }[tone];
  return (
    <div>
      {label ? (
        <div className="mb-1.5 flex items-center justify-between text-xs text-muted">
          <span>{label}</span>
          <span className="font-mono tabular-nums">{bounded}%</span>
        </div>
      ) : null}
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-line"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={bounded}
        aria-label={label ? undefined : "Progress"}
      >
        <div className={`h-full rounded-full ${bar} transition-[width] duration-500`} style={{ width: `${bounded}%` }} />
      </div>
    </div>
  );
}

export function CodeBlock({
  children,
  label,
  maxHeight = "24rem",
  className = "",
}: {
  children: ReactNode;
  label?: string;
  maxHeight?: string;
  className?: string;
}) {
  return (
    <div className={`overflow-hidden rounded-lg border border-line bg-[#0f131a] ${className}`}>
      {label ? (
        <div className="flex items-center justify-between border-b border-white/10 px-3 py-2">
          <span className="font-mono text-[11px] text-white/60">{label}</span>
        </div>
      ) : null}
      <pre
        className="overflow-auto p-4 font-mono text-xs leading-6 text-slate-200"
        style={{ maxHeight }}
      >
        <code>{children}</code>
      </pre>
    </div>
  );
}

export function KeyValue({
  label,
  children,
  mono = false,
}: {
  label: string;
  children: ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] gap-3 py-2 text-sm">
      <dt className="text-muted">{label}</dt>
      <dd className={`min-w-0 break-words text-ink ${mono ? "font-mono text-xs" : ""}`}>{children}</dd>
    </div>
  );
}
