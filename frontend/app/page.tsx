"use client";

import Link from "next/link";
import {
  ArrowRight,
  BarChart3,
  Bot,
  FolderGit2,
  GitPullRequest,
  ListChecks,
  ScanSearch,
} from "lucide-react";

import { PageHeader } from "@/components/ui/page-header";
import { Badge, SeverityBadge, StatusBadge, VerificationBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { CodeBlock } from "@/components/ui/data-display";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/states";
import { StatCard } from "@/components/ui/stat-card";
import { Table, Td, Th, Tr } from "@/components/ui/table";
import { useApiQuery } from "@/lib/hooks/use-api";
import {
  formatDuration,
  formatPercent,
  formatRelative,
  humanizeStatus,
  pluralize,
  shortSha,
  titleCase,
} from "@/lib/format";
import type { Overview } from "@/lib/types";

function Distribution({
  values,
  order,
  empty = "No observations yet",
}: {
  values: Record<string, number>;
  order: string[];
  empty?: string;
}) {
  const entries = order
    .map((key) => [key, values[key] ?? 0] as const)
    .filter(([, count]) => count > 0);
  if (entries.length === 0) return <p className="text-xs text-muted">{empty}</p>;
  const max = Math.max(...entries.map(([, count]) => count));
  return (
    <ul className="space-y-2.5">
      {entries.map(([key, count]) => (
        <li key={key}>
          <div className="mb-1 flex items-center justify-between gap-2 text-xs">
            <span className="text-muted">{humanizeStatus(key)}</span>
            <span className="font-mono tabular-nums text-ink">{count}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-line">
            <div
              className="h-full rounded-full bg-accent/70"
              style={{ width: `${Math.max(4, (count / max) * 100)}%` }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}

function Onboarding() {
  return (
    <Card className="overflow-hidden">
      <div className="bg-sidebar px-6 py-6 text-white sm:px-8">
        <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-indigo-300">
          <ScanSearch className="size-4" aria-hidden />
          First review
        </div>
        <h2 className="mt-2 text-xl font-semibold tracking-tight">Give PatchLens a repository to reason about</h2>
        <p className="mt-1 max-w-2xl text-sm leading-6 text-slate-300">
          The demo workspace uses a real Python fixture, the deterministic mock provider and the
          bundled pull-request diff. No GitHub App or paid API key is required.
        </p>
      </div>
      <div className="grid gap-6 px-6 py-6 sm:px-8 lg:grid-cols-[1.1fr_0.9fr]">
        <div>
          <ol className="space-y-4">
            {[
              ["Index the fixture", "Parse the Python AST, map tests and store embeddings."],
              ["Review the bundled diff", "Run deterministic checks, retrieval and validation."],
              ["Inspect the evidence", "Open a finding to see its rule, line anchor and limits."],
            ].map(([title, detail], index) => (
              <li key={title} className="flex gap-3">
                <span className="grid size-6 shrink-0 place-items-center rounded-full border border-line bg-surface-muted text-[11px] font-semibold text-muted">
                  {index + 1}
                </span>
                <div>
                  <p className="text-sm font-semibold text-ink">{title}</p>
                  <p className="mt-0.5 text-sm leading-6 text-muted">{detail}</p>
                </div>
              </li>
            ))}
          </ol>
          <div className="mt-5 flex flex-wrap gap-2">
            <LinkButton href="/repositories">Register a repository</LinkButton>
            <LinkButton href="/reviews/new" variant="secondary">
              Review a diff
            </LinkButton>
          </div>
        </div>
        <CodeBlock label="terminal">{`make init-db
make demo-repo
make demo-review`}</CodeBlock>
      </div>
    </Card>
  );
}

export default function OverviewPage() {
  const { data, error, loading, reload } = useApiQuery<Overview>("/api/v1/overview", {
    refreshIntervalMs: 15_000,
  });

  if (loading && !data) {
    return (
      <>
        <PageHeader title="Overview" description="Loading persisted review, finding and job state…" />
        <LoadingState rows={4} />
      </>
    );
  }
  if (error && !data) {
    return (
      <>
        <PageHeader title="Overview" description="Review workspace health and recent activity." />
        <ErrorState error={error} onRetry={reload} />
      </>
    );
  }
  if (!data) return null;

  const empty = data.repositories.total === 0 && data.reviews.total === 0;

  return (
    <div>
      <PageHeader
        eyebrow="Workspace"
        title="Overview"
        description="Persisted reviews, findings and job state. Every number links back to a row in the database; evaluation runs are counted on their own page, not here."
        actions={
          <>
            <LinkButton href="/reviews/new">New review</LinkButton>
            <LinkButton href="/repositories" variant="secondary">
              Repositories
            </LinkButton>
          </>
        }
      />

      {empty ? (
        <div className="mb-6">
          <Onboarding />
        </div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Repositories"
          value={data.repositories.total}
          detail={`${data.repositories.indexed} indexed · ${data.repositories.by_status.failed ?? 0} failed`}
          icon={FolderGit2}
          href="/repositories"
        />
        <StatCard
          label="Reviews"
          value={data.reviews.total}
          detail={
            data.reviews.success_rate === null
              ? "Success rate UNKNOWN · no terminal runs"
              : `${formatPercent(data.reviews.success_rate)} succeeded · ${data.reviews.by_status.failed ?? 0} failed`
          }
          icon={GitPullRequest}
          href="/reviews"
          tone="accent"
        />
        <StatCard
          label="Awaiting triage"
          value={data.findings.open_findings}
          detail={`${data.findings.non_rejected} non-rejected · ${data.findings.labelled_findings} labelled`}
          icon={ScanSearch}
          href="/findings"
          tone={data.findings.open_findings > 0 ? "warning" : "success"}
        />
        <StatCard
          label="Active jobs"
          value={data.jobs.active}
          detail={
            data.jobs.failure_rate === null
              ? "Failure rate UNKNOWN · no terminal jobs"
              : `${formatPercent(data.jobs.failure_rate)} terminal failure rate`
          }
          icon={ListChecks}
          href="/jobs"
          tone={data.jobs.active > 0 ? "accent" : "neutral"}
        />
      </div>

      <div className="mt-6">
        <Card>
          <CardHeader
            title="Recent reviews"
            description="Review lifecycle, model identity and anchored finding counts."
            action={
              <Link href="/reviews" className="inline-flex items-center gap-1 text-xs font-medium text-accent-strong hover:underline">
                All reviews <ArrowRight className="size-3.5" aria-hidden />
              </Link>
            }
          />
          {data.recent_reviews.length === 0 ? (
            <EmptyState
              title="No reviews yet"
              description="Run the demo or submit a unified diff to create the first persisted review."
              icon={GitPullRequest}
              action={<LinkButton href="/reviews/new">Review a diff</LinkButton>}
            />
          ) : (
            <Table label="Recent reviews">
              <thead>
                <tr>
                  <Th>Review</Th>
                  <Th>Repository</Th>
                  <Th>Status</Th>
                  <Th>Findings</Th>
                  <Th>Duration</Th>
                  <Th>Created</Th>
                </tr>
              </thead>
              <tbody>
                {data.recent_reviews.map((review) => (
                  <Tr key={review.id}>
                    <Td>
                      <Link href={`/reviews/${review.id}`} className="font-medium text-ink hover:text-accent-strong hover:underline">
                        {shortSha(review.commit_sha, 12)}
                      </Link>
                      <p className="mt-0.5 font-mono text-[10px] text-muted">{review.id}</p>
                    </Td>
                    <Td>
                      <span className="text-xs text-muted">{review.repository_full_name ?? "—"}</span>
                      <p className="mt-0.5 text-[11px] text-muted">
                        {review.is_mock_provider ? "Mock provider" : review.llm_model}
                      </p>
                    </Td>
                    <Td>
                      <StatusBadge status={review.status} />
                    </Td>
                    <Td>
                      <span className="text-xs tabular-nums text-ink">{review.finding_count}</span>
                      <span className="ml-1 text-[11px] text-muted">({review.verified_finding_count} verified)</span>
                    </Td>
                    <Td className="text-xs tabular-nums text-muted">{formatDuration(review.duration_ms)}</Td>
                    <Td className="text-xs text-muted">{formatRelative(review.created_at)}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
      </div>

      <div className="mt-4 grid items-start gap-4 md:grid-cols-3">
        <Card>
          <CardHeader title="Verification mix" description="How non-rejected findings were established." />
          <CardBody>
            <Distribution
              values={data.findings.by_verification_status}
              order={["verified", "needs_manual_verification", "ai_suggested", "rejected"]}
            />
            <div className="mt-4 border-t border-line pt-3 text-xs leading-5 text-muted">
              Verified means a deterministic check matched an added line. AI suggestions are
              structurally validated, not proven.
            </div>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Severity mix" description="Non-rejected findings by severity." />
          <CardBody>
            <Distribution
              values={data.findings.by_severity}
              order={["critical", "high", "medium", "low", "info"]}
            />
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Pipeline health" description="Observed, not assumed." />
          <CardBody>
            <dl className="divide-y divide-line/70 text-sm">
              <div className="flex items-center justify-between gap-3 py-2 first:pt-0">
                <dt className="text-muted">Review latency p50 / p95</dt>
                <dd className="font-mono text-xs text-ink">
                  {formatDuration(data.reviews.latency_ms.p50)} / {formatDuration(data.reviews.latency_ms.p95)}
                </dd>
              </div>
              <div className="flex items-center justify-between gap-3 py-2">
                <dt className="text-muted">Latency samples</dt>
                <dd className="font-mono text-xs text-ink">{data.reviews.latency_ms.sample_size}</dd>
              </div>
              <div className="flex items-center justify-between gap-3 py-2">
                <dt className="text-muted">Evaluation runs</dt>
                <dd className="text-xs text-ink">
                  {data.evaluation.runs > 0 ? (
                    <Link href="/evaluation" className="font-medium text-accent-strong hover:underline">
                      {data.evaluation.runs} recorded
                    </Link>
                  ) : (
                    <span className="text-muted">NOT RUN</span>
                  )}
                </dd>
              </div>
              <div className="flex items-center justify-between gap-3 py-2">
                <dt className="text-muted">Candidate tests</dt>
                <dd className="text-xs text-ink">
                  {data.test_intelligence.candidates} generated · {data.test_intelligence.executed} executed
                </dd>
              </div>
            </dl>
          </CardBody>
        </Card>
      </div>

      <div className="mt-6 grid items-start gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader
            title="Recent findings"
            description="Newest non-rejected findings, unaffected by feedback."
            action={
              <Link href="/findings" className="inline-flex items-center gap-1 text-xs font-medium text-accent-strong hover:underline">
                All findings <ArrowRight className="size-3.5" aria-hidden />
              </Link>
            }
          />
          {data.recent_findings.length === 0 ? (
            <EmptyState title="No findings" description="Reviews have not produced any non-rejected findings yet." icon={ScanSearch} />
          ) : (
            <ul className="divide-y divide-line">
              {data.recent_findings.slice(0, 5).map((finding) => (
                <li key={finding.id} className="px-5 py-3">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <SeverityBadge severity={finding.severity} />
                    <VerificationBadge status={finding.verification_status} />
                    <Badge tone="neutral">{titleCase(finding.category)}</Badge>
                  </div>
                  <Link
                    href={`/findings/${finding.id}`}
                    className="mt-1.5 block text-sm font-medium leading-6 text-ink hover:text-accent-strong hover:underline"
                  >
                    {finding.title}
                  </Link>
                  <p className="mt-0.5 font-mono text-[11px] text-muted">
                    {finding.path}:{finding.start_line} · {formatRelative(finding.created_at)}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <CardHeader
            title="Recent jobs"
            description="Durable queue state; workers process reviews and indexing off the request path."
            action={
              <Link href="/jobs" className="inline-flex items-center gap-1 text-xs font-medium text-accent-strong hover:underline">
                All jobs <ArrowRight className="size-3.5" aria-hidden />
              </Link>
            }
          />
          {data.recent_jobs.length === 0 ? (
            <EmptyState title="No jobs" description="Indexing and review requests create durable jobs here." icon={Bot} />
          ) : (
            <ul className="divide-y divide-line">
              {data.recent_jobs.map((job) => (
                <li key={job.id} className="flex flex-wrap items-center gap-2 px-5 py-3">
                  <StatusBadge status={job.status} />
                  <span className="text-sm font-medium text-ink">{humanizeStatus(job.kind)}</span>
                  <span className="ml-auto font-mono text-[10px] text-muted">{job.id}</span>
                  <span className="w-full text-[11px] text-muted">
                    {pluralize(job.attempts, "attempt")} / {job.max_attempts} ·{" "}
                    {formatRelative(job.created_at)} · {formatDuration(job.duration_ms)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="mt-6 grid gap-4 sm:grid-cols-3">
        <Link href="/evaluation" className="group">
          <Card className="h-full transition-colors group-hover:border-accent/30">
            <CardBody className="flex items-center gap-3">
              <BarChart3 className="size-5 text-accent" aria-hidden />
              <div>
                <p className="text-sm font-semibold text-ink">Evaluation</p>
                <p className="text-xs text-muted">Reproducible retrieval and finding metrics.</p>
              </div>
            </CardBody>
          </Card>
        </Link>
        <Link href="/tests" className="group">
          <Card className="h-full transition-colors group-hover:border-accent/30">
            <CardBody className="flex items-center gap-3">
              <Bot className="size-5 text-accent" aria-hidden />
              <div>
                <p className="text-sm font-semibold text-ink">Test intelligence</p>
                <p className="text-xs text-muted">Generated candidates and real execution state.</p>
              </div>
            </CardBody>
          </Card>
        </Link>
        <Link href="/settings" className="group">
          <Card className="h-full transition-colors group-hover:border-accent/30">
            <CardBody className="flex items-center gap-3">
              <ListChecks className="size-5 text-accent" aria-hidden />
              <div>
                <p className="text-sm font-semibold text-ink">Configuration</p>
                <p className="text-xs text-muted">Providers, sandbox and GitHub integration state.</p>
              </div>
            </CardBody>
          </Card>
        </Link>
      </div>
    </div>
  );
}
