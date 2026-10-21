"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Ban, RefreshCcw } from "lucide-react";

import { Badge, StatusBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { KeyValue, ProgressBar } from "@/components/ui/data-display";
import { PageBreadcrumb, PageHeader } from "@/components/ui/page-header";
import { EmptyState, ErrorState, InlineNotice, LoadingState } from "@/components/ui/states";
import { StatCard } from "@/components/ui/stat-card";
import { formatApiError, postJson } from "@/lib/api";
import {
  formatDateTime,
  formatDuration,
  formatRelative,
  humanizeStatus,
} from "@/lib/format";
import { asString, asRecord } from "@/lib/json";
import { useApiQuery } from "@/lib/hooks/use-api";
import type { Job, OkResponse } from "@/lib/types";

export default function JobDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const job = useApiQuery<Job>(id ? `/api/v1/jobs/${encodeURIComponent(id)}` : null, {
    refreshIntervalMs: 4_000,
  });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const cancel = async () => {
    if (!id) return;
    setPending(true);
    setError(null);
    setNote(null);
    try {
      const response = await postJson<OkResponse>(`/api/v1/jobs/${encodeURIComponent(id)}/cancel`);
      setNote(response.detail ?? "Job cancelled.");
      job.reload();
    } catch (cause) {
      setError(formatApiError(cause));
      job.reload();
    } finally {
      setPending(false);
    }
  };

  if (job.loading && !job.data) {
    return (
      <>
        <PageHeader title="Job" description="Loading job state and events…" />
        <LoadingState rows={3} />
      </>
    );
  }
  if (job.error && !job.data) {
    return (
      <>
        <PageBreadcrumb items={[{ label: "Jobs", href: "/jobs" }, { label: "Unknown" }]} />
        <ErrorState error={job.error} onRetry={job.reload} title="Job could not be loaded" />
      </>
    );
  }
  if (!job.data) return null;

  const item = job.data;
  const reviewId = asString(asRecord(item.result)?.review_id);
  const active = item.status === "queued" || item.status === "running";
  const cancellable = item.status === "queued";
  const events = item.events ?? [];

  return (
    <div>
      <PageBreadcrumb items={[{ label: "Jobs", href: "/jobs" }, { label: item.id }]} />
      <PageHeader
        eyebrow="Job"
        title={humanizeStatus(item.kind)}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <StatusBadge status={item.status} />
            <span className="font-mono text-xs">{item.id}</span>
            <span>·</span>
            <span>created {formatRelative(item.created_at)}</span>
          </span>
        }
        actions={
          <>
            {reviewId ? <LinkButton href={`/reviews/${reviewId}`} variant="secondary">Open review</LinkButton> : null}
            <Button variant="ghost" onClick={job.reload}>
              <RefreshCcw className="size-3.5" aria-hidden /> Refresh
            </Button>
          </>
        }
      />

      {item.status === "queued" ? (
        <InlineNotice tone="info" title="No worker has claimed this job" className="mb-5">
          Start one with <code className="font-mono">make worker</code>. This page refreshes every four
          seconds while you leave it open.
        </InlineNotice>
      ) : null}

      {error ? (
        <InlineNotice tone="danger" title="Could not cancel" className="mb-5">
          {error}
        </InlineNotice>
      ) : null}
      {note ? (
        <InlineNotice tone="success" title="Cancelled" className="mb-5">
          {note}
        </InlineNotice>
      ) : null}

      {item.error ? (
        <InlineNotice tone="danger" title={item.error_type ? `Job failed · ${item.error_type}` : "Job failed"} className="mb-5">
          <p className="break-words font-mono text-xs">{item.error}</p>
        </InlineNotice>
      ) : null}

      <Card className="mb-5">
        <CardBody>
          <ProgressBar
            value={item.progress}
            label={`${humanizeStatus(item.stage ?? item.status)} · ${item.status}`}
            tone={item.status === "failed" || item.status === "dead" ? "danger" : item.status === "succeeded" ? "success" : "accent"}
          />
          <p className="mt-2 text-xs text-muted">
            {active
              ? "Progress is written by the worker as stages complete. A stalled running job is recovered by lease expiry."
              : "This job is terminal; its status and result are final unless it is explicitly re-queued by a new request."}
          </p>
        </CardBody>
      </Card>

      <div className="mb-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Status" value={humanizeStatus(item.status)} detail={item.stage ? humanizeStatus(item.stage) : "No stage"} tone={item.status === "failed" || item.status === "dead" ? "danger" : item.status === "succeeded" ? "success" : "accent"} />
        <StatCard label="Attempts" value={`${item.attempts} / ${item.max_attempts}`} detail={`priority ${item.priority}`} />
        <StatCard label="Duration" value={formatDuration(item.duration_ms)} detail={item.worker_id ? `worker ${item.worker_id}` : "No worker recorded"} />
        <StatCard label="Created" value={formatDateTime(item.created_at)} detail={formatDateTime(item.created_at)} />
      </div>

      <div className="grid gap-6 xl:grid-cols-[1.5fr_1fr]">
        <div className="space-y-5">
          <Card>
            <CardHeader
              title="Events"
              description="Append-only audit trail written by the queue and handler."
              action={<Badge tone="neutral">{events.length} events</Badge>}
            />
            {events.length === 0 ? (
              <EmptyState title="No events" description="This job has no recorded events yet." />
            ) : (
              <CardBody>
                <ol className="space-y-3">
                  {events.map((event, index) => (
                    <li key={`${event.created_at ?? "unknown"}-${index}`} className="flex gap-3">
                      <span
                        className={`mt-1.5 size-1.5 shrink-0 rounded-full ${
                          event.level === "error" ? "bg-danger" : event.level === "warning" ? "bg-warning" : "bg-accent"
                        }`}
                        aria-hidden
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="text-xs font-medium text-ink">{event.message}</p>
                          {event.stage ? <Badge tone="neutral">{humanizeStatus(event.stage)}</Badge> : null}
                          <span className="ml-auto text-[10px] text-muted">{formatDateTime(event.created_at)}</span>
                        </div>
                        {Object.keys(event.data ?? {}).length > 0 ? (
                          <pre className="mt-1 overflow-x-auto rounded border border-line bg-surface-muted px-2 py-1 font-mono text-[10px] leading-4 text-muted">
                            {JSON.stringify(event.data, null, 2)}
                          </pre>
                        ) : null}
                      </div>
                    </li>
                  ))}
                </ol>
              </CardBody>
            )}
          </Card>

          <Card>
            <CardHeader title="Result" description="The payload recorded by the handler, verbatim." />
            <CardBody>
              {Object.keys(item.result ?? {}).length === 0 ? (
                <p className="text-sm text-muted">No result has been recorded.</p>
              ) : (
                <pre className="max-h-96 overflow-auto rounded-lg border border-line bg-surface-muted px-3 py-2 font-mono text-[11px] leading-5 text-muted">
                  {JSON.stringify(item.result, null, 2)}
                </pre>
              )}
            </CardBody>
          </Card>
        </div>

        <div className="space-y-5">
          <Card>
            <CardHeader title="Job record" />
            <CardBody>
              <dl className="divide-y divide-line/70">
                <KeyValue label="Job id" mono>{item.id}</KeyValue>
                <KeyValue label="Kind">{humanizeStatus(item.kind)}</KeyValue>
                <KeyValue label="Status">{humanizeStatus(item.status)}</KeyValue>
                <KeyValue label="Stage">{item.stage ? humanizeStatus(item.stage) : "—"}</KeyValue>
                <KeyValue label="Attempts">{item.attempts} of {item.max_attempts}</KeyValue>
                <KeyValue label="Priority">{item.priority}</KeyValue>
                <KeyValue label="Worker" mono>{item.worker_id ?? "—"}</KeyValue>
                <KeyValue label="Dedupe key" mono>{item.dedupe_key ?? "—"}</KeyValue>
                <KeyValue label="Run after">{formatDateTime(item.run_after)}</KeyValue>
                <KeyValue label="Started">{formatDateTime(item.started_at)}</KeyValue>
                <KeyValue label="Finished">{formatDateTime(item.finished_at)}</KeyValue>
                <KeyValue label="Duration">{formatDuration(item.duration_ms)}</KeyValue>
              </dl>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Actions" description="Cancellation is only possible before a worker claims the job." />
            <CardBody>
              <Button variant="danger" disabled={!cancellable || pending} loading={pending} onClick={cancel}>
                <Ban className="size-4" aria-hidden />
                {cancellable ? "Cancel job" : "Cancellation unavailable"}
              </Button>
              <p className="mt-2 text-[11px] leading-5 text-muted">
                Running jobs are not force-killed by the API; the worker must observe a cooperative stop.
                A terminal job cannot be cancelled again.
              </p>
            </CardBody>
          </Card>

          <Link href="/jobs" className="inline-flex items-center gap-1 text-xs font-medium text-accent-strong hover:underline">
            <ArrowLeft className="size-3.5" aria-hidden /> Back to jobs
          </Link>
        </div>
      </div>
    </div>
  );
}
