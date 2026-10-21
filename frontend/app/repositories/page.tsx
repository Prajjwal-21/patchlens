"use client";

import Link from "next/link";
import { ArrowRight, FolderGit2, GitBranch, Layers, TriangleAlert } from "lucide-react";

import { IndexButton, RegisterRepositoryForm } from "@/components/repository-actions";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { EmptyState, ErrorState, InlineNotice, LoadingState } from "@/components/ui/states";
import { PageHeader } from "@/components/ui/page-header";
import { StatCard } from "@/components/ui/stat-card";
import { useApiQuery } from "@/lib/hooks/use-api";
import { formatDuration, formatRelative, pluralize, shortSha } from "@/lib/format";
import type { Repository } from "@/lib/types";

export default function RepositoriesPage() {
  const { data, error, loading, reload } = useApiQuery<Repository[]>("/api/v1/repositories?limit=200");

  if (loading && !data) {
    return (
      <>
        <PageHeader title="Repositories" description="Loading registered checkouts and index state…" />
        <LoadingState rows={3} />
      </>
    );
  }
  if (error && !data) {
    return (
      <>
        <PageHeader title="Repositories" description="Registered checkouts and their indexed snapshots." />
        <ErrorState error={error} onRetry={reload} />
      </>
    );
  }

  const repositories = data ?? [];
  const indexed = repositories.filter((repository) => repository.index_status === "indexed").length;
  const failed = repositories.filter((repository) => repository.index_status === "failed").length;
  const symbols = repositories.reduce((sum, item) => sum + (item.latest_version?.symbol_count ?? 0), 0);

  return (
    <div>
      <PageHeader
        eyebrow="Workspace"
        title="Repositories"
        description="Python checkouts PatchLens can parse. Indexes are immutable snapshots keyed by commit or local tree hash."
        actions={<RegisterRepositoryForm onRegistered={reload} />}
      />

      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <StatCard label="Registered" value={repositories.length} detail={`${indexed} indexed`} icon={FolderGit2} />
        <StatCard
          label="Indexed symbols"
          value={symbols}
          detail="Across the latest snapshot of each repository"
          icon={Layers}
        />
        <StatCard
          label="Index failures"
          value={failed}
          detail={failed > 0 ? "Open a repository for its error" : "No failed snapshots"}
          icon={TriangleAlert}
          tone={failed > 0 ? "danger" : "success"}
        />
      </div>

      {repositories.length === 0 ? (
        <Card>
          <EmptyState
            title="No repositories registered"
            description={
              <>
                Use <span className="font-medium text-ink">Register checkout</span> above, or run{" "}
                <code className="rounded bg-surface-muted px-1 py-0.5 font-mono text-[11px]">make demo-repo</code>{" "}
                to index the bundled fixture.
              </>
            }
            icon={FolderGit2}
            action={<LinkButton href="/reviews/new" variant="secondary">Review a diff</LinkButton>}
          />
        </Card>
      ) : (
        <div className="space-y-4">
          {repositories.map((repository) => {
            const version = repository.latest_version;
            return (
              <Card key={repository.id}>
                <CardHeader
                  title={
                    <span className="inline-flex flex-wrap items-center gap-2">
                      <Link href={`/repositories/${repository.id}`} className="hover:text-accent-strong hover:underline">
                        {repository.full_name}
                      </Link>
                      <StatusBadge status={repository.index_status} />
                      {repository.is_private ? <Badge tone="neutral">Private</Badge> : null}
                    </span>
                  }
                  description={
                    <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
                      <span className="inline-flex items-center gap-1">
                        <GitBranch className="size-3.5" aria-hidden /> {repository.default_branch}
                      </span>
                      <span>{repository.provider}</span>
                      <span>{repository.language_primary}</span>
                      <span>Updated {formatRelative(repository.last_indexed_at)}</span>
                    </span>
                  }
                  action={<IndexButton repositoryId={repository.id} onQueued={reload} />}
                />
                <CardBody>
                  {repository.last_index_error ? (
                    <InlineNotice tone="danger" title="Last index failed">
                      <p className="line-clamp-3 break-words font-mono text-xs">{repository.last_index_error}</p>
                    </InlineNotice>
                  ) : null}
                  <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                    <div>
                      <p className="text-[11px] uppercase tracking-wide text-muted">Indexed commit</p>
                      <p className="mt-1 font-mono text-xs text-ink">{shortSha(repository.indexed_commit_sha, 12)}</p>
                    </div>
                    <div>
                      <p className="text-[11px] uppercase tracking-wide text-muted">Files / symbols / tests</p>
                      <p className="mt-1 text-sm tabular-nums text-ink">
                        {version ? `${version.file_count} / ${version.symbol_count} / ${version.test_count}` : "—"}
                      </p>
                    </div>
                    <div>
                      <p className="text-[11px] uppercase tracking-wide text-muted">Embeddings</p>
                      <p className="mt-1 text-sm text-ink">
                        {version ? pluralize(version.embedding_count, "vector") : "—"}
                        {version?.embedding_provider ? (
                          <span className="ml-1 text-[11px] text-muted">({version.embedding_provider})</span>
                        ) : null}
                      </p>
                    </div>
                    <div>
                      <p className="text-[11px] uppercase tracking-wide text-muted">Index duration</p>
                      <p className="mt-1 text-sm text-ink">{formatDuration(version?.index_duration_ms ?? null)}</p>
                    </div>
                  </div>
                  <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-line pt-3">
                    <Link
                      href={`/repositories/${repository.id}`}
                      className="inline-flex items-center gap-1 text-xs font-medium text-accent-strong hover:underline"
                    >
                      Snapshots and reviews <ArrowRight className="size-3.5" aria-hidden />
                    </Link>
                    {version && version.embedding_model ? (
                      <span className="text-[11px] text-muted">Embedding model: {version.embedding_model}</span>
                    ) : null}
                    {version ? (
                      <span className="text-[11px] text-muted">Skipped files: {version.skipped_file_count}</span>
                    ) : null}
                  </div>
                </CardBody>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
