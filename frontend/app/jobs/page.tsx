"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ListChecks } from "lucide-react";

import { StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/states";
import { Table, Td, Th, Tr } from "@/components/ui/table";
import { buildQuery } from "@/lib/api";
import { formatCount, formatDuration, formatRelative, humanizeStatus } from "@/lib/format";
import { useApiQuery } from "@/lib/hooks/use-api";
import type { JobListResponse } from "@/lib/types";

const STATUSES = ["", "queued", "running", "succeeded", "failed", "dead", "canceled"];
const KINDS = ["", "index_repository", "review_pull_request", "generate_tests", "run_candidate_test"];
const PAGE_SIZE = 50;

export default function JobsPage() {
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState("");
  const [offset, setOffset] = useState(0);

  const path = useMemo(
    () => `/api/v1/jobs${buildQuery({ status, kind, limit: PAGE_SIZE, offset })}`,
    [status, kind, offset],
  );
  const jobs = useApiQuery<JobListResponse>(path, { refreshIntervalMs: 5_000 });
  const items = jobs.data?.items ?? [];
  const pagination = jobs.data?.pagination;
  const hasActive = items.some((job) => job.status === "queued" || job.status === "running");

  return (
    <div>
      <PageHeader
        eyebrow="Insights"
        title="Jobs"
        description="Durable queue state for indexing, reviews, test generation and execution. A queued job means no worker has claimed it yet."
      />

      <Card className="mb-5">
        <CardBody className="flex flex-wrap items-end gap-3">
          <label className="block text-xs font-medium text-ink">
            Status
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
          <label className="block text-xs font-medium text-ink">
            Kind
            <select
              className="mt-1 h-9 rounded-lg border border-line bg-surface px-2.5 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              value={kind}
              onChange={(event) => {
                setKind(event.target.value);
                setOffset(0);
              }}
            >
              {KINDS.map((value) => (
                <option key={value || "all"} value={value}>
                  {value ? humanizeStatus(value) : "All kinds"}
                </option>
              ))}
            </select>
          </label>
          <span className="ml-auto text-xs text-muted">
            {pagination ? `${formatCount(pagination.total)} total` : "Loading…"}
            {hasActive ? " · active, refreshing every 5 seconds" : ""}
          </span>
        </CardBody>
      </Card>

      {jobs.loading && !jobs.data ? <LoadingState rows={3} /> : null}
      {jobs.error && !jobs.data ? <ErrorState error={jobs.error} onRetry={jobs.reload} /> : null}

      {jobs.data ? (
        items.length === 0 ? (
          <Card>
            <EmptyState
              title="No jobs match"
              description="Index a repository or start a review; both create durable jobs."
              icon={ListChecks}
            />
          </Card>
        ) : (
          <>
            <Card className="overflow-hidden">
              <Table label="Jobs">
                <thead>
                  <tr>
                    <Th>Job</Th>
                    <Th>Kind</Th>
                    <Th>Status</Th>
                    <Th>Progress</Th>
                    <Th>Attempts</Th>
                    <Th>Duration</Th>
                    <Th>Created</Th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((job) => (
                    <Tr key={job.id}>
                      <Td>
                        <Link href={`/jobs/${job.id}`} className="font-mono text-xs font-medium text-ink hover:text-accent-strong hover:underline">
                          {job.id}
                        </Link>
                        {job.stage ? <p className="mt-0.5 text-[10px] text-muted">{humanizeStatus(job.stage)}</p> : null}
                      </Td>
                      <Td className="text-xs text-muted">{humanizeStatus(job.kind)}</Td>
                      <Td><StatusBadge status={job.status} /></Td>
                      <Td>
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 w-20 overflow-hidden rounded-full bg-line">
                            <div
                              className={`h-full ${job.status === "failed" || job.status === "dead" ? "bg-danger" : "bg-accent"}`}
                              style={{ width: `${Math.min(100, Math.max(0, job.progress))}%` }}
                            />
                          </div>
                          <span className="font-mono text-[11px] text-muted">{job.progress}%</span>
                        </div>
                      </Td>
                      <Td className="font-mono text-xs text-muted">
                        {job.attempts} / {job.max_attempts}
                      </Td>
                      <Td className="text-xs tabular-nums text-muted">{formatDuration(job.duration_ms)}</Td>
                      <Td className="text-xs text-muted">{formatRelative(job.created_at)}</Td>
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
    </div>
  );
}
