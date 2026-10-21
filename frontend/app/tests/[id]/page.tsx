"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { AlertTriangle, Play, RefreshCcw, Terminal } from "lucide-react";

import { Badge, ProviderBadge, StatusBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { CodeBlock, KeyValue } from "@/components/ui/data-display";
import { PageBreadcrumb, PageHeader } from "@/components/ui/page-header";
import { ErrorState, InlineNotice, LoadingState } from "@/components/ui/states";
import { formatApiError, postJson } from "@/lib/api";
import { formatDateTime, formatDuration } from "@/lib/format";
import { useApiQuery } from "@/lib/hooks/use-api";
import type {
  SandboxStatus,
  TestCandidateDetail,
  TestExecutionResponse,
} from "@/lib/types";

function RunOutput({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) {
    return (
      <div>
        <p className="text-xs font-medium text-muted">{label}</p>
        <p className="mt-1 text-xs text-muted">No output recorded.</p>
      </div>
    );
  }
  return (
    <div>
      <p className="text-xs font-medium text-muted">{label}</p>
      <pre className="mt-1 max-h-72 overflow-auto rounded-lg bg-[#0f131a] px-3 py-2 font-mono text-[11px] leading-5 text-slate-200">
        {value}
      </pre>
    </div>
  );
}

export default function TestCandidateDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const candidate = useApiQuery<TestCandidateDetail>(
    id ? `/api/v1/test-candidates/${encodeURIComponent(id)}` : null,
    { refreshIntervalMs: 4_000 },
  );
  const sandbox = useApiQuery<SandboxStatus>("/api/v1/test-execution");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const execute = async () => {
    if (!id) return;
    setPending(true);
    setError(null);
    setNote(null);
    try {
      const response = await postJson<TestExecutionResponse>(
        `/api/v1/test-candidates/${encodeURIComponent(id)}/execute`,
        { wait: true },
      );
      candidate.setData(response.candidate);
      setNote(response.note ?? "Execution finished.");
      sandbox.reload();
    } catch (cause) {
      setError(formatApiError(cause));
      candidate.reload();
      sandbox.reload();
    } finally {
      setPending(false);
    }
  };

  if (candidate.loading && !candidate.data) {
    return (
      <>
        <PageHeader title="Candidate test" description="Loading candidate source and execution state…" />
        <LoadingState rows={3} />
      </>
    );
  }
  if (candidate.error && !candidate.data) {
    return (
      <>
        <PageBreadcrumb items={[{ label: "Test intelligence", href: "/tests" }, { label: "Unknown" }]} />
        <ErrorState error={candidate.error} onRetry={candidate.reload} title="Candidate could not be loaded" />
      </>
    );
  }
  if (!candidate.data) return null;

  const item = candidate.data;
  const active = item.status === "queued" || item.status === "running";
  const sandboxAvailable = sandbox.data?.available === true;
  const runMetadata = Object.entries(item.run_metadata ?? {});
  const sandboxReason = sandbox.data?.reason ?? sandbox.data?.detail ?? null;

  return (
    <div>
      <PageBreadcrumb
        items={[{ label: "Test intelligence", href: "/tests" }, { label: item.suggested_test_path }]}
      />
      <PageHeader
        eyebrow="Candidate test"
        title={item.suggested_test_path}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <StatusBadge status={item.status} />
            <ProviderBadge provider={item.provider} isMock={item.is_mock_provider} />
            <span className="font-mono text-xs">
              {item.target_path}
              {item.target_symbol ? `::${item.target_symbol}` : ""}
            </span>
            {item.review_id ? (
              <Link href={`/reviews/${item.review_id}`} className="text-xs text-accent-strong hover:underline">
                from review
              </Link>
            ) : null}
          </span>
        }
        actions={
          <>
            <Button loading={pending} onClick={execute} disabled={!sandboxAvailable || active}>
              <Play className="size-3.5" aria-hidden /> {item.executed_at ? "Run again" : "Execute candidate"}
            </Button>
            <Button variant="ghost" onClick={() => { candidate.reload(); sandbox.reload(); }}>
              <RefreshCcw className="size-3.5" aria-hidden /> Refresh
            </Button>
          </>
        }
      />

      {!sandboxAvailable ? (
        <InlineNotice
          tone="warning"
          title={item.executed_at ? "Execution is currently unavailable" : "Execution is not available"}
          className="mb-5"
        >
          {!item.executed_at ? (
            <>{sandboxReason ? `${sandboxReason}. ` : "The backend did not report an available execution tier. "}</>
          ) : null}
          {item.executed_at
            ? "This candidate has a recorded run from an earlier configuration; the result below is unchanged."
            : "You can still inspect the generated source; no process was run."}
        </InlineNotice>
      ) : null}

      {error ? (
        <InlineNotice tone="danger" title="Execution failed" className="mb-5">
          {error}
        </InlineNotice>
      ) : null}
      {note ? (
        <InlineNotice tone="success" title="Execution finished" className="mb-5">
          {note}
        </InlineNotice>
      ) : null}
      {active ? (
        <InlineNotice tone="info" title="Execution is queued">
          A worker must pick up this job. This page refreshes every 4 seconds.
        </InlineNotice>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[1.5fr_1fr]">
        <div className="space-y-5">
          <Card>
            <CardHeader
              title="Generated test source"
              description="Stored exactly as generated and validated. Review it before trusting its assertions."
              action={
                <Badge tone={item.source.includes("TODO") ? "warning" : "neutral"}>
                  {item.source.split("\n").length} lines
                </Badge>
              }
            />
            <CardBody>
              <CodeBlock label={item.suggested_test_path} maxHeight="32rem">
                {item.source}
              </CodeBlock>
              <p className="mt-2 text-[11px] leading-5 text-muted">
                Generation: {item.provider} / {item.model}. {item.rationale ?? "No rationale was recorded."}
              </p>
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Execution"
              description="Only a real process result is shown here. Green means the generated test passed, not that the code is correct."
              action={<StatusBadge status={item.status} />}
            />
            <CardBody>
              {item.status === "execution_disabled" ? (
                <InlineNotice tone="warning" title="Execution was refused">
                  {item.execution_disabled_reason ?? "The backend refused to run this candidate."}
                </InlineNotice>
              ) : item.executed_at === null ? (
                <InlineNotice tone="neutral" title="Not executed">
                  This candidate has never run. Its status reflects validation only.
                </InlineNotice>
              ) : (
                <div className="space-y-4">
                  <dl className="grid gap-x-6 gap-y-0 sm:grid-cols-2">
                    <div>
                      <KeyValue label="Exit code">{item.exit_code ?? "UNKNOWN"}</KeyValue>
                      <KeyValue label="Duration">{formatDuration(item.duration_ms)}</KeyValue>
                      <KeyValue label="Collected tests">{item.collected_tests ?? "UNKNOWN"}</KeyValue>
                    </div>
                    <div>
                      <KeyValue label="Timed out">{item.timed_out ? "Yes" : "No"}</KeyValue>
                      <KeyValue label="Output truncated">{item.output_truncated ? "Yes" : "No"}</KeyValue>
                      <KeyValue label="Executed at">{formatDateTime(item.executed_at)}</KeyValue>
                    </div>
                  </dl>
                  <RunOutput label="stdout tail" value={item.stdout_tail} />
                  <RunOutput label="stderr tail" value={item.stderr_tail} />
                </div>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Run metadata" description="Command, sandbox tier and any recorded limits." />
            <CardBody>
              {runMetadata.length === 0 ? (
                <p className="text-sm text-muted">No run metadata was recorded.</p>
              ) : (
                <pre className="overflow-x-auto rounded-lg border border-line bg-surface-muted px-3 py-2 font-mono text-[11px] leading-5 text-muted">
                  {JSON.stringify(item.run_metadata, null, 2)}
                </pre>
              )}
            </CardBody>
          </Card>
        </div>

        <div className="space-y-5">
          <Card>
            <CardHeader title="Identity" />
            <CardBody>
              <dl className="divide-y divide-line/70">
                <KeyValue label="Candidate id" mono>{item.id}</KeyValue>
                <KeyValue label="Target" mono>
                  {item.target_path}
                  {item.target_symbol ? `::${item.target_symbol}` : ""}
                </KeyValue>
                <KeyValue label="Suggested path" mono>{item.suggested_test_path}</KeyValue>
                <KeyValue label="Repository">
                  <Link href={`/repositories/${item.repository_id}`} className="text-xs text-accent-strong hover:underline">
                    {item.repository_id}
                  </Link>
                </KeyValue>
                <KeyValue label="Review">
                  {item.review_id ? (
                    <Link href={`/reviews/${item.review_id}`} className="font-mono text-xs text-accent-strong hover:underline">
                      {item.review_id}
                    </Link>
                  ) : (
                    "Not review-scoped"
                  )}
                </KeyValue>
                <KeyValue label="Finding">
                  {item.finding_id ? (
                    <Link href={`/findings/${item.finding_id}`} className="font-mono text-xs text-accent-strong hover:underline">
                      {item.finding_id}
                    </Link>
                  ) : (
                    "—"
                  )}
                </KeyValue>
                <KeyValue label="Provider">
                  {item.provider} / {item.model}
                </KeyValue>
                <KeyValue label="Created">{formatDateTime(item.created_at)}</KeyValue>
              </dl>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Sandbox" description="Effective tier, not just configuration." />
            <CardBody>
              {sandbox.data ? (
                <dl className="divide-y divide-line/70">
                  <KeyValue label="Requested" mono>{sandbox.data.requested_mode}</KeyValue>
                  <KeyValue label="Effective" mono>{sandbox.data.effective_mode}</KeyValue>
                  <KeyValue label="Available">
                    <Badge tone={sandboxAvailable ? "success" : "warning"}>
                      {sandboxAvailable ? "Yes" : "No"}
                    </Badge>
                  </KeyValue>
                  <KeyValue label="Candidate sandbox">
                    {item.sandbox_mode ?? "UNKNOWN"}
                  </KeyValue>
                  <KeyValue label="Detail">{sandbox.data.detail}</KeyValue>
                  {item.sandbox_detail ? <KeyValue label="Recorded">{item.sandbox_detail}</KeyValue> : null}
                </dl>
              ) : (
                <p className="text-sm text-muted">Sandbox status is unavailable.</p>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Safety boundary" />
            <CardBody className="space-y-2 text-xs leading-5 text-muted">
              <p className="flex gap-2">
                <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
                The subprocess tier is development-only and is not a security boundary.
              </p>
              <p className="flex gap-2">
                <Terminal className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden />
                Docker execution runs with no network and bounded time/memory. The recorded exit code is
                the real pytest process status.
              </p>
            </CardBody>
          </Card>

          <LinkButton href="/tests" variant="secondary" className="w-full">
            Back to candidates
          </LinkButton>
        </div>
      </div>
    </div>
  );
}
