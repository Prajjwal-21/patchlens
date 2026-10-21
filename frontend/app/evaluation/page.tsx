"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { BarChart3, Play, RefreshCcw } from "lucide-react";

import { StatusBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { KeyValue } from "@/components/ui/data-display";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState, ErrorState, InlineNotice, LoadingState } from "@/components/ui/states";
import { Table, Td, Th, Tr } from "@/components/ui/table";
import { formatApiError, postJson } from "@/lib/api";
import {
  formatDateTime,
  formatDuration,
  formatMetric,
  formatPercent,
  humanizeStatus,
  titleCase,
} from "@/lib/format";
import { asRecord } from "@/lib/json";
import { useApiQuery } from "@/lib/hooks/use-api";
import { useMeta } from "@/lib/hooks/use-meta";
import type {
  EvalDataset,
  EvalDatasetListResponse,
  EvalRun,
  EvalRunDetail,
  FeedbackAggregate,
  Repository,
} from "@/lib/types";

function valueFrom(record: Record<string, unknown> | null, key: string): unknown {
  return record ? record[key] : null;
}

function RunHighlights({ run }: { run: EvalRun }) {
  if (run.status !== "succeeded") {
    return (
      <p className="text-xs text-muted">
        Latest run is {humanizeStatus(run.status)}; no metrics are being presented as results.
      </p>
    );
  }

  if (run.kind === "retrieval") {
    const best = valueFrom(run.metrics, "best_strategy");
    const bestMetrics = typeof best === "string" ? run.strategy_metrics[best] : null;
    const recallKey = Object.keys(bestMetrics ?? {}).find((key) => key.startsWith("recall@"));
    const mrrKey = Object.keys(bestMetrics ?? {}).find((key) => key.startsWith("mrr@"));
    return (
      <dl className="grid grid-cols-2 gap-3 text-xs">
        <div>
          <dt className="text-muted">Best strategy</dt>
          <dd className="mt-0.5 font-mono text-[11px] text-ink">{typeof best === "string" ? best : "UNKNOWN"}</dd>
        </div>
        <div>
          <dt className="text-muted">Recall / MRR</dt>
          <dd className="mt-0.5 font-mono text-[11px] text-ink">
            {recallKey ? formatMetric(bestMetrics?.[recallKey]) : "UNKNOWN"} /{" "}
            {mrrKey ? formatMetric(bestMetrics?.[mrrKey]) : "UNKNOWN"}
          </dd>
        </div>
      </dl>
    );
  }

  if (run.kind === "review") {
    const overall = asRecord(run.metrics.overall);
    const mustFind = asRecord(run.metrics.must_find);
    return (
      <dl className="grid grid-cols-2 gap-3 text-xs">
        <div>
          <dt className="text-muted">Precision / recall</dt>
          <dd className="mt-0.5 font-mono text-[11px] text-ink">
            {formatMetric(overall?.precision)} / {formatMetric(overall?.recall)}
          </dd>
        </div>
        <div>
          <dt className="text-muted">Must-find recall</dt>
          <dd className="mt-0.5 font-mono text-[11px] text-ink">{formatMetric(mustFind?.recall)}</dd>
        </div>
      </dl>
    );
  }

  if (run.kind === "test_generation") {
    return (
      <dl className="grid grid-cols-2 gap-3 text-xs">
        <div>
          <dt className="text-muted">Validity rate</dt>
          <dd className="mt-0.5 font-mono text-[11px] text-ink">{formatPercent(run.metrics.validity_rate)}</dd>
        </div>
        <div>
          <dt className="text-muted">Execution / pass rate</dt>
          <dd className="mt-0.5 font-mono text-[11px] text-ink">
            {formatPercent(run.metrics.execution_rate)} / {formatPercent(run.metrics.pass_rate_on_original)}
          </dd>
        </div>
      </dl>
    );
  }

  return <p className="text-xs text-muted">No summary for kind {run.kind}.</p>;
}

function DatasetCard({
  dataset,
  onRun,
  running,
  href,
}: {
  dataset: EvalDataset;
  onRun: () => void;
  running: boolean;
  href: string;
}) {
  return (
    <Card className="flex h-full flex-col">
      <CardHeader
        title={
          <span className="flex flex-wrap items-center gap-2">
            {dataset.name}
            <StatusBadge status={dataset.status} />
          </span>
        }
        description={
          <span>
            {titleCase(dataset.kind)} · v{dataset.version} · {dataset.case_count} cases
            {dataset.repository ? ` · ${dataset.repository}` : ""}
          </span>
        }
      />
      <CardBody className="flex flex-1 flex-col">
        {dataset.description ? <p className="text-xs leading-5 text-muted">{dataset.description}</p> : null}
        <div className="mt-3 rounded-lg border border-line bg-surface-muted/50 p-3">
          {dataset.latest_run ? (
            <>
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <span className="text-[11px] font-medium text-ink">Latest run</span>
                <span className="text-[10px] text-muted">{formatDateTime(dataset.latest_run.finished_at)}</span>
              </div>
              <RunHighlights run={dataset.latest_run} />
            </>
          ) : (
            <p className="text-xs text-muted">
              <span className="font-semibold text-ink">NOT RUN.</span> This dataset has no recorded run;
              no metrics exist yet.
            </p>
          )}
        </div>
        {dataset.limitations ? (
          <p className="mt-3 text-[11px] leading-5 text-muted">
            <span className="font-medium text-ink">Limitations:</span> {dataset.limitations}
          </p>
        ) : null}
        <div className="mt-auto flex flex-wrap items-center gap-2 pt-4">
          <Button size="sm" loading={running} onClick={onRun}>
            <Play className="size-3.5" aria-hidden /> Run now
          </Button>
          <LinkButton href={href} size="sm" variant="secondary">
            Open dataset
          </LinkButton>
          {dataset.latest_run ? (
            <Link
              href={`/evaluation/runs/${dataset.latest_run.id}`}
              className="text-xs font-medium text-accent-strong hover:underline"
            >
              Latest run
            </Link>
          ) : null}
        </div>
      </CardBody>
    </Card>
  );
}

