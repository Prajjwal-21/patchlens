"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Database, Play } from "lucide-react";

import { Badge, StatusBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { KeyValue } from "@/components/ui/data-display";
import { PageBreadcrumb, PageHeader } from "@/components/ui/page-header";
import { ErrorState, LoadingState } from "@/components/ui/states";
import { Table, Td, Th, Tr } from "@/components/ui/table";
import { formatDateTime, humanizeStatus, titleCase } from "@/lib/format";
import { useApiQuery } from "@/lib/hooks/use-api";
import type { EvalDatasetDetail } from "@/lib/types";

export default function EvalDatasetPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const dataset = useApiQuery<EvalDatasetDetail>(
    id ? `/api/v1/evaluation/datasets/${encodeURIComponent(id)}` : null,
  );

  if (dataset.loading && !dataset.data) {
    return (
      <>
        <PageHeader title="Dataset" description="Loading dataset and its cases…" />
        <LoadingState rows={3} />
      </>
    );
  }
  if (dataset.error && !dataset.data) {
    return (
      <>
        <PageBreadcrumb items={[{ label: "Evaluation", href: "/evaluation" }, { label: "Unknown" }]} />
        <ErrorState error={dataset.error} onRetry={dataset.reload} title="Dataset could not be loaded" />
      </>
    );
  }
  if (!dataset.data) return null;

  const item = dataset.data;

  return (
    <div>
      <PageBreadcrumb
        items={[
          { label: "Evaluation", href: "/evaluation" },
          { label: item.name },
        ]}
      />
      <PageHeader
        eyebrow="Evaluation dataset"
        title={item.name}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <StatusBadge status={item.status} />
            <Badge tone="neutral">{titleCase(item.kind)}</Badge>
            <span>v{item.version}</span>
            <span>·</span>
            <span>{item.case_count} cases</span>
            {item.repository ? <span className="font-mono">· {item.repository}</span> : null}
          </span>
        }
        actions={
          <>
            {item.latest_run ? (
              <LinkButton href={`/evaluation/runs/${item.latest_run.id}`} variant="secondary">
                Latest run
              </LinkButton>
            ) : null}
            <LinkButton href="/evaluation" variant="ghost">
              <ArrowLeft className="size-3.5" aria-hidden /> Evaluation
            </LinkButton>
          </>
        }
      />

      <div className="grid gap-6 xl:grid-cols-[1.5fr_1fr]">
        <div className="space-y-5">
          <Card>
            <CardHeader
              title="Cases"
              description="The exact queries and labels this dataset contributes to a run."
              action={<Badge tone="neutral">{item.cases.length} loaded</Badge>}
            />
            {item.cases.length === 0 ? (
              <CardBody>
                <p className="text-sm text-muted">This dataset has no stored cases.</p>
              </CardBody>
            ) : (
              <Table label="Evaluation cases">
                <thead>
                  <tr>
                    <Th>Case</Th>
                    <Th>Split</Th>
                    <Th>Query</Th>
                    <Th>Labels</Th>
                  </tr>
                </thead>
                <tbody>
                  {item.cases.map((entry) => (
                    <Tr key={entry.id}>
                      <Td>
                        <span className="font-mono text-xs text-ink">{entry.case_key}</span>
                      </Td>
                      <Td className="text-xs text-muted">{humanizeStatus(entry.split)}</Td>
                      <Td className="text-xs text-ink">
                        {entry.query}
                        {entry.notes ? <p className="mt-1 text-[11px] text-muted">{entry.notes}</p> : null}
                      </Td>
                      <Td>
                        <span className="text-xs text-ink">{entry.expected_count} expected</span>
                        <details className="mt-1">
                          <summary className="cursor-pointer text-[10px] text-muted hover:text-ink">Labels</summary>
                          <pre className="mt-1 max-h-48 max-w-md overflow-auto rounded border border-line bg-surface-muted px-2 py-1 font-mono text-[10px] leading-4 text-muted">
                            {JSON.stringify(entry.labels, null, 2)}
                          </pre>
                        </details>
                      </Td>
                    </Tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Card>
        </div>

        <div className="space-y-5">
          <Card>
            <CardHeader title="Provenance" description="Where the labels came from and what they do not prove." />
            <CardBody>
              <dl className="divide-y divide-line/70">
                <KeyValue label="Kind">{titleCase(item.kind)}</KeyValue>
                <KeyValue label="Version">{item.version}</KeyValue>
                <KeyValue label="Repository" mono>{item.repository ?? "—"}</KeyValue>
                <KeyValue label="Commit" mono>{item.commit_sha ?? "—"}</KeyValue>
                <KeyValue label="Content hash" mono>{item.content_hash}</KeyValue>
                <KeyValue label="Source" mono>{item.source_path ?? "—"}</KeyValue>
                <KeyValue label="Created">{formatDateTime(item.created_at)}</KeyValue>
                <KeyValue label="Splits">
                  {Object.entries(item.split_counts)
                    .map(([split, count]) => `${split}: ${count}`)
                    .join(" · ") || "—"}
                </KeyValue>
              </dl>
              {item.provenance ? (
                <div className="mt-3 rounded-lg border border-line bg-surface-muted/60 p-3">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-muted">Provenance</p>
                  <p className="mt-1 text-xs leading-5 text-ink/80">{item.provenance}</p>
                </div>
              ) : null}
              {item.license_note ? (
                <p className="mt-3 text-[11px] leading-5 text-muted">
                  <span className="font-medium text-ink">License:</span> {item.license_note}
                </p>
              ) : null}
              {item.limitations ? (
                <p className="mt-3 rounded-lg border border-warning/25 bg-warning-soft px-3 py-2 text-[11px] leading-5 text-warning-strong">
                  <span className="font-semibold">Limitations:</span> {item.limitations}
                </p>
              ) : null}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Latest run" />
            <CardBody>
              {item.latest_run ? (
                <>
                  <dl className="divide-y divide-line/70">
                    <KeyValue label="Run" mono>
                      <Link href={`/evaluation/runs/${item.latest_run.id}`} className="text-accent-strong hover:underline">
                        {item.latest_run.id}
                      </Link>
                    </KeyValue>
                    <KeyValue label="Status">
                      <StatusBadge status={item.latest_run.status} />
                    </KeyValue>
                    <KeyValue label="Samples">{item.latest_run.sample_count}</KeyValue>
                    <KeyValue label="Finished">{formatDateTime(item.latest_run.finished_at)}</KeyValue>
                  </dl>
                  {item.latest_run.error ? (
                    <p className="mt-3 break-words rounded-lg border border-danger/20 bg-danger-soft px-3 py-2 font-mono text-[11px] leading-5 text-danger-strong">
                      {item.latest_run.error}
                    </p>
                  ) : null}
                </>
              ) : (
                <p className="text-sm text-muted">NOT RUN. No metrics exist for this dataset yet.</p>
              )}
            </CardBody>
          </Card>

          <LinkButton href="/evaluation" variant="secondary" className="w-full">
            <Database className="size-4" aria-hidden /> Back to evaluation
          </LinkButton>
          <p className="text-center text-[11px] text-muted">
            Use “Run now” on the evaluation page to create a recorded run.
          </p>
          <p className="flex items-center justify-center gap-1 text-[11px] text-muted">
            <Play className="size-3" aria-hidden /> Runs are synchronous and bounded to 200 cases.
          </p>
        </div>
      </div>
    </div>
  );
}
