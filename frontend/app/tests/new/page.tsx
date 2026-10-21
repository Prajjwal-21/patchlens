"use client";

import { useDeferredValue, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FlaskConical, Plus, Search, Trash2 } from "lucide-react";

import { Badge, ProviderBadge, StatusBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { PageBreadcrumb, PageHeader } from "@/components/ui/page-header";
import { EmptyState, InlineNotice, LoadingState } from "@/components/ui/states";
import { buildQuery, formatApiError, postJson } from "@/lib/api";
import { formatCount, titleCase } from "@/lib/format";
import { useApiQuery } from "@/lib/hooks/use-api";
import type {
  CodeSymbol,
  CodeSymbolListResponse,
  Repository,
  SettingsResponse,
  TestCandidate,
  TestGenerationResponse,
} from "@/lib/types";

interface Target {
  path: string;
  qualified_name: string;
}

const MAX_TARGETS = 10;
const inputClasses =
  "mt-1 h-10 w-full rounded-lg border border-line bg-surface px-3 text-sm text-ink placeholder:text-muted/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40";

function targetKey(target: Target): string {
  return `${target.path}::${target.qualified_name}`;
}

export default function NewTestGenerationPage() {
  const router = useRouter();
  const repositories = useApiQuery<Repository[]>("/api/v1/repositories?limit=200");
  const runtime = useApiQuery<SettingsResponse>("/api/v1/settings");
  const [repositoryId, setRepositoryId] = useState("");
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);
  const [targets, setTargets] = useState<Target[]>([]);
  const [manualPath, setManualPath] = useState("");
  const [manualSymbol, setManualSymbol] = useState("");
  const [wait, setWait] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TestGenerationResponse | null>(null);

  const repositoryList = useMemo(() => repositories.data ?? [], [repositories.data]);
  const effectiveRepositoryId =
    repositoryId || (repositoryList.length === 1 ? (repositoryList[0]?.id ?? "") : "");
  const selectedRepository = repositoryList.find((item) => item.id === effectiveRepositoryId);

  const changeRepository = (value: string) => {
    setRepositoryId(value);
    setTargets([]);
    setResult(null);
    setError(null);
    setSearch("");
  };

  const symbolPath = effectiveRepositoryId
    ? `/api/v1/repositories/${encodeURIComponent(effectiveRepositoryId)}/symbols${buildQuery({
        query: deferredSearch.trim(),
        limit: 100,
      })}`
    : null;
  const symbols = useApiQuery<CodeSymbolListResponse>(symbolPath);
  const symbolItems = useMemo(() => symbols.data?.items ?? [], [symbols.data]);

  const selectedKeys = useMemo(() => new Set(targets.map(targetKey)), [targets]);

  const toggleSymbol = (symbol: CodeSymbol) => {
    const candidate = { path: symbol.path, qualified_name: symbol.qualified_name };
    const key = targetKey(candidate);
    if (selectedKeys.has(key)) {
      setTargets((current) => current.filter((target) => targetKey(target) !== key));
      return;
    }
    if (targets.length >= MAX_TARGETS) {
      setError(`At most ${MAX_TARGETS} targets can be generated in one request.`);
      return;
    }
    setError(null);
    setTargets((current) => [...current, candidate]);
  };

  const addManualTarget = () => {
    if (!manualPath.trim() || !manualSymbol.trim()) return;
    const candidate = { path: manualPath.trim(), qualified_name: manualSymbol.trim() };
    const key = targetKey(candidate);
    if (selectedKeys.has(key)) {
      setError("That target is already selected.");
      return;
    }
    if (targets.length >= MAX_TARGETS) {
      setError(`At most ${MAX_TARGETS} targets can be generated in one request.`);
      return;
    }
    setError(null);
    setTargets((current) => [...current, candidate]);
    setManualPath("");
    setManualSymbol("");
  };

  const generate = async () => {
    if (!effectiveRepositoryId || targets.length === 0) {
      setError("Choose a repository and at least one target.");
      return;
    }
    setPending(true);
    setError(null);
    setResult(null);
    try {
      const response = await postJson<TestGenerationResponse>(
        `/api/v1/repositories/${encodeURIComponent(effectiveRepositoryId)}/test-candidates`,
        { targets, wait },
      );
      setResult(response);
      if (!wait && response.job_id) {
        router.push(`/jobs/${encodeURIComponent(response.job_id)}`);
      }
    } catch (cause) {
      setError(formatApiError(cause));
    } finally {
      setPending(false);
    }
  };

  const generationDisabled = runtime.data ? !runtime.data.test_generation.enabled : false;

  return (
    <div>
      <PageBreadcrumb items={[{ label: "Test intelligence", href: "/tests" }, { label: "Generate" }]} />
      <PageHeader
        eyebrow="Test intelligence"
        title="Generate candidate tests"
        description="Select symbols from the latest indexed snapshot. Generation is a model call; candidates are validated for path and shape, but they are not executed here."
        actions={<LinkButton href="/tests" variant="secondary">Back to candidates</LinkButton>}
      />

      {generationDisabled ? (
        <InlineNotice tone="warning" title="Test generation is disabled" className="mb-5">
          The backend has <code className="font-mono">TEST_GENERATION_ENABLED=false</code>. A request will
          return a conflict rather than silently doing nothing. Enable it and restart the API and worker.
        </InlineNotice>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[1.4fr_1fr]">
        <Card>
          <CardHeader
            title="Targets"
            description="Search the latest indexed snapshot, or enter a relative path and qualified name manually."
          />
          <CardBody className="space-y-4">
            <label className="block text-xs font-medium text-ink">
              Repository
              <select
                className={`${inputClasses} max-w-xl`}
                value={effectiveRepositoryId}
                onChange={(event) => changeRepository(event.target.value)}
              >
                <option value="">Select a repository…</option>
                {repositoryList.map((repository) => (
                  <option key={repository.id} value={repository.id}>
                    {repository.full_name}
                  </option>
                ))}
              </select>
            </label>

            {selectedRepository && !selectedRepository.latest_version ? (
              <InlineNotice tone="warning" title="No indexed snapshot">
                Generation reads source from the latest indexed version.{" "}
                <Link href={`/repositories/${selectedRepository.id}`} className="underline">
                  Index this repository
                </Link>{" "}
                before selecting targets.
              </InlineNotice>
            ) : null}

            {effectiveRepositoryId ? (
              <>
                <div>
                  <label htmlFor="symbol-search" className="text-xs font-medium text-ink">
                    Search symbols
                  </label>
                  <div className="relative mt-1">
                    <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted" aria-hidden />
                    <input
                      id="symbol-search"
                      value={search}
                      onChange={(event) => setSearch(event.target.value)}
                      placeholder="path, name or qualified name"
                      className="h-10 w-full rounded-lg border border-line bg-surface pl-9 pr-3 text-sm text-ink placeholder:text-muted/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                    />
                  </div>
                  <p className="mt-1 text-[11px] text-muted">
                    {symbols.loading
                      ? "Searching the snapshot…"
                      : symbols.data
                        ? `${formatCount(symbols.data.pagination.total)} matching production symbol${symbols.data.pagination.total === 1 ? "" : "s"}; showing ${formatCount(symbolItems.length)}.`
                        : "Indexed symbols only."}
                  </p>
                </div>

                {symbols.error ? (
                  <InlineNotice tone="warning" title="Symbols could not be loaded">
                    {symbols.error.message}
                  </InlineNotice>
                ) : null}

                {symbols.loading && !symbols.data ? <LoadingState rows={2} /> : null}
                {symbols.data && symbolItems.length === 0 ? (
                  <EmptyState
                    title="No symbols match"
                    description="Use the manual target fields below if the symbol exists under a different qualified name."
                    icon={Search}
                  />
                ) : null}
                {symbolItems.length > 0 ? (
                  <ul className="max-h-[24rem] divide-y divide-line overflow-y-auto rounded-lg border border-line">
                    {symbolItems.map((symbol) => {
                      const selected = selectedKeys.has(targetKey(symbol));
                      return (
                        <li key={symbol.id}>
                          <button
                            type="button"
                            onClick={() => toggleSymbol(symbol)}
                            aria-pressed={selected}
                            className={`flex w-full items-start gap-3 px-3 py-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/40 ${
                              selected ? "bg-accent-soft" : "hover:bg-surface-muted"
                            }`}
                          >
                            <span
                              className={`mt-0.5 grid size-4 shrink-0 place-items-center rounded border ${
                                selected ? "border-accent bg-accent text-white" : "border-line bg-surface"
                              }`}
                              aria-hidden
                            >
                              {selected ? "✓" : null}
                            </span>
                            <span className="min-w-0 flex-1">
                              <span className="block truncate font-mono text-xs text-ink">{symbol.qualified_name}</span>
                              <span className="block truncate text-[11px] text-muted">
                                {symbol.path}:{symbol.start_line} · {titleCase(symbol.kind)}
                              </span>
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                ) : null}
              </>
            ) : null}

            <div className="rounded-xl border border-dashed border-line-strong p-4">
              <h3 className="text-xs font-semibold text-ink">Manual target</h3>
              <p className="mt-0.5 text-[11px] leading-5 text-muted">
                The API rejects a target that is absent from the snapshot, so a typo cannot silently
                generate a test against the wrong file.
              </p>
              <div className="mt-3 grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
                <label className="block text-xs font-medium text-ink">
                  Relative path
                  <input
                    className={inputClasses}
                    value={manualPath}
                    onChange={(event) => setManualPath(event.target.value)}
                    placeholder="app/calculator.py"
                    maxLength={1_024}
                  />
                </label>
                <label className="block text-xs font-medium text-ink">
                  Qualified name
                  <input
                    className={inputClasses}
                    value={manualSymbol}
                    onChange={(event) => setManualSymbol(event.target.value)}
                    placeholder="add"
                    maxLength={1_024}
                  />
                </label>
                <Button variant="secondary" onClick={addManualTarget} disabled={!manualPath.trim() || !manualSymbol.trim()}>
                  <Plus className="size-3.5" aria-hidden /> Add
                </Button>
              </div>
            </div>
          </CardBody>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader
              title="Selected targets"
              description={`${targets.length} of ${MAX_TARGETS} selected`}
              action={
                targets.length > 0 ? (
                  <Button variant="ghost" size="sm" onClick={() => setTargets([])}>
                    Clear
                  </Button>
                ) : null
              }
            />
            <CardBody>
              {targets.length === 0 ? (
                <p className="text-sm text-muted">No targets selected yet.</p>
              ) : (
                <ul className="space-y-2">
                  {targets.map((target) => (
                    <li key={targetKey(target)} className="flex items-start gap-2 rounded-lg border border-line bg-surface-muted/50 px-3 py-2">
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-mono text-xs text-ink">{target.qualified_name}</p>
                        <p className="truncate text-[11px] text-muted">{target.path}</p>
                      </div>
                      <button
                        type="button"
                        onClick={() => setTargets((current) => current.filter((item) => targetKey(item) !== targetKey(target)))}
                        className="rounded p-1 text-muted hover:bg-surface hover:text-danger-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                        aria-label={`Remove ${target.qualified_name}`}
                      >
                        <Trash2 className="size-3.5" aria-hidden />
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              <label className="mt-4 flex items-start gap-2 rounded-lg border border-line bg-surface-muted/40 px-3 py-2.5 text-xs leading-5 text-muted">
                <input
                  type="checkbox"
                  checked={wait}
                  onChange={(event) => setWait(event.target.checked)}
                  className="mt-0.5 size-3.5 rounded border-line text-accent focus-visible:ring-accent/40"
                />
                <span>
                  <span className="font-medium text-ink">Generate synchronously.</span> Otherwise a
                  durable job is queued for a worker. The API records which one happened.
                </span>
              </label>

              {error ? (
                <InlineNotice tone="danger" title="Generation was not started" className="mt-3">
                  {error}
                </InlineNotice>
              ) : null}

              <Button
                className="mt-4 w-full"
                loading={pending}
                disabled={!effectiveRepositoryId || targets.length === 0}
                onClick={generate}
              >
                <FlaskConical className="size-4" aria-hidden />
                {wait ? "Generate now" : "Queue generation"}
              </Button>
            </CardBody>
          </Card>

          {result ? (
            <Card>
              <CardHeader
                title="Generation result"
                description={result.note ?? "The API returned the candidates it persisted."}
                action={
                  <Badge tone={result.executed_synchronously ? "success" : "info"}>
                    {result.executed_synchronously ? "Synchronous" : "Queued"}
                  </Badge>
                }
              />
              <CardBody>
                {result.candidates.length === 0 ? (
                  <p className="text-sm text-muted">
                    No candidates were recorded. If a job was queued, follow it for the real outcome.
                  </p>
                ) : (
                  <ul className="divide-y divide-line">
                    {result.candidates.map((candidate: TestCandidate) => (
                      <li key={candidate.id} className="flex flex-wrap items-center gap-2 py-2.5 first:pt-0 last:pb-0">
                        <StatusBadge status={candidate.status} />
                        <Link href={`/tests/${candidate.id}`} className="min-w-0 flex-1 truncate font-mono text-xs text-accent-strong hover:underline">
                          {candidate.suggested_test_path}
                        </Link>
                        <ProviderBadge provider={candidate.provider} isMock={candidate.is_mock_provider} />
                      </li>
                    ))}
                  </ul>
                )}
                {result.job_id ? (
                  <Link href={`/jobs/${result.job_id}`} className="mt-3 inline-block text-xs font-medium text-accent-strong hover:underline">
                    Follow job {result.job_id} →
                  </Link>
                ) : null}
              </CardBody>
            </Card>
          ) : null}

          <Card>
            <CardHeader title="Reality check" />
            <CardBody className="space-y-2 text-xs leading-5 text-muted">
              <p>
                Generation uses the latest <span className="font-medium text-ink">indexed snapshot</span>,
                not a pull-request head. Review-triggered generation uses post-change source.
              </p>
              <p>
                A candidate is a proposed test. Its status starts at{" "}
                <span className="font-mono">generated</span>; only an explicit execution changes that.
              </p>
              <p>
                The deterministic mock provider is safe for demos but does not demonstrate model quality.
              </p>
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  );
}
