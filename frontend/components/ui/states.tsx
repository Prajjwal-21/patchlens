import type { ReactNode } from "react";
import {
  AlertTriangle,
  Inbox,
  Loader2,
  RefreshCcw,
  type LucideIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { formatApiError } from "@/lib/api";

export function LoadingState({
  label = "Loading",
  rows = 3,
  className = "",
}: {
  label?: string;
  rows?: number;
  className?: string;
}) {
  return (
    <div className={`space-y-3 ${className}`} role="status" aria-live="polite">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="animate-pulse rounded-xl border border-line bg-surface p-5">
          <div className="h-3 w-1/4 rounded bg-line" />
          <div className="mt-3 h-4 w-3/4 rounded bg-line/80" />
          <div className="mt-2 h-3 w-1/2 rounded bg-line/60" />
        </div>
      ))}
    </div>
  );
}

export function InlineLoading({ label = "Loading" }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-muted" role="status">
      <Loader2 className="size-4 animate-spin" aria-hidden />
      {label}
    </span>
  );
}

export function ErrorState({
  error,
  onRetry,
  title = "Could not load this data",
  className = "",
}: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
  className?: string;
}) {
  return (
    <div
      className={`rounded-xl border border-danger/20 bg-danger-soft/60 p-5 ${className}`}
      role="alert"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 size-5 shrink-0 text-danger" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-danger-strong">{title}</p>
          <p className="mt-1 break-words text-sm text-danger-strong/80">{formatApiError(error)}</p>
          {onRetry ? (
            <Button variant="secondary" size="sm" className="mt-3" onClick={onRetry}>
              <RefreshCcw className="size-3.5" aria-hidden />
              Retry
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon: Icon = Inbox,
  className = "",
}: {
  title: string;
  description: ReactNode;
  action?: ReactNode;
  icon?: LucideIcon;
  className?: string;
}) {
  return (
    <div className={`flex flex-col items-center justify-center px-6 py-12 text-center ${className}`}>
      <div className="grid size-11 place-items-center rounded-full border border-line bg-surface-muted">
        <Icon className="size-5 text-muted" aria-hidden />
      </div>
      <h3 className="mt-3 text-sm font-semibold text-ink">{title}</h3>
      <div className="mt-1 max-w-md text-sm leading-6 text-muted">{description}</div>
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export type NoticeTone = "info" | "warning" | "success" | "danger" | "neutral";

const noticeTones: Record<NoticeTone, string> = {
  info: "border-info/20 bg-info-soft text-info-strong",
  warning: "border-warning/25 bg-warning-soft text-warning-strong",
  success: "border-success/20 bg-success-soft text-success-strong",
  danger: "border-danger/20 bg-danger-soft text-danger-strong",
  neutral: "border-line bg-surface-muted text-muted",
};

export function InlineNotice({
  tone = "info",
  title,
  children,
  className = "",
}: {
  tone?: NoticeTone;
  title?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`rounded-xl border px-4 py-3 text-sm ${noticeTones[tone]} ${className}`}>
      {title ? <p className="font-semibold">{title}</p> : null}
      {children ? <div className={`text-sm leading-6 ${title ? "mt-1 opacity-90" : ""}`}>{children}</div> : null}
    </div>
  );
}
