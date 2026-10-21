import Link from "next/link";
import { ArrowUpRight, Code2, MessageSquareText } from "lucide-react";

import { FeedbackForm, FeedbackSummary } from "@/components/finding-feedback";
import { Badge, SeverityBadge, VerificationBadge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { formatRelative, humanizeStatus } from "@/lib/format";
import type { Finding, Evidence } from "@/lib/types";

function evidenceExcerpt(evidence: Evidence): string | null {
  const detail = evidence.detail ?? {};
  for (const key of ["excerpt", "text", "snippet"]) {
    const value = detail[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
}

function evidenceLocation(evidence: Evidence): string | null {
  const detail = evidence.detail ?? {};
  const path = typeof detail.path === "string" ? detail.path : null;
  const line = typeof detail.line === "number" ? detail.line : null;
  if (path && line) return `${path}:${line}`;
  if (path) return path;
  const name = typeof detail.qualified_name === "string" ? detail.qualified_name : null;
  return name;
}

export function EvidencePreview({ evidence }: { evidence: Evidence[] }) {
  const first = evidence.find((item) => evidenceExcerpt(item));
  if (!first) return null;
  const excerpt = evidenceExcerpt(first);
  if (!excerpt) return null;
  return (
    <div className="mt-3 overflow-hidden rounded-lg border border-line bg-surface-muted/60">
      <div className="flex items-center gap-1.5 border-b border-line px-3 py-1.5 text-[11px] text-muted">
        <Code2 className="size-3.5" aria-hidden />
        <span className="font-mono">{evidenceLocation(first) ?? first.kind}</span>
      </div>
      <pre className="overflow-x-auto px-3 py-2 font-mono text-xs leading-5 text-ink/80">{excerpt}</pre>
    </div>
  );
}

export function FindingCard({
  finding,
  onFeedback,
  showReviewLink = true,
  compact = false,
}: {
  finding: Finding;
  onFeedback?: () => void;
  showReviewLink?: boolean;
  compact?: boolean;
}) {
  return (
    <Card as="article" className="overflow-hidden">
      <div className="flex flex-wrap items-start justify-between gap-3 px-5 pt-4">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <SeverityBadge severity={finding.severity} />
          <VerificationBadge status={finding.verification_status} />
          <Badge tone="neutral">{humanizeStatus(finding.category)}</Badge>
          {finding.feedback.length > 0 ? (
            <Badge tone="accent" title="Human feedback exists for this finding">
              <MessageSquareText className="size-3" aria-hidden />
              Reviewed
            </Badge>
          ) : null}
        </div>
        <Link
          href={`/findings/${finding.id}`}
          className="inline-flex items-center gap-1 rounded text-xs font-medium text-accent-strong hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        >
          Open finding
          <ArrowUpRight className="size-3.5" aria-hidden />
        </Link>
      </div>
      <div className="px-5 pb-4 pt-2">
        <h3 className="text-sm font-semibold leading-6 text-ink">{finding.title}</h3>
        <p className="mt-1 line-clamp-3 text-sm leading-6 text-muted">{finding.explanation}</p>
        {!compact ? <EvidencePreview evidence={finding.evidence} /> : null}
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted">
          <span className="font-mono text-[11px] text-ink/70">
            {finding.path}:{finding.start_line}
            {finding.end_line !== finding.start_line ? `–${finding.end_line}` : ""}
          </span>
          <span>{finding.line_verified ? "Line verified against diff" : "Line not verified"}</span>
          <span title={finding.confidence_basis}>
            {finding.confidence === null
              ? "Confidence UNKNOWN"
              : `Detector score ${finding.confidence.toFixed(2)} · ${humanizeStatus(finding.confidence_basis)}`}
          </span>
          <span>{formatRelative(finding.created_at)}</span>
          {showReviewLink ? (
            <Link href={`/reviews/${finding.review_id}`} className="hover:text-ink hover:underline">
              Review {finding.review_id.slice(0, 10)}
            </Link>
          ) : null}
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-line/70 pt-2.5">
          <FeedbackSummary feedback={finding.feedback} />
          <FeedbackForm findingId={finding.id} onSubmitted={onFeedback} compact />
        </div>
      </div>
    </Card>
  );
}
