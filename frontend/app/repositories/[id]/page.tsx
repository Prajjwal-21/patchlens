"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, RefreshCcw } from "lucide-react";

import { IndexButton } from "@/components/repository-actions";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { KeyValue } from "@/components/ui/data-display";
import { ErrorState, InlineNotice, LoadingState } from "@/components/ui/states";
import { PageBreadcrumb, PageHeader } from "@/components/ui/page-header";
import { Table, Td, Th, Tr } from "@/components/ui/table";
import { useApiQuery } from "@/lib/hooks/use-api";
import {
  formatDateTime,
  formatDuration,
  formatRelative,
  pluralize,
  shortSha,
  titleCase,
} from "@/lib/format";
import type { RepositoryDetail } from "@/lib/types";

export default function RepositoryDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const { data, error, loading, reload } = useApiQuery<RepositoryDetail>(
    id ? `/api/v1/repositories/${encodeURIComponent(id)}` : null,
    { refreshIntervalMs: 8_000 },
  );

  if (loading && !data) {
    return (
      <>
        <PageHeader title="Repository" description="Loading index snapshots…" />
        <LoadingState rows={3} />
      </>
    );
  }
  if (error && !data) {
    return (
      <>
        <PageBreadcrumb items={[{ label: "Repositories", href: "/repositories" }, { label: "Unknown" }]} />
        <ErrorState error={error} onRetry={reload} title="Repository could not be loaded" />
      </>
    );
  }
  if (!data) return null;

  const { repository, versions, recent_reviews: reviews, finding_counts: findingCounts } = data;
  const version = repository.latest_version;
  const live = repository.index_status === "indexing" || repository.index_status === "queued";

  return (
    <div>
      <PageBreadcrumb items={[{ label: "Repositories", href: "/repositories" }, { label: repository.full_name }]} />
      <PageHeader
        eyebrow="Repository"
        title={repository.full_name}
        description={`${repository.provider} checkout · ${repository.language_primary} · default branch ${repository.default_branch}`}
        actions={
          <>
            <LinkButton href="/reviews/new" variant="secondary">New review</LinkButton>
            <IndexButton repositoryId={repository.id} onQueued={reload} label="Reindex" />
            <Button variant="ghost" size="sm" onClick={reload}>
              <RefreshCcw className="size-3.5" aria-hidden /> Refresh
            </Button>
          </>
        }
      />

      {repository.last_index_error ? (
        <InlineNotice tone="danger" title="Last indexing attempt failed" className="mb-5">
          <p className="break-words font-mono text-xs">{repository.last_index_error}</p>
        </InlineNotice>
      ) : null}
      {live ? (
        <InlineNotice tone="info" title="Indexing in progress" className="mb-5">
          This page refreshes every 8 seconds. A worker must be running to process the index job.
        </InlineNotice>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[1.55fr_1fr]">
        <div className="space-y-6">
          <Card>
            <CardHeader title="Latest snapshot" description="Immutable index keyed by commit or local tree hash." />
            <CardBody>
              {version ? (
                <dl className="divide-y divide-line/70">
                  <KeyValue label="Commit">{shortSha(version.commit_sha, 16)}</KeyValue>
                  <KeyValue label="Status"><StatusBadge status={version.status} /></KeyValue>
                  <KeyValue label="Indexed at">{formatDateTime(version.indexed_at)}</KeyValue>
                  <KeyValue label="Files / symbols">
                    {version.file_count} files · {pluralize(version.symbol_count, "symbol")}
                  </KeyValue>
                  <KeyValue label="Tests / embeddings">
                    {version.test_count} tests · {pluralize(version.embedding_count, "vector")}
                  </KeyValue>
                  <KeyValue label="Embedding model" mono>
                    {version.embedding_model ?? "—"}
                    {version.embedding_dim ? ` · ${version.embedding_dim} dims` : ""}
                  </KeyValue>
                  <KeyValue label="Duration">{formatDuration(version.index_duration_ms)}</KeyValue>
                  <KeyValue label="Skipped files">{version.skipped_file_count}</KeyValue>
                </dl>
              ) : (
                <p className="text-sm text-muted">
                  No snapshot yet. Use <span className="font-medium text-ink">Reindex</span> and ensure a
                  worker is running.
                </p>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Snapshots" description="Newest first; old snapshots remain queryable for audit." />
            {versions.length === 0 ? (
              <CardBody><p className="text-sm text-muted">No versions have been recorded.</p></CardBody>
            ) : (
              <Table label="Repository snapshots">
                <thead>
                  <tr>
                    <Th>Commit</Th>
                    <Th>Status</Th>
                    <Th>Symbols</Th>
                    <Th>Tests</Th>
                    <Th>Embeddings</Th>
                    <Th>Indexed</Th>
                  </tr>
                </thead>
                <tbody>
                  {versions.map((item) => (
                    <Tr key={item.id}>
                      <Td className="font-mono text-xs">{shortSha(item.commit_sha, 14)}</Td>
                      <Td><StatusBadge status={item.status} /></Td>
                      <Td className="tabular-nums">{item.symbol_count}</Td>
                      <Td className="tabular-nums">{item.test_count}</Td>
                      <Td className="tabular-nums">{item.embedding_count}</Td>
                      <Td className="text-xs text-muted">{formatRelative(item.indexed_at)}</Td>
                    </Tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Card>

          <Card>
            <CardHeader title="Recent reviews" description="Pull-request revisions reviewed against this repository." />
            {reviews.length === 0 ? (
              <CardBody><p className="text-sm text-muted">No reviews for this repository yet.</p></CardBody>
            ) : (
              <ul className="divide-y divide-line">
                {reviews.map((review) => (
                  <li key={review.id} className="flex flex-wrap items-center gap-2 px-5 py-3">
                    <StatusBadge status={review.status} />
                    <Link href={`/reviews/${review.id}`} className="text-sm font-medium text-ink hover:text-accent-strong hover:underline">
                      {shortSha(review.commit_sha, 12)}
                    </Link>
                    <span className="text-xs text-muted">{review.finding_count} findings</span>
                    <span className="ml-auto text-xs text-muted">{formatRelative(review.created_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader title="Finding state" description="All snapshots, non-rejected only." />
            <CardBody>
              {Object.keys(findingCounts).length === 0 ? (
                <p className="text-sm text-muted">No findings recorded for this repository.</p>
              ) : (
                <ul className="space-y-2">
                  {Object.entries(findingCounts).map(([status, count]) => (
                    <li key={status} className="flex items-center justify-between gap-3 text-sm">
                      <span className="text-muted">{titleCase(status)}</span>
                      <span className="font-mono tabular-nums text-ink">{count}</span>
                    </li>
                  ))}
                </ul>
              )}
              <div className="mt-4">
                <Link href={`/findings?repository_id=${encodeURIComponent(repository.id)}`} className="text-xs font-medium text-accent-strong hover:underline">
                  Filter findings by this repository →
                </Link>
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Integration details" />
            <CardBody>
              <dl className="divide-y divide-line/70">
                <KeyValue label="Repository id" mono>{repository.id}</KeyValue>
                <KeyValue label="Provider">{repository.provider}</KeyValue>
                <KeyValue label="Visibility">{repository.is_private ? "Private" : "Public / local"}</KeyValue>
                <KeyValue label="Last indexed">{formatDateTime(repository.last_indexed_at)}</KeyValue>
                <KeyValue label="Index state"><Badge tone="neutral">{titleCase(repository.index_status)}</Badge></KeyValue>
              </dl>
              <p className="mt-3 text-xs leading-5 text-muted">
                The local checkout path is intentionally not returned by the API. Indexing uses it on
                the server only.
              </p>
              <Link
                href="/repositories"
                className="mt-4 inline-flex items-center gap-1 text-xs font-medium text-accent-strong hover:underline"
              >
                <ArrowLeft className="size-3.5" aria-hidden /> All repositories
              </Link>
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  );
}
