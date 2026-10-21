import type { ReactNode } from "react";

import { humanizeStatus, severityTone, statusTone, verificationTone, type Tone } from "@/lib/format";

const toneClasses: Record<Tone, string> = {
  neutral: "border-line bg-surface-muted text-muted",
  accent: "border-accent/20 bg-accent-soft text-accent-strong",
  success: "border-success/20 bg-success-soft text-success-strong",
  warning: "border-warning/25 bg-warning-soft text-warning-strong",
  danger: "border-danger/20 bg-danger-soft text-danger-strong",
  info: "border-info/20 bg-info-soft text-info-strong",
};

const dotClasses: Record<Tone, string> = {
  neutral: "bg-muted",
  accent: "bg-accent",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  info: "bg-info",
};

export function Badge({
  children,
  tone = "neutral",
  className = "",
  dot = false,
  title,
}: {
  children: ReactNode;
  tone?: Tone;
  className?: string;
  dot?: boolean;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-semibold leading-5 ${toneClasses[tone]} ${className}`}
    >
      {dot ? <span className={`size-1.5 rounded-full ${dotClasses[tone]}`} aria-hidden /> : null}
      {children}
    </span>
  );
}

export function StatusBadge({ status, dot = true }: { status: string; dot?: boolean }) {
  return (
    <Badge tone={statusTone(status)} dot={dot}>
      {humanizeStatus(status)}
    </Badge>
  );
}

export function SeverityBadge({ severity }: { severity: string }) {
  return <Badge tone={severityTone(severity)}>{humanizeStatus(severity)}</Badge>;
}

export function VerificationBadge({ status }: { status: string }) {
  return <Badge tone={verificationTone(status)}>{humanizeStatus(status)}</Badge>;
}

export function ProviderBadge({ provider, isMock }: { provider: string; isMock?: boolean }) {
  if (isMock) {
    return (
      <Badge tone="warning" title="Deterministic development provider; no model was called">
        Mock · {provider}
      </Badge>
    );
  }
  return <Badge tone="accent">{provider}</Badge>;
}
