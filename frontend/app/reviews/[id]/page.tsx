"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, ExternalLink, RefreshCcw } from "lucide-react";

import { FindingCard } from "@/components/finding-card";
import { Badge, ProviderBadge, StatusBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { KeyValue } from "@/components/ui/data-display";
import { PageBreadcrumb, PageHeader } from "@/components/ui/page-header";
import { ProgressBar } from "@/components/ui/data-display";
import { EmptyState, ErrorState, InlineNotice, LoadingState } from "@/components/ui/states";
import { StatCard } from "@/components/ui/stat-card";
import { useApiQuery } from "@/lib/hooks/use-api";
import { asArray, asRecord } from "@/lib/json";
import {
  formatDateTime,
  formatDuration,
  formatRelative,
  humanizeStatus,
  safeHttpUrl,
  shortSha,
  titleCase,
} from "@/lib/format";
import type { Finding, ReviewDetail, TestCandidateListResponse } from "@/lib/types";

function MetadataPanel({ review }: { review: ReviewDetail["review"] }) {
  const metadata = review.metadata;
  const retrieval = asRecord(metadata?.retrieval);
  const testIntelligence = asRecord(metadata?.test_intelligence);
  const generation = asRecord(metadata?.test_generation);
  const symbolMapping = asRecord(metadata?.symbol_mapping);
  const validation = asRecord(metadata?.validation);
  const checks = asRecord(metadata?.checks);
  const notes = Array.isArray(metadata?.validation_notes)
    ? (metadata.validation_notes as unknown[]).filter((item): item is string => typeof item === "string")
    : [];
  const relatedTests = asArray(testIntelligence?.related_tests);
  const publishedUrl = safeHttpUrl(review.published_comment_url);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader title="Run metadata" description="Facts recorded by the review pipeline." />
        <CardBody>
          <dl className="divide-y divide-line/70">
            <KeyValue label="Review id" mono>{review.id}</KeyValue>
            <KeyValue label="Commit" mono>{review.commit_sha}</KeyValue>
            <KeyValue label="Base" mono>{review.base_sha ?? "—"}</KeyValue>
            <KeyValue label="Trigger">{review.trigger}</KeyValue>
            <KeyValue label="Provider">
              <ProviderBadge provider={review.llm_provider} isMock={review.is_mock_provider} />
            </KeyValue>
            <KeyValue label="Model" mono>{review.llm_model}</KeyValue>
            <KeyValue label="Retrieval">{review.strategy}</KeyValue>
            <KeyValue label="Context items">{review.retrieved_context_count}</KeyValue>
            <KeyValue label="Changed">{review.changed_files} files · {review.changed_lines} lines</KeyValue>
            <KeyValue label="Started">{formatDateTime(review.started_at)}</KeyValue>
            <KeyValue label="Finished">{formatDateTime(review.finished_at)}</KeyValue>
            <KeyValue label="Publish state">
              <Badge tone={review.publish_state === "published" ? "success" : "neutral"}>
                {humanizeStatus(review.publish_state)}
              </Badge>
            </KeyValue>
            {publishedUrl ? (
              <KeyValue label="Comment">
                <a href={publishedUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent-strong hover:underline">
                  Open on GitHub <ExternalLink className="size-3" aria-hidden />
                </a>
              </KeyValue>
            ) : null}
          </dl>
          {review.publish_detail ? (
            <p className="mt-3 rounded-lg border border-line bg-surface-muted px-3 py-2 text-xs leading-5 text-muted">
              {review.publish_detail}
            </p>
          ) : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Retrieval and validation" description="Why context was selected, and what was rejected." />
        <CardBody>
          <dl className="divide-y divide-line/70">
            <KeyValue label="Semantic backend" mono>{String(retrieval?.backend ?? "—")}</KeyValue>
            <KeyValue label="Neural embeddings">{retrieval?.semantic_is_neural === true ? "Yes" : retrieval?.semantic_is_neural === false ? "No (hashing)" : "UNKNOWN"}</KeyValue>
            <KeyValue label="Reranker" mono>{String(retrieval?.reranker ?? "—")}</KeyValue>
            <KeyValue label="Query / results">
              {String(retrieval?.query_count ?? "—")} / {String(retrieval?.result_count ?? "—")}
            </KeyValue>
            <KeyValue label="Changed symbols">{String(symbolMapping?.changed_symbols ?? "—")}</KeyValue>
            <KeyValue label="Checks run">{String(checks?.checks_run ?? checks?.total ?? "—")}</KeyValue>
            <KeyValue label="Validation accepted">{String(validation?.accepted ?? "—")}</KeyValue>
            <KeyValue label="Validation rejected">{String(validation?.rejected ?? "—")}</KeyValue>
            <KeyValue label="Related tests">{relatedTests.length}</KeyValue>
          </dl>
          {notes.length > 0 ? (
            <ul className="mt-3 space-y-1.5 rounded-lg border border-warning/20 bg-warning-soft px-3 py-2 text-xs leading-5 text-warning-strong">
              {notes.slice(0, 6).map((note, index) => (
                <li key={index}>· {note}</li>
              ))}
            </ul>
          ) : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Candidate tests" description="Generation is not execution." />
        <CardBody>
          {generation ? (
            <dl className="divide-y divide-line/70">
              <KeyValue label="Generation state">
                <Badge tone={generation.state === "generated" ? "success" : "neutral"}>
                  {humanizeStatus(String(generation.state ?? "unknown"))}
                </Badge>
              </KeyValue>
              <KeyValue label="Attempted / generated">
                {String(generation.attempted ?? 0)} / {String(generation.generated ?? 0)}
              </KeyValue>
              <KeyValue label="Rejected by validation">{String(generation.rejected ?? 0)}</KeyValue>
              <KeyValue label="Provider">
                {generation.is_mock_provider === true ? "Mock" : String(generation.provider ?? "—")}
              </KeyValue>
              <KeyValue label="Execution">
                {String((asRecord(generation.execution)?.executed) ?? 0)} executed
              </KeyValue>
            </dl>
          ) : (
            <p className="text-sm text-muted">
              Test generation was not part of this review. Enable{" "}
              <code className="rounded bg-surface-muted px-1 py-0.5 font-mono text-[11px]">TEST_GENERATION_ENABLED</code>{" "}
              and review the diff again.
            </p>
          )}
          {generation?.detail ? (
            <p className="mt-2 text-xs leading-5 text-muted">{String(generation.detail)}</p>
          ) : null}
        </CardBody>
      </Card>
    </div>
  );
}

export default function ReviewDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [includeRejected, setIncludeRejected] = useState(false);

  const detail = useApiQuery<ReviewDetail>(
    id ? `/api/v1/reviews/${encodeURIComponent(id)}` : null,
    { refreshIntervalMs: 3_000 },
  );
  const isActive = detail.data?.review.status === "queued" || detail.data?.review.status === "running";
  const interval = isActive ? 3_000 : 0;

  const findings = useApiQuery<Finding[]>(
    id
      ? `/api/v1/reviews/${encodeURIComponent(id)}/findings?include_rejected=${includeRejected ? "true" : "false"}`
      : null,
    { refreshIntervalMs: interval },
  );
  const candidates = useApiQuery<TestCandidateListResponse>(
    id ? `/api/v1/reviews/${encodeURIComponent(id)}/test-candidates?limit=50` : null,
    { refreshIntervalMs: interval },
  );

  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const finding of findings.data ?? []) {
      if (finding.verification_status === "rejected" && !includeRejected) continue;
      counts[finding.verification_status] = (counts[finding.verification_status] ?? 0) + 1;
    }
    return counts;
  }, [findings.data, includeRejected]);

  if (detail.loading && !detail.data) {
    return (
      <>
        <PageHeader title="Review" description="Loading review state…" />
        <LoadingState rows={3} />
      </>
    );
  }
  if (detail.error && !detail.data) {
    return (
      <>
        <PageBreadcrumb items={[{ label: "Reviews", href: "/reviews" }, { label: "Unknown" }]} />
        <ErrorState error={detail.error} onRetry={detail.reload} title="Review could not be loaded" />
      </>
    );
  }
  if (!detail.data) return null;

  const { review, job, finding_summary: summary } = detail.data;
  const repositoryName = review.repository_full_name ?? "repository";
  const jobHref = job ? `/jobs/${job.id}` : null;
  const candidateItems = candidates.data?.items ?? [];

  return (
    <div>
      <PageBreadcrumb items={[{ label: "Reviews", href: "/reviews" }, { label: shortSha(review.commit_sha, 12) }]} />
      <PageHeader
        eyebrow="Review"
        title={`Review ${shortSha(review.commit_sha, 12)}`}
        description={
          <span className="inline-flex flex-wrap items-center gap-2">
            <Link href={`/repositories/${review.repository_id}`} className="text-accent-strong hover:underline">
              {repositoryName}
            </Link>
            <span>·</span>
            <span>{review.trigger}</span>
            <span>·</span>
            <span>created {formatRelative(review.created_at)}</span>
          </span>
        }
        actions={
          <>
            {jobHref ? <LinkButton href={jobHref} variant="secondary">View job</LinkButton> : null}
            <Button variant="ghost" onClick={detail.reload}>
              <RefreshCcw className="size-3.5" aria-hidden /> Refresh
            </Button>
          </>
        }
      />

      {isActive ? (
        <Card className="mb-5">
          <CardBody>
            <ProgressBar value={review.progress} label={`${humanizeStatus(review.stage)} · review is ${review.status}`} />
            <p className="mt-2 text-xs text-muted">
              This page polls every 3 seconds. Reviews run in the worker; if the worker is not running,
              the job remains queued.
            </p>
          </CardBody>
        </Card>
      ) : null}

      {review.error ? (
        <InlineNotice tone="danger" title="Review failed" className="mb-5">
          <p className="break-words font-mono text-xs">{review.error}</p>
          {review.is_mock_provider ? null : (
            <p className="mt-1">Provider failures are recorded without discarding deterministic findings.</p>
          )}
        </InlineNotice>
      ) : null}

      <div className="mb-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Findings" value={review.finding_count} detail={`${review.verified_finding_count} verified`} />
        <StatCard label="Duration" value={formatDuration(review.duration_ms)} detail={`stage: ${humanizeStatus(review.stage)}`} />
        <StatCard label="Context" value={review.retrieved_context_count} detail={`${review.strategy} retrieval`} />
        <StatCard
          label="Candidate tests"
          value={review.candidate_test_count}
          detail={candidateItems.length > 0 ? `${candidateItems.length} available` : "Not generated"}
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-[1.55fr_1fr]">
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-base font-semibold tracking-tight text-ink">Findings</h2>
              <p className="mt-0.5 text-xs text-muted">
                {Object.entries(statusCounts)
                  .map(([status, count]) => `${count} ${humanizeStatus(status).toLowerCase()}`)
                  .join(" · ") || "No findings in this selection"}
              </p>
            </div>
            <label className="inline-flex cursor-pointer items-center gap-2 text-xs text-muted">
              <input
                type="checkbox"
                checked={includeRejected}
                onChange={(event) => setIncludeRejected(event.target.checked)}
                className="size-3.5 rounded border-line text-accent focus-visible:ring-accent/40"
              />
              Include rejected
            </label>
          </div>

          {findings.loading && !findings.data ? <LoadingState rows={2} /> : null}
          {findings.error && !findings.data ? <ErrorState error={findings.error} onRetry={findings.reload} /> : null}
          {findings.data && findings.data.length === 0 ? (
            <Card>
              <EmptyState
                title="No findings for this review"
                description="A clean result is a valid result. The review still records what was checked and what was not."
              />
            </Card>
          ) : null}
          {(findings.data ?? []).map((finding) => (
            <FindingCard
              key={finding.id}
              finding={finding}
              onFeedback={findings.reload}
              showReviewLink={false}
            />
          ))}

          <Card>
            <CardHeader
              title="Candidate tests"
              description="Generated tests are distinct from tests that actually executed."
              action={candidateItems.length > 0 ? <Link href="/tests" className="text-xs font-medium text-accent-strong hover:underline">All candidates</Link> : null}
            />
            {candidateItems.length === 0 ? (
              <CardBody>
                <p className="text-sm text-muted">
                  No candidate tests were produced for this review.{" "}
                  {asRecord(review.metadata?.test_generation)?.detail
                    ? String(asRecord(review.metadata?.test_generation)?.detail)
                    : "Generation may be disabled or no changed symbol had enough source context."}
                </p>
              </CardBody>
            ) : (
              <ul className="divide-y divide-line">
                {candidateItems.map((candidate) => (
                  <li key={candidate.id} className="flex flex-wrap items-center gap-2 px-5 py-3">
                    <StatusBadge status={candidate.status} />
                    <Link href={`/tests/${candidate.id}`} className="min-w-0 flex-1 truncate font-mono text-xs text-ink hover:text-accent-strong hover:underline">
                      {candidate.suggested_test_path}
                    </Link>
                    <span className="text-[11px] text-muted">{candidate.is_mock_provider ? "mock" : candidate.model}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {job?.events && job.events.length > 0 ? (
            <Card>
              <CardHeader title="Job timeline" description={`Job ${job.id} · ${humanizeStatus(job.status)}`} />
              <CardBody>
                <ol className="space-y-3">
                  {job.events.map((event, index) => (
                    <li key={index} className="flex gap-3">
                      <span className={`mt-1.5 size-1.5 shrink-0 rounded-full ${event.level === "error" ? "bg-danger" : "bg-accent"}`} aria-hidden />
                      <div className="min-w-0">
                        <p className="text-xs font-medium text-ink">
                          {event.stage ? `${humanizeStatus(event.stage)} · ` : ""}{event.message}
                        </p>
                        <p className="text-[11px] text-muted">{formatDateTime(event.created_at)}</p>
                      </div>
                    </li>
                  ))}
                </ol>
              </CardBody>
            </Card>
          ) : null}
        </div>

        <div>
          <MetadataPanel review={review} />
          <Card className="mt-6">
            <CardHeader title="Finding summary" />
            <CardBody>
              <div className="space-y-3">
                {["critical", "high", "medium", "low", "info"].map((severity) => (
                  <div key={severity} className="flex items-center justify-between text-sm">
                    <span className="text-muted">{titleCase(severity)}</span>
                    <span className="font-mono tabular-nums text-ink">{summary.by_severity[severity] ?? 0}</span>
                  </div>
                ))}
              </div>
              <div className="mt-4 border-t border-line pt-3 text-xs leading-5 text-muted">
                {Object.entries(summary.by_category)
                  .map(([category, count]) => `${count} ${titleCase(category)}`)
                  .join(" · ") || "No categories"}
              </div>
              <div className="mt-4">
                <Link href={`/findings?review_id=${encodeURIComponent(review.id)}`} className="text-xs font-medium text-accent-strong hover:underline">
                  View all findings for this review →
                </Link>
              </div>
            </CardBody>
          </Card>
          <div className="mt-4">
            <Link href="/reviews" className="inline-flex items-center gap-1 text-xs font-medium text-accent-strong hover:underline">
              <ArrowLeft className="size-3.5" aria-hidden /> Back to reviews
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
