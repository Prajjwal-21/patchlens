"use client";

import { Suspense, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FlaskConical, ShieldAlert, Terminal } from "lucide-react";

import { Badge, ProviderBadge, StatusBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState, ErrorState, InlineNotice, LoadingState } from "@/components/ui/states";
import { Table, Td, Th, Tr } from "@/components/ui/table";
import { buildQuery } from "@/lib/api";
import { formatCount, formatDuration, formatRelative, humanizeStatus } from "@/lib/format";
import { useApiQuery } from "@/lib/hooks/use-api";
import type {
  Repository,
  SandboxStatus,
  TestCandidateListResponse,
} from "@/lib/types";

const STATUSES = [
  "",
  "generated",
  "execution_disabled",
  "queued",
  "running",
  "passed",
  "failed",
  "error",
];
const PAGE_SIZE = 50;

function TestIntelligenceView() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const reviewId = searchParams.get("review_id") ?? "";

  const [repositoryId, setRepositoryId] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);

  const repositories = useApiQuery<Repository[]>("/api/v1/repositories?limit=200");
  const sandbox = useApiQuery<SandboxStatus>("/api/v1/test-execution");

  const query = useMemo(
    () =>
      buildQuery({
        repository_id: repositoryId,
        review_id: reviewId,
        status,
        limit: PAGE_SIZE,
        offset,
      }),
    [repositoryId, reviewId, status, offset],
  );

  const candidates = useApiQuery<TestCandidateListResponse>(`/api/v1/test-candidates${query}`, {
    refreshIntervalMs: 5_000,
  });
  const items = candidates.data?.items ?? [];
  const pagination = candidates.data?.pagination;
  const hasActive = items.some((item) => item.status === "queued" || item.status === "running");
  const sandboxStatus = sandbox.data;

  return (
    <div>
      <PageHeader
        eyebrow="Workspace"
        title="Test intelligence"
        description="Candidates are generated tests; execution is a separate, explicitly enabled step. The status column records exactly what happened, including when execution was refused by the sandbox."
        actions={
          <LinkButton href="/tests/new">Generate candidates</LinkButton>
        }
      />

      <Card className="mb-5">
        <CardHeader
          title="Execution sandbox"
          description="The effective tier comes from a runtime probe, not from the configured setting alone."
          action={
            sandboxStatus ? (
              <Badge tone={sandboxStatus.available ? "success" : "warning"}>
                {sandboxStatus.available ? "Available" : "Unavailable"}
              </Badge>
            ) : null
          }
        />
        <CardBody>
          {sandbox.loading && !sandboxStatus ? (
            <p className="text-sm text-muted">Probing execution sandbox…</p>
          ) : sandbox.error && !sandboxStatus ? (
            <ErrorState error={sandbox.error} onRetry={sandbox.reload} title="Sandbox status is unavailable" />
          ) : sandboxStatus ? (
            <div className="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
              <div>
                <p className="text-xs text-muted">Requested</p>
                <p className="mt-0.5 font-mono text-xs text-ink">{sandboxStatus.requested_mode}</p>
              </div>
              <div>
                <p className="text-xs text-muted">Effective</p>
                <p className="mt-0.5 font-mono text-xs text-ink">{sandboxStatus.effective_mode}</p>
              </div>
              <div className="sm:col-span-2">
                <p className="text-xs text-muted">Probe detail</p>
                <p className="mt-0.5 text-xs leading-5 text-ink">{sandboxStatus.detail}</p>
              </div>
              {sandboxStatus.reason ? (
                <div className="sm:col-span-2 lg:col-span-4">
                  <InlineNotice tone="warning" title="Why execution is currently unavailable">
                    {sandboxStatus.reason}. This describes the current sandbox; candidates with a
                    recorded run keep that result.
                  </InlineNotice>
                </div>
              ) : null}
              {Object.keys(sandboxStatus.limits).length > 0 ? (
                <div className="sm:col-span-2 lg:col-span-4">
                  <p className="text-xs text-muted">Recorded limits</p>
                  <pre className="mt-1 overflow-x-auto rounded-lg border border-line bg-surface-muted px-3 py-2 font-mono text-[11px] leading-5 text-muted">
                    {JSON.stringify(sandboxStatus.limits, null, 2)}
                  </pre>
                </div>
              ) : null}
            </div>
          ) : null}
        </CardBody>
      </Card>

      <Card className="mb-5">
        <CardBody className="flex flex-wrap items-end gap-3">
          <label className="block text-xs font-medium text-ink">
            Repository
            <select
              className="mt-1 h-9 max-w-xs rounded-lg border border-line bg-surface px-2.5 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              value={repositoryId}
              onChange={(event) => {
                setRepositoryId(event.target.value);
                setOffset(0);
              }}
            >
              <option value="">All repositories</option>
              {(repositories.data ?? []).map((repository) => (
                <option key={repository.id} value={repository.id}>
                  {repository.full_name}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-xs font-medium text-ink">
            Candidate status
            <select
              className="mt-1 h-9 rounded-lg border border-line bg-surface px-2.5 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                setOffset(0);
              }}
            >
              {STATUSES.map((value) => (
                <option key={value || "all"} value={value}>
                  {value ? humanizeStatus(value) : "All statuses"}
                </option>
              ))}
            </select>
          </label>
          <span className="ml-auto text-xs text-muted">
            {pagination ? `${formatCount(pagination.total)} total` : "Loading…"}
            {hasActive ? " · live, refreshing every 5 seconds" : ""}
          </span>
        </CardBody>
        {reviewId ? (
          <div className="border-t border-line px-5 py-2.5 text-xs text-accent-strong">
            Filtered to review <span className="font-mono">{reviewId}</span>.{" "}
            <button type="button" className="font-semibold underline" onClick={() => router.replace("/tests")}>
              Show all reviews
            </button>
          </div>
        ) : null}
      </Card>

      {candidates.loading && !candidates.data ? <LoadingState rows={3} /> : null}
      {candidates.error && !candidates.data ? <ErrorState error={candidates.error} onRetry={candidates.reload} /> : null}

      {candidates.data ? (
        items.length === 0 ? (
          <Card>
            <EmptyState
              title="No candidate tests"
              description="Reviews generate candidates only when test generation is enabled and a changed symbol has source. You can also request generation explicitly for an indexed symbol."
              icon={FlaskConical}
              action={<LinkButton href="/tests/new">Generate candidates</LinkButton>}
            />
          </Card>
        ) : (
          <>
            <Card className="overflow-hidden">
              <Table label="Test candidates">
                <thead>
                  <tr>
                    <Th>Candidate</Th>
                    <Th>Target</Th>
                    <Th>Provider</Th>
                    <Th>Status</Th>
                    <Th>Execution</Th>
                    <Th>Created</Th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((candidate) => (
                    <Tr key={candidate.id}>
                      <Td>
                        <Link
                          href={`/tests/${candidate.id}`}
                          className="font-mono text-xs font-medium text-ink hover:text-accent-strong hover:underline"
                        >
                          {candidate.suggested_test_path}
                        </Link>
                        <p className="mt-0.5 font-mono text-[10px] text-muted">{candidate.id}</p>
                      </Td>
                      <Td>
                        <p className="font-mono text-[11px] text-ink">{candidate.target_path}</p>
                        <p className="text-[11px] text-muted">{candidate.target_symbol ?? "—"}</p>
                        {candidate.review_id ? (
                          <Link href={`/reviews/${candidate.review_id}`} className="mt-0.5 block text-[10px] text-accent-strong hover:underline">
                            from review {candidate.review_id.slice(0, 10)}
                          </Link>
                        ) : null}
                      </Td>
                      <Td>
                        <ProviderBadge provider={candidate.provider} isMock={candidate.is_mock_provider} />
                        <p className="mt-1 max-w-[9rem] truncate text-[10px] text-muted" title={candidate.model}>
                          {candidate.model}
                        </p>
                      </Td>
                      <Td>
                        <StatusBadge status={candidate.status} />
                        {candidate.execution_disabled_reason ? (
                          <p className="mt-1 max-w-[14rem] text-[10px] leading-4 text-warning-strong">
                            {candidate.execution_disabled_reason}
                          </p>
                        ) : null}
                      </Td>
                      <Td className="text-xs text-muted">
                        {candidate.exit_code === null ? (
                          <span>exit UNKNOWN</span>
                        ) : (
                          <span className={candidate.exit_code === 0 ? "text-success-strong" : "text-danger-strong"}>
                            exit {candidate.exit_code}
                          </span>
                        )}
                        <p className="mt-0.5 text-[10px]">{formatDuration(candidate.duration_ms)}</p>
                        {candidate.sandbox_mode ? <p className="text-[10px]">sandbox: {candidate.sandbox_mode}</p> : null}
                      </Td>
                      <Td className="text-xs text-muted">{formatRelative(candidate.created_at)}</Td>
                    </Tr>
                  ))}
                </tbody>
              </Table>
            </Card>
            <div className="mt-5 flex flex-wrap items-center justify-between gap-3 text-xs text-muted">
              <span>
                Showing {formatCount(offset + 1)}–{formatCount(offset + items.length)} of{" "}
                {formatCount(pagination?.total ?? items.length)}
              </span>
              <div className="flex items-center gap-2">
                <Button variant="secondary" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                  Previous
                </Button>
                <Button variant="secondary" size="sm" disabled={!pagination?.has_more} onClick={() => setOffset(offset + PAGE_SIZE)}>
                  Next
                </Button>
              </div>
            </div>
          </>
        )
      ) : null}

      <div className="mt-6 grid gap-4 sm:grid-cols-2">
        <Card>
          <CardBody className="flex items-start gap-3">
            <ShieldAlert className="mt-0.5 size-5 shrink-0 text-warning" aria-hidden />
            <div>
              <p className="text-sm font-semibold text-ink">Generation is not validation</p>
              <p className="mt-1 text-xs leading-5 text-muted">
                Generation alone does not execute a candidate. Even a passing execution covers only
                the assertions the generated test contains.
              </p>
            </div>
          </CardBody>
        </Card>
        <Card>
          <CardBody className="flex items-start gap-3">
            <Terminal className="mt-0.5 size-5 shrink-0 text-accent" aria-hidden />
            <div>
              <p className="text-sm font-semibold text-ink">Execution is explicit</p>
              <p className="mt-1 text-xs leading-5 text-muted">
                The subprocess tier is development-only and is not a security boundary. Use the Docker
                tier with no network for untrusted code.
              </p>
            </div>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

export default function TestsPage() {
  return (
    <Suspense fallback={<LoadingState rows={3} />}>
      <TestIntelligenceView />
    </Suspense>
  );
}
