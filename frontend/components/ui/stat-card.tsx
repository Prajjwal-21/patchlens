import Link from "next/link";
import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

export function StatCard({
  label,
  value,
  detail,
  icon: Icon,
  href,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  icon?: LucideIcon;
  href?: string;
  tone?: "neutral" | "accent" | "warning" | "danger" | "success";
}) {
  const iconTone = {
    neutral: "bg-surface-muted text-muted",
    accent: "bg-accent-soft text-accent",
    warning: "bg-warning-soft text-warning",
    danger: "bg-danger-soft text-danger",
    success: "bg-success-soft text-success",
  }[tone];

  const body = (
    <div className="flex h-full items-start justify-between gap-3 rounded-xl border border-line bg-surface p-5 shadow-[0_1px_2px_rgba(16,24,40,0.04)] transition-colors">
      <div className="min-w-0">
        <p className="text-xs font-medium text-muted">{label}</p>
        <p className="mt-2 truncate text-2xl font-semibold tracking-tight text-ink">{value}</p>
        {detail ? <div className="mt-1.5 text-xs leading-5 text-muted">{detail}</div> : null}
      </div>
      {Icon ? (
        <span className={`grid size-9 shrink-0 place-items-center rounded-lg ${iconTone}`}>
          <Icon className="size-4" aria-hidden />
        </span>
      ) : null}
    </div>
  );

  if (href) {
    return (
      <Link
        href={href}
        className="block rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 focus-visible:ring-offset-2 focus-visible:ring-offset-canvas hover:[&>div]:border-line-strong"
      >
        {body}
      </Link>
    );
  }
  return body;
}

export function MetricValue({
  value,
  suffix,
  unknown = "UNKNOWN",
}: {
  value: number | string | null | undefined;
  suffix?: string;
  unknown?: string;
}) {
  if (value === null || value === undefined || value === "") {
    return (
      <span className="text-xs font-semibold uppercase tracking-wide text-muted" title="Not measured by this run">
        {unknown}
      </span>
    );
  }
  return (
    <span className="font-mono text-sm tabular-nums text-ink">
      {value}
      {suffix ? <span className="ml-0.5 text-muted">{suffix}</span> : null}
    </span>
  );
}
