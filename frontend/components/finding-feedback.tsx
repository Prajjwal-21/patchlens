"use client";

import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { postJson, formatApiError } from "@/lib/api";
import { humanizeStatus } from "@/lib/format";
import type { FindingFeedback } from "@/lib/types";

const LABELS = ["correct", "incorrect", "irrelevant", "duplicate", "needs_investigation"] as const;

export function FeedbackForm({
  findingId,
  onSubmitted,
  compact = false,
}: {
  findingId: string;
  onSubmitted?: () => void;
  compact?: boolean;
}) {
  const fieldId = useId();
  const [label, setLabel] = useState<(typeof LABELS)[number]>("correct");
  const [comment, setComment] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const form = (
    <form
      className="space-y-2.5"
      onSubmit={async (event) => {
        event.preventDefault();
        setPending(true);
        setError(null);
        setSuccess(false);
        try {
          await postJson<FindingFeedback>(`/api/v1/findings/${encodeURIComponent(findingId)}/feedback`, {
            label,
            comment: comment.trim() || null,
            reviewer: "dashboard",
            source: "human_review",
          });
          setComment("");
          setSuccess(true);
          onSubmitted?.();
        } catch (cause) {
          setError(formatApiError(cause));
        } finally {
          setPending(false);
        }
      }}
    >
      <div className={compact ? "grid gap-2 sm:grid-cols-[10rem_minmax(0,1fr)_auto]" : "grid gap-2"}>
        <label htmlFor={`${fieldId}-label`} className="sr-only">
          Feedback label
        </label>
        <select
          id={`${fieldId}-label`}
          value={label}
          onChange={(event) => setLabel(event.target.value as (typeof LABELS)[number])}
          className="h-9 rounded-lg border border-line bg-surface px-2.5 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        >
          {LABELS.map((value) => (
            <option key={value} value={value}>
              {humanizeStatus(value)}
            </option>
          ))}
        </select>
        <label htmlFor={`${fieldId}-comment`} className="sr-only">
          Comment (optional)
        </label>
        <input
          id={`${fieldId}-comment`}
          value={comment}
          maxLength={2_000}
          onChange={(event) => setComment(event.target.value)}
          placeholder="Optional note (stored with the label)"
          className="h-9 min-w-0 rounded-lg border border-line bg-surface px-2.5 text-sm text-ink placeholder:text-muted/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        />
        <Button type="submit" size="sm" variant="secondary" loading={pending} className="h-9">
          Record
        </Button>
      </div>
      {error ? (
        <p className="text-xs text-danger-strong" role="alert">
          {error}
        </p>
      ) : null}
      {success ? (
        <p className="text-xs text-success-strong" role="status">
          Feedback recorded. Labels are stored for evaluation; they do not retrain a model.
        </p>
      ) : null}
    </form>
  );

  if (!compact) return form;

  return (
    <details className="mt-2">
      <summary className="cursor-pointer select-none text-xs font-medium text-muted hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40">
        Add feedback
      </summary>
      <div className="mt-2">{form}</div>
    </details>
  );
}

export function FeedbackSummary({ feedback }: { feedback: FindingFeedback[] }) {
  if (feedback.length === 0) {
    return <span className="text-xs text-muted">No feedback yet</span>;
  }
  const newest = feedback[0];
  if (!newest) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5 text-xs text-muted">
      <span className="font-medium text-ink">{humanizeStatus(newest.label)}</span>
      {newest.comment ? <span className="truncate">· {newest.comment}</span> : null}
      {feedback.length > 1 ? <span>· {feedback.length} labels</span> : null}
    </span>
  );
}
