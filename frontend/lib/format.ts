/** Presentation helpers. Missing metrics are rendered as UNKNOWN, never zero. */

export function formatMetric(value: unknown, digits = 3): string {
  if (value === null || value === undefined || value === "") return "UNKNOWN";
  if (typeof value !== "number" || Number.isNaN(value)) return "UNKNOWN";
  if (!Number.isFinite(value)) return "UNKNOWN";
  return value.toFixed(digits);
}

export function formatPercent(value: unknown, digits = 1): string {
  if (value === null || value === undefined) return "UNKNOWN";
  if (typeof value !== "number" || !Number.isFinite(value)) return "UNKNOWN";
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-US").format(value);
}

export function formatDuration(milliseconds: number | null | undefined): string {
  if (milliseconds === null || milliseconds === undefined || !Number.isFinite(milliseconds)) {
    return "—";
  }
  if (milliseconds < 1) return "<1 ms";
  if (milliseconds < 1_000) return `${Math.round(milliseconds)} ms`;
  if (milliseconds < 60_000) return `${(milliseconds / 1_000).toFixed(1)} s`;
  const minutes = Math.floor(milliseconds / 60_000);
  const seconds = Math.round((milliseconds % 60_000) / 1_000);
  return `${minutes}m ${seconds}s`;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("en-US", { dateStyle: "medium" }).format(date);
}

export function formatRelative(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const seconds = Math.round((date.getTime() - Date.now()) / 1_000);
  const formatter = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  const thresholds: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["year", 31_536_000],
    ["month", 2_592_000],
    ["week", 604_800],
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
    ["second", 1],
  ];
  for (const [unit, size] of thresholds) {
    if (Math.abs(seconds) >= size || unit === "second") {
      return formatter.format(Math.round(seconds / size), unit);
    }
  }
  return "just now";
}

export function shortSha(value: string | null | undefined, length = 9): string {
  if (!value) return "—";
  return value.length > length ? value.slice(0, length) : value;
}

export function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

export function pluralize(count: number, singular: string, plural?: string): string {
  return `${formatCount(count)} ${count === 1 ? singular : (plural ?? `${singular}s`)}`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1_024) return `${bytes} B`;
  if (bytes < 1_048_576) return `${(bytes / 1_024).toFixed(1)} KiB`;
  return `${(bytes / 1_048_576).toFixed(1)} MiB`;
}

export function safeHttpUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}

export type Tone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

export function severityTone(severity: string): Tone {
  switch (severity) {
    case "critical":
    case "high":
      return "danger";
    case "medium":
      return "warning";
    case "low":
      return "info";
    default:
      return "neutral";
  }
}

export function verificationTone(status: string): Tone {
  switch (status) {
    case "verified":
      return "success";
    case "needs_manual_verification":
      return "warning";
    case "ai_suggested":
      return "info";
    case "rejected":
      return "neutral";
    default:
      return "neutral";
  }
}

export function statusTone(status: string): Tone {
  switch (status) {
    case "succeeded":
    case "indexed":
    case "passed":
    case "correct":
      return "success";
    case "failed":
    case "dead":
    case "incorrect":
      return "danger";
    case "running":
    case "indexing":
    case "processing":
      return "accent";
    case "queued":
    case "pending":
    case "generated":
      return "info";
    case "canceled":
    case "disabled":
    case "execution_disabled":
      return "neutral";
    case "needs_investigation":
    case "needs_manual_verification":
    case "stale":
      return "warning";
    case "duplicate":
    case "irrelevant":
      return "neutral";
    default:
      return "neutral";
  }
}

export function humanizeStatus(value: string): string {
  switch (value) {
    case "not_run":
      return "NOT RUN";
    case "ai_suggested":
      return "AI suggested";
    case "needs_manual_verification":
      return "Needs verification";
    case "execution_disabled":
      return "Not executed";
    case "needs_investigation":
      return "Needs investigation";
    default:
      return titleCase(value);
  }
}
