"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { GitPullRequest, Search } from "lucide-react";

import { Badge, StatusBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/states";
import { PageHeader } from "@/components/ui/page-header";
import { Table, Td, Th, Tr } from "@/components/ui/table";
import { useApiQuery } from "@/lib/hooks/use-api";
import { buildQuery } from "@/lib/api";
import { formatDuration, formatRelative, shortSha } from "@/lib/format";
import type { Repository, Review } from "@/lib/types";

const STATUS_OPTIONS = ["", "queued", "running", "succeeded", "failed", "canceled"];

export default function ReviewsPage() {
  const [status, setStatus] = useState("");
  const [repositoryId, setRepositoryId] = useState("");
  const repositories = useApiQuery<Repository[]>("/api/v1/repositories?limit=200");

  const path = useMemo(
    () =>
      `/api/v1/reviews${buildQuery({
        status,
        repository_id: repositoryId,
        limit: 100,
      })}`,
    [status, repositoryId],
  );
  const live = useApiQuery<Review[]>(path, { refreshIntervalMs: 5_000 });

  const reviews = live.data ?? [];
  const hasActive = reviews.some((review) => review.status === "queued" || review.status === "running");

  return (
    <div>
      <PageHeader
        eyebrow="Workspace"
        title="Reviews"
        description="Each row is one end-to-end review of a diff at a specific commit, with the provider that produced it."
        actions={<LinkButton href="/reviews/new">New review</LinkButton>}
      />

      <Card className="mb-5">
        <CardBody className="flex flex-wrap items-end gap-3">
          <label className="block text-xs font-medium text-ink">
            Status
            <select
              className="mt-1 h-9 rounded-lg border border-line bg-surface px-2.5 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              value={status}
              onChange={(event) => setStatus(event.target.value)}
            >
              {STATUS_OPTIONS.map((option) => (
                <option key={option || "all"} value={option}>
                  {option ? option[0]?.toUpperCase() + option.slice(1) : "All statuses"}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-xs font-medium text-ink">
            Repository
            <select
              className="mt-1 h-9 max-w-xs rounded-lg border border-line bg-surface px-2.5 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              value={repositoryId}
              onChange={(event) => setRepositoryId(event.target.value)}
            >
              <option value="">All repositories</option>
              {(repositories.data ?? []).map((repository) => (
                <option key={repository.id} value={repository.id}>
                  {repository.full_name}
                </option>
              ))}
            </select>
          </label>
          <span className="ml-auto text-xs text-muted">
            {hasActive ? "Live reviews refresh every 5 seconds" : `${reviews.length} shown`}
          </span>
        </CardBody>
      </Card>

      {live.loading && !live.data ? <LoadingState rows={3} /> : null}
      {live.error && !live.data ? <ErrorState error={live.error} onRetry={live.reload} /> : null}

      {live.data ? (
        reviews.length === 0 ? (
          <Card>
            <EmptyState
              title="No reviews match these filters"
              description="Clear the filters, or review a unified diff to create the first run."
              icon={GitPullRequest}
              action={<LinkButton href="/reviews/new">New review</LinkButton>}
            />
          </Card>
        ) : (
          <Card className="overflow-hidden">
            <Table label="Reviews">
              <thead>
                <tr>
                  <Th>Commit / review</Th>
                  <Th>Repository</Th>
                  <Th>Status</Th>
                  <Th>Findings</Th>
                  <Th>Provider</Th>
                  <Th>Duration</Th>
                  <Th>Created</Th>
                </tr>
              </thead>
              <tbody>
                {reviews.map((review) => (
                  <Tr key={review.id}>
                    <Td>
                      <Link href={`/reviews/${review.id}`} className="font-mono text-xs font-medium text-ink hover:text-accent-strong hover:underline">
                        {shortSha(review.commit_sha, 14)}
                      </Link>
                      <p className="mt-0.5 text-[11px] text-muted">
                        {review.trigger}
                        {review.pull_request_id ? ` · PR ${review.pull_request_id}` : ""}
                      </p>
                    </Td>
                    <Td className="text-xs text-muted">{review.repository_full_name ?? "—"}</Td>
                    <Td>
                      <StatusBadge status={review.status} />
                      {review.status === "running" ? (
                        <p className="mt-1 text-[11px] text-muted">{review.stage} · {review.progress}%</p>
                      ) : null}
                    </Td>
                    <Td>
                      <span className="tabular-nums text-ink">{review.finding_count}</span>
                      <span className="ml-1 text-[11px] text-muted">({review.verified_finding_count} verified)</span>
                    </Td>
                    <Td>
                      {review.is_mock_provider ? <Badge tone="warning">Mock</Badge> : <Badge tone="accent">{review.llm_model}</Badge>}
                    </Td>
                    <Td className="text-xs tabular-nums text-muted">{formatDuration(review.duration_ms)}</Td>
                    <Td className="text-xs text-muted">
                      {formatRelative(review.created_at)}
                      {review.publish_state !== "not_published" ? (
                        <p className="mt-0.5 text-[10px]">Publish: {review.publish_state}</p>
                      ) : null}
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
            {reviews.length === 0 ? (
              <div className="flex items-center gap-2 px-5 py-4 text-sm text-muted">
                <Search className="size-4" aria-hidden /> No reviews found.
              </div>
            ) : null}
          </Card>
        )
      ) : null}
    </div>
  );
}
