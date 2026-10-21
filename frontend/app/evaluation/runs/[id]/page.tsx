"use client";

import { useParams } from "next/navigation";
import { ArrowLeft, AlertTriangle, BarChart3 } from "lucide-react";

import { Badge, StatusBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { KeyValue } from "@/components/ui/data-display";
import { PageBreadcrumb, PageHeader } from "@/components/ui/page-header";
import { ErrorState, InlineNotice, LoadingState } from "@/components/ui/states";
import { StatCard } from "@/components/ui/stat-card";
import { Table, Td, Th, Tr } from "@/components/ui/table";
import {
  formatDateTime,
  formatDuration,
  formatMetric,
  formatPercent,
  humanizeStatus,
  titleCase,
} from "@/lib/format";
import { asNumber, asRecord, asString } from "@/lib/json";
import { useApiQuery } from "@/lib/hooks/use-api";
import type { EvalCaseResult, EvalRunDetail } from "@/lib/types";

function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "UNKNOWN";
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : formatMetric(value);
  }
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function RetrievalMetrics({ run }: { run: EvalRunDetail }) {
  const strategies = Object.entries(run.strategy_metrics);
  if (strategies.length === 0) {
    return <p className="text-sm text-muted">No strategy metrics were recorded.</p>;
  }
  const keys = Array.from(new Set(strategies.flatMap(([, metrics]) => Object.keys(metrics)))).filter(
    (key) => key !== "latency_ms" && key !== "cost_usd",
  );
  const preferred = ["cases", "failures", "failure_rate"];
  keys.sort((a, b) => {
    const ai = preferred.findIndex((key) => a.startsWith(key));
    const bi = preferred.findIndex((key) => b.startsWith(key));
    if (ai !== -1 || bi !== -1) return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
    return a.localeCompare(b);
  });

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[48rem] border-collapse text-left text-xs" aria-label="Retrieval strategy metrics">
        <thead>
          <tr>
            <Th>Strategy</Th>
            {keys.map((key) => (
              <Th key={key}>{key.replace(/_/g, " ")}</Th>
            ))}
            <Th>p50</Th>
            <Th>p95</Th>
            <Th>Cost</Th>
          </tr>
        </thead>
        <tbody>
          {strategies.map(([strategy, metrics]) => {
            const latency = asRecord(metrics.latency_ms);
            return (
              <Tr key={strategy}>
                <Td className="font-mono font-medium text-ink">{strategy}</Td>
                {keys.map((key) => (
                  <Td key={key} className="font-mono tabular-nums text-muted">
                    {key === "failure_rate" ? formatPercent(metrics[key]) : displayValue(metrics[key])}
                  </Td>
                ))}
                <Td className="font-mono tabular-nums text-muted">{formatDuration(asNumber(latency?.p50))}</Td>
                <Td className="font-mono tabular-nums text-muted">{formatDuration(asNumber(latency?.p95))}</Td>
                <Td className="text-[11px] text-muted">
                  {metrics.cost_usd === null || metrics.cost_usd === undefined ? "UNKNOWN" : displayValue(metrics.cost_usd)}
                </Td>
              </Tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ReviewMetrics({ run }: { run: EvalRunDetail }) {
  const overall = asRecord(run.metrics.overall);
  const mustFind = asRecord(run.metrics.must_find);
  const categories = asRecord(run.metrics.categories);
  const latency = asRecord(run.metrics.latency_ms);
  const detected = Array.isArray(run.metrics.detected) ? run.metrics.detected : [];
  const missed = Array.isArray(run.metrics.missed_expected) ? run.metrics.missed_expected : [];

  return (
    <div className="space-y-4">
      <dl className="grid gap-3 sm:grid-cols-4">
        <div className="rounded-lg border border-line bg-surface-muted/40 p-3">
          <dt className="text-[11px] text-muted">Precision</dt>
          <dd className="mt-1 font-mono text-sm text-ink">{formatMetric(overall?.precision)}</dd>
        </div>
        <div className="rounded-lg border border-line bg-surface-muted/40 p-3">
          <dt className="text-[11px] text-muted">Recall</dt>
          <dd className="mt-1 font-mono text-sm text-ink">{formatMetric(overall?.recall)}</dd>
        </div>
        <div className="rounded-lg border border-line bg-surface-muted/40 p-3">
          <dt className="text-[11px] text-muted">F1</dt>
          <dd className="mt-1 font-mono text-sm text-ink">{formatMetric(overall?.f1)}</dd>
        </div>
        <div className="rounded-lg border border-line bg-surface-muted/40 p-3">
          <dt className="text-[11px] text-muted">Must-find recall</dt>
          <dd className="mt-1 font-mono text-sm text-ink">{formatMetric(mustFind?.recall)}</dd>
        </div>
      </dl>
      <dl className="divide-y divide-line/70">
        <KeyValue label="True / false positive">{`${displayValue(overall?.tp)} / ${displayValue(overall?.fp)}`}</KeyValue>
        <KeyValue label="False negatives">{displayValue(overall?.fn)}</KeyValue>
        <KeyValue label="Rule match rate">{formatMetric(run.metrics.rule_match_rate)}</KeyValue>
        <KeyValue label="Rejected candidates">{displayValue(run.metrics.rejected_total)}</KeyValue>
        <KeyValue label="Must-find missed">{displayValue(mustFind?.missed)} of {displayValue(mustFind?.total)}</KeyValue>
        <KeyValue label="Latency p50 / p95">
          {formatDuration(asNumber(latency?.p50))} / {formatDuration(asNumber(latency?.p95))}
        </KeyValue>
        <KeyValue label="Provider">{run.metrics.provider_is_mock === true ? "Mock (deterministic)" : "Configured provider"}</KeyValue>
      </dl>

      {categories ? (
        <div>
          <h3 className="mb-2 text-xs font-semibold text-ink">Per category</h3>
          <Table label="Review metrics by category">
            <thead>
              <tr>
                <Th>Category</Th>
                <Th>TP</Th>
                <Th>FP</Th>
                <Th>FN</Th>
                <Th>Precision</Th>
                <Th>Recall</Th>
                <Th>F1</Th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(categories).map(([category, raw]) => {
                const metrics = asRecord(raw);
                return (
                  <Tr key={category}>
                    <Td className="text-xs font-medium text-ink">{titleCase(category)}</Td>
                    <Td className="font-mono text-xs text-muted">{displayValue(metrics?.tp)}</Td>
                    <Td className="font-mono text-xs text-muted">{displayValue(metrics?.fp)}</Td>
                    <Td className="font-mono text-xs text-muted">{displayValue(metrics?.fn)}</Td>
                    <Td className="font-mono text-xs text-muted">{formatMetric(metrics?.precision)}</Td>
                    <Td className="font-mono text-xs text-muted">{formatMetric(metrics?.recall)}</Td>
                    <Td className="font-mono text-xs text-muted">{formatMetric(metrics?.f1)}</Td>
                  </Tr>
                );
              })}
            </tbody>
          </Table>
        </div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border border-line bg-surface-muted/40 p-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-muted">Rules detected</p>
          <p className="mt-1 font-mono text-[11px] leading-5 text-ink">
            {detected.length > 0 ? detected.map((item) => String(item)).join(", ") : "None"}
          </p>
        </div>
        <div className="rounded-lg border border-line bg-surface-muted/40 p-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-muted">Expected but missed</p>
          <p className="mt-1 font-mono text-[11px] leading-5 text-ink">
            {missed.length > 0 ? missed.map((item) => String(item)).join(", ") : "None"}
          </p>
        </div>
      </div>
    </div>
  );
}

function TestGenerationMetrics({ run }: { run: EvalRunDetail }) {
  const mutations = asRecord(run.metrics.mutations);
  const latency = asRecord(run.metrics.latency_ms);
  const executionLatency = asRecord(run.metrics.execution_latency_ms);
  const caseMetrics = Array.isArray(run.metrics.case_metrics) ? run.metrics.case_metrics : [];
  return (
    <div className="space-y-4">
      <dl className="grid gap-3 sm:grid-cols-4">
        <div className="rounded-lg border border-line bg-surface-muted/40 p-3">
          <dt className="text-[11px] text-muted">Validity rate</dt>
          <dd className="mt-1 font-mono text-sm text-ink">{formatPercent(run.metrics.validity_rate)}</dd>
        </div>
        <div className="rounded-lg border border-line bg-surface-muted/40 p-3">
          <dt className="text-[11px] text-muted">Execution rate</dt>
          <dd className="mt-1 font-mono text-sm text-ink">{formatPercent(run.metrics.execution_rate)}</dd>
        </div>
        <div className="rounded-lg border border-line bg-surface-muted/40 p-3">
          <dt className="text-[11px] text-muted">Pass on original</dt>
          <dd className="mt-1 font-mono text-sm text-ink">{formatPercent(run.metrics.pass_rate_on_original)}</dd>
        </div>
        <div className="rounded-lg border border-line bg-surface-muted/40 p-3">
          <dt className="text-[11px] text-muted">Regression detection</dt>
          <dd className="mt-1 font-mono text-sm text-ink">{formatPercent(run.metrics.regression_detection_rate)}</dd>
        </div>
      </dl>
      <dl className="divide-y divide-line/70">
        <KeyValue label="Attempted / valid">{`${displayValue(run.metrics.attempted)} / ${displayValue(run.metrics.valid)}`}</KeyValue>
        <KeyValue label="Rejected by validation">{displayValue(run.metrics.rejected)}</KeyValue>
        <KeyValue label="Execution failures">{displayValue(run.metrics.failure_rate)}</KeyValue>
        <KeyValue label="Mutations evaluated">
          {`${displayValue(mutations?.evaluated)} of ${displayValue(mutations?.declared)} declared`}
        </KeyValue>
        <KeyValue label="Mutations detected">{displayValue(mutations?.detected)}</KeyValue>
        <KeyValue label="Generation latency p50 / p95">
          {formatDuration(asNumber(latency?.p50))} / {formatDuration(asNumber(latency?.p95))}
        </KeyValue>
        <KeyValue label="Execution latency p50 / p95">
          {formatDuration(asNumber(executionLatency?.p50))} / {formatDuration(asNumber(executionLatency?.p95))}
        </KeyValue>
        <KeyValue label="Cost">{run.metrics.cost_usd === null ? "UNKNOWN" : displayValue(run.metrics.cost_usd)}</KeyValue>
      </dl>
      {caseMetrics.length > 0 ? (
        <div>
          <h3 className="mb-2 text-xs font-semibold text-ink">Per case</h3>
          <Table label="Test generation metrics per case">
            <thead>
              <tr>
                <Th>Case</Th>
                <Th>Valid</Th>
                <Th>Executed</Th>
                <Th>Passed</Th>
                <Th>Mutations</Th>
                <Th>Error</Th>
              </tr>
            </thead>
            <tbody>
              {caseMetrics.map((raw, index) => {
                const item = asRecord(raw);
                return (
                  <Tr key={`${asString(item?.case) ?? "case"}-${index}`}>
                    <Td className="font-mono text-[11px] text-ink">{displayValue(item?.case)}</Td>
                    <Td className="text-xs text-muted">{displayValue(item?.valid)}</Td>
                    <Td className="text-xs text-muted">{displayValue(item?.executed)}</Td>
                    <Td className="text-xs text-muted">{displayValue(item?.passed_on_original)}</Td>
                    <Td className="text-xs text-muted">
                      {displayValue(item?.mutations_detected)} / {displayValue(item?.mutations_evaluated)}
                    </Td>
                    <Td className="text-xs text-danger-strong">{asString(item?.error) ?? "—"}</Td>
                  </Tr>
                );
              })}
            </tbody>
          </Table>
        </div>
      ) : null}
    </div>
  );
}

function CaseResults({ results }: { results: EvalCaseResult[] }) {
  if (results.length === 0) {
    return <p className="text-sm text-muted">No per-case results were recorded.</p>;
  }
  return (
    <Table label="Per-case evaluation results">
      <thead>
        <tr>
          <Th>Case</Th>
          <Th>Split</Th>
          <Th>Strategy</Th>
          <Th>Metrics</Th>
          <Th>Latency</Th>
          <Th>Error</Th>
        </tr>
      </thead>
      <tbody>
        {results.map((result) => (
          <Tr key={result.id}>
            <Td className="font-mono text-[11px] text-ink">{result.case_key}</Td>
            <Td className="text-xs text-muted">{humanizeStatus(result.split)}</Td>
            <Td className="font-mono text-[11px] text-muted">{result.strategy}</Td>
            <Td>
              <details>
                <summary className="cursor-pointer text-[11px] text-accent-strong">View</summary>
                <pre className="mt-1 max-h-64 max-w-md overflow-auto rounded border border-line bg-surface-muted px-2 py-1 font-mono text-[10px] leading-4 text-muted">
                  {JSON.stringify(result.metrics, null, 2)}
                </pre>
                {result.predictions.length > 0 ? (
                  <pre className="mt-1 max-h-64 max-w-md overflow-auto rounded border border-line bg-surface-muted px-2 py-1 font-mono text-[10px] leading-4 text-muted">
                    {JSON.stringify(result.predictions, null, 2)}
                  </pre>
                ) : null}
              </details>
            </Td>
            <Td className="text-xs tabular-nums text-muted">{formatDuration(result.latency_ms)}</Td>
            <Td className="max-w-[14rem] break-words text-[11px] text-danger-strong">{result.error ?? "—"}</Td>
          </Tr>
        ))}
      </tbody>
    </Table>
  );
}

export default function EvalRunPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const run = useApiQuery<EvalRunDetail>(id ? `/api/v1/evaluation/runs/${encodeURIComponent(id)}` : null);

  if (run.loading && !run.data) {
    return (
      <>
        <PageHeader title="Evaluation run" description="Loading recorded metrics and per-case results…" />
        <LoadingState rows={3} />
      </>
    );
  }
  if (run.error && !run.data) {
    return (
      <>
        <PageBreadcrumb items={[{ label: "Evaluation", href: "/evaluation" }, { label: "Unknown" }]} />
        <ErrorState error={run.error} onRetry={run.reload} title="Run could not be loaded" />
      </>
    );
  }
  if (!run.data) return null;

  const item = run.data;
  const unknowns = Array.isArray(item.metrics.unknowns) ? item.metrics.unknowns : [];
  const configEntries = Object.entries(item.config);

  return (
    <div>
      <PageBreadcrumb
        items={[
          { label: "Evaluation", href: "/evaluation" },
          { label: item.dataset_name ?? item.dataset_id, href: `/evaluation/datasets/${item.dataset_id}` },
          { label: item.id },
        ]}
      />
      <PageHeader
        eyebrow="Evaluation run"
        title={item.dataset_name ?? item.dataset_id}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <StatusBadge status={item.status} />
            <Badge tone="neutral">{titleCase(item.kind)}</Badge>
            <span>{item.sample_count} samples</span>
            <span>·</span>
            <span className="font-mono">{item.id}</span>
          </span>
        }
        actions={
          <>
            <LinkButton href={`/evaluation/datasets/${item.dataset_id}`} variant="secondary">
              Dataset
            </LinkButton>
            <LinkButton href="/evaluation" variant="ghost">
              <ArrowLeft className="size-3.5" aria-hidden /> Evaluation
            </LinkButton>
          </>
        }
      />

      <div className="mb-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Status" value={humanizeStatus(item.status)} detail={item.error ? "See error below" : "Recorded synchronously"} tone={item.status === "succeeded" ? "success" : "danger"} />
        <StatCard label="Samples" value={item.sample_count} detail={`${item.results.length} per-case results`} icon={BarChart3} />
        <StatCard label="Duration" value={formatDuration(item.duration_ms)} detail={formatDateTime(item.started_at)} />
        <StatCard label="Finished" value={formatDateTime(item.finished_at)} detail={formatDateTime(item.finished_at)} />
      </div>

      {item.error ? (
        <InlineNotice tone="danger" title="This run failed" className="mb-5">
          <p className="break-words font-mono text-xs">{item.error}</p>
        </InlineNotice>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[1.6fr_1fr]">
        <div className="space-y-5">
          <Card>
            <CardHeader
              title="Metrics"
              description="Rendered from the recorded run. A null metric is UNKNOWN, not zero."
            />
            <CardBody>
              {item.status !== "succeeded" ? (
                <p className="text-sm text-muted">
                  Metrics are not shown for a run that did not succeed. The raw record and any per-case
                  results are below.
                </p>
              ) : item.kind === "retrieval" ? (
                <RetrievalMetrics run={item} />
              ) : item.kind === "review" ? (
                <ReviewMetrics run={item} />
              ) : item.kind === "test_generation" ? (
                <TestGenerationMetrics run={item} />
              ) : (
                <pre className="overflow-x-auto rounded-lg border border-line bg-surface-muted px-3 py-2 font-mono text-[11px] leading-5 text-muted">
                  {JSON.stringify(item.metrics, null, 2)}
                </pre>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Per-case results"
              description="The evidence behind the aggregate; every case is retained, including failures."
            />
            <CaseResults results={item.results} />
          </Card>
        </div>

        <div className="space-y-5">
          <Card>
            <CardHeader title="Run record" />
            <CardBody>
              <dl className="divide-y divide-line/70">
                <KeyValue label="Run id" mono>{item.id}</KeyValue>
                <KeyValue label="Dataset" mono>{item.dataset_name ?? item.dataset_id}</KeyValue>
                <KeyValue label="Kind">{titleCase(item.kind)}</KeyValue>
                <KeyValue label="Started">{formatDateTime(item.started_at)}</KeyValue>
                <KeyValue label="Finished">{formatDateTime(item.finished_at)}</KeyValue>
                <KeyValue label="Duration">{formatDuration(item.duration_ms)}</KeyValue>
                <KeyValue label="Notes">{item.notes ?? "—"}</KeyValue>
              </dl>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Configuration" description="Exactly what was run." />
            <CardBody>
              {configEntries.length === 0 ? (
                <p className="text-sm text-muted">No configuration was recorded.</p>
              ) : (
                <pre className="max-h-80 overflow-auto rounded-lg border border-line bg-surface-muted px-3 py-2 font-mono text-[11px] leading-5 text-muted">
                  {JSON.stringify(item.config, null, 2)}
                </pre>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Limitations and unknowns" />
            <CardBody className="space-y-3">
              {item.limitations.length === 0 && unknowns.length === 0 ? (
                <p className="text-sm text-muted">No limitations were recorded for this run.</p>
              ) : null}
              {item.limitations.length > 0 ? (
                <ul className="space-y-1.5 text-xs leading-5 text-muted">
                  {item.limitations.map((limitation) => (
                    <li key={limitation} className="flex gap-2">
                      <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
                      {limitation}
                    </li>
                  ))}
                </ul>
              ) : null}
              {unknowns.length > 0 ? (
                <div className="rounded-lg border border-warning/25 bg-warning-soft px-3 py-2 text-xs leading-5 text-warning-strong">
                  <p className="font-semibold">Explicit unknowns</p>
                  <ul className="mt-1 list-disc space-y-1 pl-4">
                    {unknowns.map((unknown) => (
                      <li key={String(unknown)}>{String(unknown)}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Raw metrics" description="The stored object, for auditing a rendering choice." />
            <CardBody>
              <pre className="max-h-80 overflow-auto rounded-lg border border-line bg-surface-muted px-3 py-2 font-mono text-[10px] leading-4 text-muted">
                {JSON.stringify(item.metrics, null, 2)}
              </pre>
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  );
}