export default function EvaluationPage() {
  const router = useRouter();
  const { meta } = useMeta();
  const datasets = useApiQuery<EvalDatasetListResponse>("/api/v1/evaluation/datasets");
  const runs = useApiQuery<{ items: EvalRun[] }>("/api/v1/evaluation/runs?limit=20");
  const feedback = useApiQuery<FeedbackAggregate>("/api/v1/findings/feedback/aggregate");
  const repositories = useApiQuery<Repository[]>("/api/v1/repositories?limit=200");

  const datasetItems = useMemo(() => datasets.data?.items ?? [], [datasets.data]);
  const [selection, setSelection] = useState("");
  const [repositoryId, setRepositoryId] = useState("");
  const [strategyOverride, setStrategyOverride] = useState<string[] | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Derive both defaults instead of syncing them in effects: an empty selection
  // means "the first dataset", and no override means "every server strategy".
  const selectedId = selection || (datasetItems[0]?.id ?? "");
  const strategies = strategyOverride ?? meta?.retrieval_strategies ?? [];
  const setSelectedId = setSelection;
  const setStrategies = (update: string[] | ((current: string[]) => string[])) => {
    setStrategyOverride((current) => {
      const base = current ?? meta?.retrieval_strategies ?? [];
      return typeof update === "function" ? update(base) : update;
    });
  };

  const selected = datasetItems.find((dataset) => dataset.id === selectedId);
  const retrieval = selected?.kind === "retrieval";

  const run = async (datasetId: string) => {
    setPending(true);
    setError(null);
    try {
      const dataset = datasetItems.find((item) => item.id === datasetId);
      const result = await postJson<EvalRunDetail>("/api/v1/evaluation/runs", {
        dataset_id: datasetId,
        repository_id: repositoryId || null,
        strategies: dataset?.kind === "retrieval" ? strategies : null,
      });
      datasets.reload();
      runs.reload();
      router.push(`/evaluation/runs/${encodeURIComponent(result.id)}`);
    } catch (cause) {
      setError(formatApiError(cause));
      setPending(false);
    }
  };

  return (
    <div>
      <PageHeader
        eyebrow="Insights"
        title="Evaluation"
        description="Reproducible runs over bundled, labelled datasets. A dataset without a run is shown as NOT RUN; an undefined metric is UNKNOWN, never zero."
        actions={
          <Button
            variant="secondary"
            onClick={() => {
              datasets.reload();
              runs.reload();
              feedback.reload();
            }}
          >
            <RefreshCcw className="size-3.5" aria-hidden /> Refresh
          </Button>
        }
      />

      <Card className="mb-6">
        <CardHeader
          title="Run an evaluation"
          description="Runs execute synchronously and record the exact dataset version, configuration and per-case output."
        />
        <CardBody>
          <div className="grid gap-3 lg:grid-cols-[1.1fr_1fr_auto] lg:items-end">
            <label className="block text-xs font-medium text-ink">
              Dataset
              <select
                className="mt-1 h-10 w-full rounded-lg border border-line bg-surface px-3 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                value={selectedId}
                onChange={(event) => setSelectedId(event.target.value)}
              >
                <option value="">Select a dataset…</option>
                {datasetItems.map((dataset) => (
                  <option key={dataset.id} value={dataset.id}>
                    {dataset.name} · {dataset.kind} · {dataset.case_count} cases
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-xs font-medium text-ink">
              Repository override <span className="font-normal text-muted">(optional)</span>
              <select
                className="mt-1 h-10 w-full rounded-lg border border-line bg-surface px-3 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                value={repositoryId}
                onChange={(event) => setRepositoryId(event.target.value)}
              >
                <option value="">Use the dataset&apos;s repository</option>
                {(repositories.data ?? []).map((repository) => (
                  <option key={repository.id} value={repository.id}>
                    {repository.full_name}
                  </option>
                ))}
              </select>
            </label>
            <Button
              loading={pending}
              disabled={!selectedId || (retrieval && strategies.length === 0)}
              onClick={() => {
                if (selectedId) void run(selectedId);
              }}
            >
              <Play className="size-4" aria-hidden /> Run evaluation
            </Button>
          </div>
          {retrieval ? (
            <div className="mt-3">
              <p className="text-xs font-medium text-ink">Retrieval strategies</p>
              <div className="mt-1.5 flex flex-wrap gap-2">
                {(meta?.retrieval_strategies ?? []).map((strategy) => {
                  const checked = strategies.includes(strategy);
                  return (
                    <label
                      key={strategy}
                      className={`inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] ${
                        checked
                          ? "border-accent/30 bg-accent-soft text-accent-strong"
                          : "border-line bg-surface text-muted"
                      }`}
                    >
                      <input
                        type="checkbox"
                        className="sr-only"
                        checked={checked}
                        onChange={() =>
                          setStrategies((current) =>
                            checked ? current.filter((item) => item !== strategy) : [...current, strategy],
                          )
                        }
                      />
                      {strategy}
                    </label>
                  );
                })}
              </div>
            </div>
          ) : null}
          {selected && !retrieval ? (
            <p className="mt-2 text-[11px] text-muted">
              Strategy selection applies only to retrieval datasets. {titleCase(selected.kind)} runs use
              the dataset&apos;s own cases and labels.
            </p>
          ) : null}
          {error ? (
            <InlineNotice tone="danger" title="Evaluation was not started" className="mt-3">
              {error}
            </InlineNotice>
          ) : null}
        </CardBody>
      </Card>

      {datasets.loading && !datasets.data ? <LoadingState rows={2} /> : null}
      {datasets.error && !datasets.data ? <ErrorState error={datasets.error} onRetry={datasets.reload} /> : null}
      {datasets.data && datasetItems.length === 0 ? (
        <Card>
          <EmptyState
            title="No evaluation datasets"
            description="Seed the bundled datasets with `make eval-seed`. They are small, labelled fixtures, not a benchmark of production quality."
            icon={BarChart3}
          />
        </Card>
      ) : null}

      {datasetItems.length > 0 ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {datasetItems.map((dataset) => (
            <DatasetCard
              key={dataset.id}
              dataset={dataset}
              running={pending && selectedId === dataset.id}
              onRun={() => {
                setSelectedId(dataset.id);
                void run(dataset.id);
              }}
              href={`/evaluation/datasets/${dataset.id}`}
            />
          ))}
        </div>
      ) : null}

      <div className="mt-6 grid gap-6 xl:grid-cols-[1.4fr_1fr]">
        <Card>
          <CardHeader
            title="Recent runs"
            description="Every run is stored with its configuration, limitations and per-case results."
            action={
              <span className="text-xs text-muted">
                {runs.data ? `${runs.data.items.length} loaded` : ""}
              </span>
            }
          />
          {runs.loading && !runs.data ? (
            <CardBody>
              <p className="text-sm text-muted">Loading runs…</p>
            </CardBody>
          ) : runs.data && runs.data.items.length === 0 ? (
            <EmptyState title="No runs yet" description="Run a dataset above; the result will be listed here." icon={BarChart3} />
          ) : runs.data ? (
            <Table label="Evaluation runs">
              <thead>
                <tr>
                  <Th>Run</Th>
                  <Th>Dataset</Th>
                  <Th>Status</Th>
                  <Th>Cases</Th>
                  <Th>Duration</Th>
                  <Th>Finished</Th>
                </tr>
              </thead>
              <tbody>
                {runs.data.items.map((item) => (
                  <Tr key={item.id}>
                    <Td>
                      <Link href={`/evaluation/runs/${item.id}`} className="font-mono text-xs font-medium text-ink hover:text-accent-strong hover:underline">
                        {item.id}
                      </Link>
                      <p className="mt-0.5 text-[10px] text-muted">{item.kind}</p>
                    </Td>
                    <Td className="text-xs text-muted">{item.dataset_name ?? item.dataset_id}</Td>
                    <Td><StatusBadge status={item.status} /></Td>
                    <Td className="text-xs tabular-nums text-ink">{item.sample_count}</Td>
                    <Td className="text-xs tabular-nums text-muted">{formatDuration(item.duration_ms)}</Td>
                    <Td className="text-xs text-muted">{formatDateTime(item.finished_at)}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          ) : null}
        </Card>

        <Card>
          <CardHeader
            title="Human feedback"
            description="Descriptive labels from reviewers. Not a model metric and never used as ground truth."
          />
          <CardBody>
            {feedback.data ? (
              <>
                <dl className="divide-y divide-line/70">
                  <KeyValue label="Non-rejected findings">{feedback.data.total_findings}</KeyValue>
                  <KeyValue label="Labelled findings">{feedback.data.labelled_findings}</KeyValue>
                  {Object.entries(feedback.data.labels).map(([label, count]) => (
                    <KeyValue key={label} label={humanizeStatus(label)}>{count}</KeyValue>
                  ))}
                </dl>
                <p className="mt-3 text-[11px] leading-5 text-muted">{feedback.data.note}</p>
              </>
            ) : feedback.error ? (
              <ErrorState error={feedback.error} onRetry={feedback.reload} />
            ) : (
              <p className="text-sm text-muted">Loading feedback aggregate…</p>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
