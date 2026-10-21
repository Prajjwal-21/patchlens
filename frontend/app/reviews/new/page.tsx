"use client";

import { type FormEvent, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { FileUp, GitPullRequest, Info, ShieldCheck } from "lucide-react";

import { Badge, ProviderBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { PageBreadcrumb, PageHeader } from "@/components/ui/page-header";
import { InlineNotice } from "@/components/ui/states";
import { formatApiError, postJson } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { useApiQuery } from "@/lib/hooks/use-api";
import { useMeta } from "@/lib/hooks/use-meta";
import type { Repository, ReviewCreateResponse } from "@/lib/types";

const MAX_DIFF_CHARS = 2_000_000;

const fieldClasses =
  "mt-1 w-full rounded-lg border border-line bg-surface px-3 text-sm text-ink placeholder:text-muted/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40";

function repositoryCommit(repository: Repository | undefined): string {
  return repository?.indexed_commit_sha ?? "";
}

export default function NewReviewPage() {
  const router = useRouter();
  const { meta } = useMeta();
  const repositories = useApiQuery<Repository[]>("/api/v1/repositories?limit=200");

  const [repositoryId, setRepositoryId] = useState("");
  const [commitSha, setCommitSha] = useState("");
  const [baseSha, setBaseSha] = useState("");
  const [pullRequestId, setPullRequestId] = useState("");
  const [diffText, setDiffText] = useState("");
  const [wait, setWait] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const repositoryList = useMemo(() => repositories.data ?? [], [repositories.data]);
  const effectiveRepositoryId =
    repositoryId || (repositoryList.length === 1 ? (repositoryList[0]?.id ?? "") : "");
  const selectedRepository = repositoryList.find((item) => item.id === effectiveRepositoryId);
  const diffBytes = useMemo(() => new TextEncoder().encode(diffText).length, [diffText]);
  const tooLarge = diffText.length > MAX_DIFF_CHARS;

  const handleRepositoryChange = (value: string) => {
    setRepositoryId(value);
    const repository = repositoryList.find((item) => item.id === value);
    if (!commitSha || commitSha === repositoryCommit(selectedRepository)) {
      setCommitSha(repositoryCommit(repository));
    }
  };

  const handleFile = async (file: File | undefined) => {
    setFileError(null);
    if (!file) return;
    if (file.size > MAX_DIFF_CHARS * 4) {
      setFileError(`That file is ${formatCount(file.size)} bytes; the diff limit is ${formatCount(MAX_DIFF_CHARS)} characters.`);
      return;
    }
    const text = await file.text();
    if (text.length > MAX_DIFF_CHARS) {
      setFileError(
        `The file contains ${formatCount(text.length)} characters; the API accepts at most ${formatCount(MAX_DIFF_CHARS)}.`,
      );
      return;
    }
    setDiffText(text);
    if (fileInput.current) fileInput.current.value = "";
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    if (!effectiveRepositoryId) {
      setError("Choose a repository first.");
      return;
    }
    if (!commitSha.trim()) {
      setError("A commit SHA is required. It anchors the review and deduplicates retries.");
      return;
    }
    if (!diffText.trim()) {
      setError("Paste or load a unified diff.");
      return;
    }
    if (tooLarge) {
      setError(`The diff is ${formatCount(diffText.length)} characters; the limit is ${formatCount(MAX_DIFF_CHARS)}.`);
      return;
    }

    setPending(true);
    try {
      const result = await postJson<ReviewCreateResponse>("/api/v1/reviews", {
        repository_id: effectiveRepositoryId,
        diff_text: diffText,
        commit_sha: commitSha.trim(),
        base_sha: baseSha.trim() || null,
        pull_request_id: pullRequestId.trim() || null,
        wait,
      });
      router.push(`/reviews/${encodeURIComponent(result.review.id)}`);
    } catch (cause) {
      setError(formatApiError(cause));
      setPending(false);
    }
  };

  return (
    <div>
      <PageBreadcrumb items={[{ label: "Reviews", href: "/reviews" }, { label: "New review" }]} />
      <PageHeader
        eyebrow="Review"
        title="Review a diff"
        description="Submit a unified diff. PatchLens extracts changed symbols, retrieves repository context, runs deterministic checks and validates every candidate before recording a finding."
        actions={<LinkButton href="/reviews" variant="secondary">Back to reviews</LinkButton>}
      />

      <div className="grid gap-6 xl:grid-cols-[1.5fr_1fr]">
        <Card>
          <CardHeader
            title="Diff and identity"
            description="The diff is stored with the review so every finding can be traced back to an added line."
          />
          <CardBody>
            <form className="space-y-4" onSubmit={submit}>
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="block text-xs font-medium text-ink">
                  Repository
                  <select
                    className={`${fieldClasses} h-10`}
                    required
                    value={effectiveRepositoryId}
                    onChange={(event) => handleRepositoryChange(event.target.value)}
                  >
                    <option value="">Select a repository…</option>
                    {repositoryList.map((repository) => (
                      <option key={repository.id} value={repository.id}>
                        {repository.full_name}
                        {repository.index_status === "indexed" ? "" : ` (${repository.index_status})`}
                      </option>
                    ))}
                  </select>
                  {repositories.loading ? <span className="mt-1 block text-[11px] text-muted">Loading repositories…</span> : null}
                  {repositories.data && repositoryList.length === 0 ? (
                    <span className="mt-1 block text-[11px] text-warning-strong">
                      No repositories registered.{" "}
                      <Link className="underline" href="/repositories">
                        Register a local checkout
                      </Link>{" "}
                      first.
                    </span>
                  ) : null}
                </label>
                <label className="block text-xs font-medium text-ink">
                  Commit SHA
                  <input
                    className={`${fieldClasses} h-10 font-mono text-xs`}
                    required
                    maxLength={64}
                    value={commitSha}
                    onChange={(event) => setCommitSha(event.target.value)}
                    placeholder="head commit of the diff"
                  />
                </label>
                <label className="block text-xs font-medium text-ink">
                  Base SHA <span className="font-normal text-muted">(optional)</span>
                  <input
                    className={`${fieldClasses} h-10 font-mono text-xs`}
                    maxLength={64}
                    value={baseSha}
                    onChange={(event) => setBaseSha(event.target.value)}
                    placeholder="base commit"
                  />
                </label>
                <label className="block text-xs font-medium text-ink">
                  Pull request id <span className="font-normal text-muted">(optional)</span>
                  <input
                    className={`${fieldClasses} h-10 font-mono text-xs`}
                    maxLength={64}
                    value={pullRequestId}
                    onChange={(event) => setPullRequestId(event.target.value)}
                    placeholder="internal pull request record id"
                  />
                </label>
              </div>

              <div>
                <div className="flex flex-wrap items-end justify-between gap-2">
                  <label htmlFor="diff" className="text-xs font-medium text-ink">
                    Unified diff
                  </label>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`text-[11px] ${tooLarge ? "text-danger-strong" : "text-muted"}`}>
                      {formatCount(diffText.length)} / {formatCount(MAX_DIFF_CHARS)} characters · {formatCount(diffBytes)} bytes
                    </span>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => fileInput.current?.click()}
                      disabled={pending}
                    >
                      <FileUp className="size-3.5" aria-hidden />
                      Load file
                    </Button>
                    <input
                      ref={fileInput}
                      type="file"
                      accept=".diff,.patch,text/plain,text/x-diff"
                      className="sr-only"
                      aria-label="Load a diff file"
                      onChange={(event) => void handleFile(event.target.files?.[0])}
                    />
                  </div>
                </div>
                <textarea
                  id="diff"
                  className={`${fieldClasses} mt-1 min-h-[22rem] resize-y p-3 font-mono text-xs leading-6`}
                  required
                  spellCheck={false}
                  value={diffText}
                  onChange={(event) => setDiffText(event.target.value)}
                  placeholder={"diff --git a/app/calculator.py b/app/calculator.py\n--- a/app/calculator.py\n+++ b/app/calculator.py\n@@ -1,3 +1,4 @@\n ..."}
                  aria-describedby="diff-help"
                />
                <p id="diff-help" className="mt-1.5 text-[11px] leading-5 text-muted">
                  Diffs are parsed locally, not sent to GitHub. A malformed diff is rejected by the API
                  rather than guessed at. Binary patches are not reviewable here.
                </p>
                {fileError ? (
                  <p className="mt-1 text-xs text-danger-strong" role="alert">
                    {fileError}
                  </p>
                ) : null}
              </div>

              <label className="flex items-start gap-2 rounded-lg border border-line bg-surface-muted/60 px-3 py-2.5 text-xs leading-5 text-muted">
                <input
                  type="checkbox"
                  checked={wait}
                  onChange={(event) => setWait(event.target.checked)}
                  className="mt-0.5 size-3.5 rounded border-line text-accent focus-visible:ring-accent/40"
                />
                <span>
                  <span className="font-medium text-ink">Run synchronously in this request.</span>{" "}
                  Convenient for small diffs and demos. The default queues a durable job, which needs a
                  running worker. Synchronous failures are still recorded on the review and job.
                </span>
              </label>

              {error ? (
                <InlineNotice tone="danger" title="Review was not started">
                  {error}
                </InlineNotice>
              ) : null}

              <div className="flex flex-wrap items-center gap-2">
                <Button type="submit" loading={pending} disabled={tooLarge || !effectiveRepositoryId}>
                  <GitPullRequest className="size-4" aria-hidden />
                  {wait ? "Run review now" : "Queue review"}
                </Button>
                <LinkButton href="/reviews" variant="ghost">
                  Cancel
                </LinkButton>
                <span className="text-[11px] text-muted">
                  {pending ? "Starting review…" : wait ? "Runs before the response returns." : "Returns as soon as the job is queued."}
                </span>
              </div>
            </form>
          </CardBody>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader title="Runtime" description="Effective configuration for this request." />
            <CardBody>
              {meta ? (
                <dl className="space-y-3 text-xs">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <dt className="text-muted">Review provider</dt>
                    <dd>
                      <ProviderBadge provider={meta.llm.provider} isMock={meta.llm.is_mock} />
                    </dd>
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <dt className="text-muted">Model</dt>
                    <dd className="max-w-[14rem] truncate font-mono text-[11px] text-ink" title={meta.llm.model}>
                      {meta.llm.model}
                    </dd>
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <dt className="text-muted">Retrieval</dt>
                    <dd className="font-mono text-[11px] text-ink">{meta.retrieval.strategy}</dd>
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <dt className="text-muted">Rules loaded</dt>
                    <dd className="font-mono text-[11px] text-ink">{meta.review_rules.length}</dd>
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <dt className="text-muted">Test generation</dt>
                    <dd>
                      <Badge tone={meta.capabilities.test_execution === "disabled" ? "neutral" : "warning"}>
                        execution: {meta.capabilities.test_execution}
                      </Badge>
                    </dd>
                  </div>
                </dl>
              ) : (
                <p className="text-xs text-muted">Provider status is unavailable. The review can still run.</p>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="What this produces" />
            <CardBody>
              <ul className="space-y-3 text-xs leading-5 text-muted">
                <li className="flex gap-2">
                  <ShieldCheck className="mt-0.5 size-4 shrink-0 text-success" aria-hidden />
                  <span>
                    <span className="font-medium text-ink">Deterministic checks</span> match added lines
                    and are recorded as verified only when the line anchor exists in the diff.
                  </span>
                </li>
                <li className="flex gap-2">
                  <Info className="mt-0.5 size-4 shrink-0 text-info" aria-hidden />
                  <span>
                    <span className="font-medium text-ink">Model findings</span> are structurally
                    validated but never presented as confirmed defects. Unverifiable or duplicate
                    output is rejected with a reason.
                  </span>
                </li>
                <li className="flex gap-2">
                  <Info className="mt-0.5 size-4 shrink-0 text-info" aria-hidden />
                  <span>
                    <span className="font-medium text-ink">No hidden execution.</span> Candidate tests
                    may be generated, but they run only when the sandbox is explicitly enabled and an
                    operator asks for execution.
                  </span>
                </li>
              </ul>
            </CardBody>
          </Card>

          {selectedRepository?.index_status !== "indexed" && selectedRepository ? (
            <InlineNotice tone="warning" title="This repository is not indexed">
              Reviews still parse the diff, but retrieval and symbol-aware checks have no snapshot to
              use. <a className="underline" href={`/repositories/${selectedRepository.id}`}>Index it first</a>{" "}
              for the full pipeline.
            </InlineNotice>
          ) : null}
        </div>
      </div>
    </div>
  );
}
