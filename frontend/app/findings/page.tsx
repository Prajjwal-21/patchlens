"use client";

import { Suspense, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ScanSearch } from "lucide-react";

import { FindingCard } from "@/components/finding-card";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/states";
import { buildQuery } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { useApiQuery } from "@/lib/hooks/use-api";
import type { FindingListResponse, Repository } from "@/lib/types";

const SEVERITIES = ["", "critical", "high", "medium", "low", "info"];
const CATEGORIES = [
  "",
  "correctness",
  "security",
  "reliability",
  "performance",
  "maintainability",
  "testing",
  "style",
];
const VERIFICATION = ["", "verified", "needs_manual_verification", "ai_suggested", "rejected"];
const PAGE_SIZE = 50;

const selectClasses =
  "mt-1 h-9 rounded-lg border border-line bg-surface px-2.5 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40";
const inputClasses =
  "mt-1 h-9 w-full rounded-lg border border-line bg-surface px-2.5 text-sm text-ink placeholder:text-muted/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40";

function FindingsView() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const reviewId = searchParams.get("review_id") ?? "";

  const [repositoryId, setRepositoryId] = useState("");
  const [severity, setSeverity] = useState("");
  const [category, setCategory] = useState("");
  const [verification, setVerification] = useState("");
  const [path, setPath] = useState("");
  const [ruleId, setRuleId] = useState("");
  const [includeRejected, setIncludeRejected] = useState(false);
  const [offset, setOffset] = useState(0);

  const repositories = useApiQuery<Repository[]>("/api/v1/repositories?limit=200");

  const query = useMemo(
    () =>
      buildQuery({
        repository_id: repositoryId,
        review_id: reviewId,
        severity,
        category,
        verification_status: verification,
        path: path.trim(),
        rule_id: ruleId.trim(),
        include_rejected: includeRejected,
        limit: PAGE_SIZE,
        offset,
      }),
    [repositoryId, reviewId, severity, category, verification, path, ruleId, includeRejected, offset],
  );

  const findings = useApiQuery<FindingListResponse>(`/api/v1/findings${query}`);
  const items = findings.data?.items ?? [];
  const pagination = findings.data?.pagination;
  const activeFilters = [repositoryId, reviewId, severity, category, verification, path, ruleId].filter(Boolean).length;

  const resetPage = () => setOffset(0);
  const clearFilters = () => {
    setRepositoryId("");
    setSeverity("");
    setCategory("");
    setVerification("");
    setPath("");
    setRuleId("");
    setIncludeRejected(false);
    setOffset(0);
    if (reviewId) router.replace("/findings");
  };

  return (
    <div>
      <PageHeader
        eyebrow="Workspace"
        title="Findings"
        description="Every row records how it was established. Verified means a deterministic check matched an added line; AI suggested means the location was validated, not the claim."
      />

      <Card className="mb-5">
        <CardBody>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="block text-xs font-medium text-ink">
              Repository
              <select
                className={`${selectClasses} w-full`}
                value={repositoryId}
                onChange={(event) => {
                  setRepositoryId(event.target.value);
                  resetPage();
                }}
              >
                <option value="">All repositories</option>
                {(repositories.data ?? []).map((repository) => (
                  <option key={repository.id} value={repository.id}>
                    {repository.full_name}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-xs font-medium text-ink">
              Severity
              <select
                className={`${selectClasses} w-full`}
                value={severity}
                onChange={(event) => {
                  setSeverity(event.target.value);
                  resetPage();
                }}
              >
                {SEVERITIES.map((value) => (
                  <option key={value || "all"} value={value}>
                    {value ? value[0]?.toUpperCase() + value.slice(1) : "All severities"}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-xs font-medium text-ink">
              Category
              <select
                className={`${selectClasses} w-full`}
                value={category}
                onChange={(event) => {
                  setCategory(event.target.value);
                  resetPage();
                }}
              >
                {CATEGORIES.map((value) => (
                  <option key={value || "all"} value={value}>
                    {value ? value[0]?.toUpperCase() + value.slice(1) : "All categories"}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-xs font-medium text-ink">
              Verification
              <select
                className={`${selectClasses} w-full`}
                value={verification}
                onChange={(event) => {
                  setVerification(event.target.value);
                  resetPage();
                }}
              >
                {VERIFICATION.map((value) => (
                  <option key={value || "all"} value={value}>
                    {value === "needs_manual_verification"
                      ? "Needs manual verification"
                      : value === "ai_suggested"
                        ? "AI suggested"
                        : value
                          ? value[0]?.toUpperCase() + value.slice(1)
                          : "All statuses"}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-xs font-medium text-ink">
              Path
              <input
                className={inputClasses}
                value={path}
                maxLength={1_024}
                onChange={(event) => {
                  setPath(event.target.value);
                  resetPage();
                }}
                placeholder="exact relative path"
              />
            </label>
            <label className="block text-xs font-medium text-ink">
              Rule id
              <input
                className={inputClasses}
                value={ruleId}
                maxLength={255}
                onChange={(event) => {
                  setRuleId(event.target.value);
                  resetPage();
                }}
                placeholder="e.g. bare-except"
              />
            </label>
            <label className="flex items-end gap-2 pb-2 text-xs text-muted">
              <input
                type="checkbox"
                checked={includeRejected}
                onChange={(event) => {
                  setIncludeRejected(event.target.checked);
                  resetPage();
                }}
                className="size-3.5 rounded border-line text-accent focus-visible:ring-accent/40"
              />
              Include rejected candidates
            </label>
            <div className="flex items-end justify-between gap-2 pb-1">
              <span className="text-xs text-muted">
                {pagination ? `${formatCount(pagination.total)} matching` : "Loading…"}
                {activeFilters > 0 ? ` · ${activeFilters} filter${activeFilters === 1 ? "" : "s"}` : ""}
              </span>
              <Button variant="ghost" size="sm" onClick={clearFilters}>
                Clear filters
              </Button>
            </div>
          </div>
          {reviewId ? (
            <p className="mt-3 rounded-lg border border-accent/20 bg-accent-soft px-3 py-2 text-xs text-accent-strong">
              Showing findings for one review.{" "}
              <button type="button" className="font-semibold underline" onClick={() => router.replace("/findings")}>
                Remove review filter
              </button>
            </p>
          ) : null}
        </CardBody>
      </Card>

      {findings.loading && !findings.data ? <LoadingState rows={3} /> : null}
      {findings.error && !findings.data ? <ErrorState error={findings.error} onRetry={findings.reload} /> : null}
      {findings.error && findings.data ? (
        <p className="mb-3 text-xs text-danger-strong" role="alert">
          Refresh failed: {findings.error.message}. Showing the last loaded page.
        </p>
      ) : null}

      {findings.data ? (
        items.length === 0 ? (
          <Card>
            <EmptyState
              title="No findings match these filters"
              description="A clean review or a narrower filter can both produce an empty list. Rejected model candidates are hidden unless you include them."
              icon={ScanSearch}
              action={
                activeFilters > 0 ? (
                  <Button variant="secondary" onClick={clearFilters}>
                    Clear filters
                  </Button>
                ) : undefined
              }
            />
          </Card>
        ) : (
          <>
            <div className="space-y-4">
              {items.map((finding) => (
                <FindingCard key={finding.id} finding={finding} onFeedback={findings.reload} />
              ))}
            </div>
            <div className="mt-5 flex flex-wrap items-center justify-between gap-3 text-xs text-muted">
              <span>
                Showing {formatCount(offset + 1)}–{formatCount(offset + items.length)} of{" "}
                {formatCount(pagination?.total ?? items.length)}
              </span>
              <div className="flex items-center gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                >
                  Previous
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={!pagination?.has_more}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                >
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

export default function FindingsPage() {
  return (
    <Suspense fallback={<LoadingState rows={3} />}>
      <FindingsView />
    </Suspense>
  );
}
