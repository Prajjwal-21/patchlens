"use client";

import { useMemo } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ArrowLeft,
  ArrowUpRight,
  Braces,
  CheckCircle2,
  Code2,
  History,
  MapPin,
  MessageSquareText,
  ShieldQuestion,
} from "lucide-react";

import { FeedbackForm, FeedbackSummary } from "@/components/finding-feedback";
import { Badge, ProviderBadge, SeverityBadge, VerificationBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { KeyValue } from "@/components/ui/data-display";
import { PageBreadcrumb, PageHeader } from "@/components/ui/page-header";
import { InlineNotice, ErrorState, LoadingState } from "@/components/ui/states";
import { useApiQuery } from "@/lib/hooks/use-api";
import { asNumber, asString } from "@/lib/json";
import {
  formatDateTime,
  formatMetric,
  formatRelative,
  humanizeStatus,
  safeHttpUrl,
  titleCase,
} from "@/lib/format";
import type { Evidence, Finding, FindingTrace } from "@/lib/types";

function evidenceName(item: Evidence): string {
  const detail = item.detail ?? {};
  const path = asString(detail.path);
  const line = asNumber(detail.line);
  const name = asString(detail.qualified_name);
  const test = asString(detail.test_name);
  if (path && line) return `${path}:${line}`;
  if (path) return path;
  if (name) return name;
  if (test) return test;
  return item.kind;
}

function evidenceExcerpt(item: Evidence): string | null {
  const detail = item.detail ?? {};
  for (const key of ["excerpt", "text", "snippet", "source"]) {
    const value = asString(detail[key]);
    if (value) return value;
  }
  return null;
}

function EvidenceItem({ item }: { item: Evidence }) {
  const excerpt = evidenceExcerpt(item);
  return (
    <li className="rounded-lg border border-line bg-surface-muted/40 p-3">
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted">
        <Badge tone="neutral">{humanizeStatus(item.kind)}</Badge>
        <span className="font-mono text-ink/70">{evidenceName(item)}</span>
      </div>
      {excerpt ? (
        <pre className="mt-2 overflow-x-auto rounded-md bg-[#0f131a] px-3 py-2 font-mono text-[11px] leading-5 text-slate-200">
          {excerpt}
        </pre>
      ) : null}
      <details className="mt-2">
        <summary className="cursor-pointer text-[11px] text-muted hover:text-ink">Recorded fields</summary>
        <pre className="mt-1 overflow-x-auto rounded-md border border-line bg-surface px-3 py-2 font-mono text-[10px] leading-5 text-muted">
          {JSON.stringify(item.detail, null, 2)}
        </pre>
      </details>
    </li>
  );
}

export default function FindingDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const finding = useApiQuery<Finding>(id ? `/api/v1/findings/${encodeURIComponent(id)}` : null);
  const trace = useApiQuery<FindingTrace>(id ? `/api/v1/findings/${encodeURIComponent(id)}/trace` : null);
  const publishedUrl = safeHttpUrl(finding.data?.published_comment_url);

  const evidence = useMemo(() => finding.data?.evidence ?? [], [finding.data]);
  const retrievalEvidence = useMemo(() => finding.data?.retrieval_evidence ?? [], [finding.data]);
  const rule = trace.data?.rule;

  if (finding.loading && !finding.data) {
    return (
      <>
        <PageHeader title="Finding" description="Loading finding and its evidence…" />
        <LoadingState rows={3} />
      </>
    );
  }
  if (finding.error && !finding.data) {
    return (
      <>
        <PageBreadcrumb items={[{ label: "Findings", href: "/findings" }, { label: "Unknown" }]} />
        <ErrorState error={finding.error} onRetry={finding.reload} title="Finding could not be loaded" />
      </>
    );
  }
  if (!finding.data) return null;

  const item = finding.data;
  const notExecuted = trace.data?.not_executed ?? [];

  return (
    <div>
      <PageBreadcrumb
        items={[
          { label: "Findings", href: "/findings" },
          { label: `${item.path}:${item.start_line}` },
        ]}
      />
      <PageHeader
        eyebrow="Finding"
        title={item.title}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={item.severity} />
            <VerificationBadge status={item.verification_status} />
            <Badge tone="neutral">{titleCase(item.category)}</Badge>
            <span className="font-mono text-xs">
              {item.path}:{item.start_line}
              {item.end_line !== item.start_line ? `–${item.end_line}` : ""}
            </span>
          </span>
        }
        actions={
          <>
            <LinkButton href={`/reviews/${item.review_id}`} variant="secondary">
              View review
            </LinkButton>
            <LinkButton href="/findings" variant="ghost">
              <ArrowLeft className="size-3.5" aria-hidden /> Findings
            </LinkButton>
          </>
        }
      />

      {item.verification_status === "rejected" ? (
        <InlineNotice tone="warning" title="This finding was rejected before publication" className="mb-5">
          <p>{item.rejection_reason ?? "No rejection reason was recorded."}</p>
          <p className="mt-1">It is retained for auditability and does not count toward review metrics.</p>
        </InlineNotice>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[1.6fr_1fr]">
        <div className="space-y-5">
          <Card>
            <CardHeader
              title="Explanation"
              description="The claim, what would fix it, and a test that would exercise it."
            />
            <CardBody className="space-y-4">
              <p className="whitespace-pre-wrap text-sm leading-6 text-ink/90">{item.explanation}</p>
              {item.suggestion ? (
                <div>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Suggested change</h3>
                  <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-ink/90">{item.suggestion}</p>
                </div>
              ) : (
                <p className="text-xs text-muted">No automated remedy was recorded.</p>
              )}
              {item.suggested_test ? (
                <div>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Suggested test</h3>
                  <pre className="mt-1 overflow-x-auto rounded-lg border border-line bg-surface-muted px-3 py-2 font-mono text-xs leading-5 text-ink/80">
                    {item.suggested_test}
                  </pre>
                </div>
              ) : null}
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Detection evidence"
              description="Recorded at detection time. Excerpts are evidence, not a reproduced failure."
              action={<Badge tone="neutral">{evidence.length} item{evidence.length === 1 ? "" : "s"}</Badge>}
            />
            <CardBody>
              {evidence.length === 0 ? (
                <p className="text-sm text-muted">No evidence entries were recorded for this finding.</p>
              ) : (
                <ul className="space-y-2.5">
                  {evidence.map((entry, index) => (
                    <EvidenceItem key={`${entry.kind}-${index}`} item={entry} />
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Retrieval evidence"
              description="Context that influenced the model or check. For model findings this is not a citation of truth."
              action={<Badge tone="neutral">{retrievalEvidence.length} item{retrievalEvidence.length === 1 ? "" : "s"}</Badge>}
            />
            <CardBody>
              {retrievalEvidence.length === 0 ? (
                <p className="text-sm text-muted">No retrieval evidence was attached.</p>
              ) : (
                <ul className="space-y-2.5">
                  {retrievalEvidence.map((entry, index) => (
                    <EvidenceItem key={`${entry.kind}-${index}`} item={entry} />
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Trace"
              description="How this row was produced, including the checks that did not run."
              action={
                trace.loading ? (
                  <span className="text-xs text-muted">Loading…</span>
                ) : trace.error ? (
                  <Badge tone="warning">Trace unavailable</Badge>
                ) : null
              }
            />
            <CardBody>
              {trace.error && !trace.data ? (
                <p className="text-sm text-danger-strong">{trace.error.message}</p>
              ) : trace.data ? (
                <div className="space-y-4">
                  <dl className="divide-y divide-line/70">
                    <KeyValue label="Detector">
                      <span className="font-mono text-xs">{trace.data.detector}</span>
                    </KeyValue>
                    <KeyValue label="Rule">{trace.data.rule_id}</KeyValue>
                    <KeyValue label="Rule origin">
                      {trace.data.rule_is_deterministic ? "Deterministic check catalogue" : "Model candidate (rule id supplied by validation)"}
                    </KeyValue>
                    <KeyValue label="Confidence">
                      {trace.data.confidence === null
                        ? "UNKNOWN"
                        : `${formatMetric(trace.data.confidence, 2)} · ${humanizeStatus(trace.data.confidence_basis)}`}
                    </KeyValue>
                    <KeyValue label="Provider">
                      {trace.data.provider ? (
                        <ProviderBadge provider={trace.data.provider} isMock={trace.data.provider === "mock"} />
                      ) : (
                        "—"
                      )}
                    </KeyValue>
                    <KeyValue label="Line verified">
                      {trace.data.line_verified ? "Yes, the line exists in the diff" : "No"}
                    </KeyValue>
                    <KeyValue label="Dedupe of">
                      {trace.data.dedupe_of_finding_id ? (
                        <Link
                          href={`/findings/${trace.data.dedupe_of_finding_id}`}
                          className="font-mono text-xs text-accent-strong hover:underline"
                        >
                          {trace.data.dedupe_of_finding_id}
                        </Link>
                      ) : (
                        "—"
                      )}
                    </KeyValue>
                  </dl>
                  {rule ? (
                    <div className="rounded-lg border border-line bg-surface-muted/50 p-3">
                      <p className="text-xs font-semibold text-ink">{rule.rule_id}</p>
                      <p className="mt-1 text-xs leading-5 text-muted">{rule.summary}</p>
                      <p className="mt-1 text-xs leading-5 text-muted">
                        <span className="font-medium text-ink">Remediation:</span> {rule.remediation}
                      </p>
                    </div>
                  ) : null}
                  {notExecuted.length > 0 ? (
                    <InlineNotice tone="warning" title="What this does not prove">
                      <ul className="list-disc space-y-1 pl-4">
                        {notExecuted.map((note) => (
                          <li key={note}>{note}</li>
                        ))}
                      </ul>
                    </InlineNotice>
                  ) : null}
                  <p className="text-[11px] leading-5 text-muted">{trace.data.note}</p>
                </div>
              ) : null}
            </CardBody>
          </Card>
        </div>

        <div className="space-y-5">
          <Card>
            <CardHeader title="Identity" />
            <CardBody>
              <dl className="divide-y divide-line/70">
                <KeyValue label="Finding id" mono>{item.id}</KeyValue>
                <KeyValue label="Review">
                  <Link href={`/reviews/${item.review_id}`} className="font-mono text-xs text-accent-strong hover:underline">
                    {item.review_id}
                  </Link>
                </KeyValue>
                <KeyValue label="Repository">
                  <Link href={`/repositories/${item.repository_id}`} className="text-xs text-accent-strong hover:underline">
                    {item.repository_id}
                  </Link>
                </KeyValue>
                <KeyValue label="Location" mono>
                  {item.path}:{item.start_line}
                  {item.end_line !== item.start_line ? `–${item.end_line}` : ""}
                </KeyValue>
                <KeyValue label="Line anchored">{item.line_verified ? "Yes" : "No"}</KeyValue>
                <KeyValue label="Category">{titleCase(item.category)}</KeyValue>
                <KeyValue label="Created">{formatDateTime(item.created_at)}</KeyValue>
                <KeyValue label="Model">
                  {item.model ? <span className="font-mono text-xs">{item.provider} / {item.model}</span> : "—"}
                </KeyValue>
                <KeyValue label="Publish state">
                  <Badge tone={item.publish_state === "published" ? "success" : "neutral"}>
                    {humanizeStatus(item.publish_state)}
                  </Badge>
                </KeyValue>
                {publishedUrl ? (
                  <KeyValue label="Comment">
                    <a
                      href={publishedUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 text-accent-strong hover:underline"
                    >
                      Open on GitHub <ArrowUpRight className="size-3" aria-hidden />
                    </a>
                  </KeyValue>
                ) : null}
              </dl>
              {item.publish_detail ? (
                <p className="mt-3 rounded-lg border border-line bg-surface-muted px-3 py-2 text-xs leading-5 text-muted">
                  {item.publish_detail}
                </p>
              ) : null}
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Human feedback"
              description="Stored verbatim. Never used as automatic ground truth."
              action={
                item.feedback.length > 0 ? (
                  <Badge tone="accent">
                    <MessageSquareText className="size-3" aria-hidden />
                    {item.feedback.length}
                  </Badge>
                ) : null
              }
            />
            <CardBody>
              <FeedbackForm
                findingId={item.id}
                onSubmitted={() => {
                  finding.reload();
                }}
              />
              <div className="mt-4 border-t border-line pt-3">
                <div className="flex items-center gap-1.5 text-xs font-medium text-ink">
                  <History className="size-3.5" aria-hidden /> History
                </div>
                {item.feedback.length === 0 ? (
                  <p className="mt-2 text-xs text-muted">No labels recorded yet.</p>
                ) : (
                  <ul className="mt-2 space-y-2.5">
                    {item.feedback.map((entry) => (
                      <li key={entry.id} className="rounded-lg border border-line bg-surface-muted/50 px-3 py-2">
                        <div className="flex flex-wrap items-center gap-2 text-xs">
                          <span className="font-semibold text-ink">{humanizeStatus(entry.label)}</span>
                          <span className="text-muted">{entry.reviewer}</span>
                          <span className="ml-auto text-[11px] text-muted">{formatRelative(entry.created_at)}</span>
                        </div>
                        {entry.comment ? <p className="mt-1 text-xs leading-5 text-muted">{entry.comment}</p> : null}
                        <p className="mt-1 text-[10px] text-muted">source: {entry.source}</p>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <div className="mt-3">
                <FeedbackSummary feedback={item.feedback} />
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Quick links" />
            <CardBody className="space-y-2 text-xs">
              <Link href={`/findings?review_id=${encodeURIComponent(item.review_id)}`} className="flex items-center gap-1.5 text-accent-strong hover:underline">
                <CheckCircle2 className="size-3.5" aria-hidden /> Findings in this review
              </Link>
              <Link href={`/reviews/${item.review_id}`} className="flex items-center gap-1.5 text-accent-strong hover:underline">
                <ShieldQuestion className="size-3.5" aria-hidden /> Review pipeline state
              </Link>
              <Link href={`/tests?review_id=${encodeURIComponent(item.review_id)}`} className="flex items-center gap-1.5 text-accent-strong hover:underline">
                <Code2 className="size-3.5" aria-hidden /> Candidate tests for this review
              </Link>
              <span className="flex items-center gap-1.5 text-muted">
                <MapPin className="size-3.5" aria-hidden /> Anchored to the parsed diff
              </span>
              <span className="flex items-center gap-1.5 text-muted">
                <Braces className="size-3.5" aria-hidden /> Raw evidence is shown above
              </span>
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  );
}
