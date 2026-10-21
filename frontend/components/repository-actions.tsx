"use client";

import { useState } from "react";
import { Plus, RefreshCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { InlineNotice } from "@/components/ui/states";
import { formatApiError, postJson } from "@/lib/api";
import { useMeta } from "@/lib/hooks/use-meta";
import type { Job, Repository } from "@/lib/types";

export function IndexButton({
  repositoryId,
  onQueued,
  label = "Index now",
}: {
  repositoryId: string;
  onQueued?: (job: Job) => void;
  label?: string;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);

  return (
    <div>
      <Button
        size="sm"
        variant="secondary"
        loading={pending}
        onClick={async () => {
          setPending(true);
          setError(null);
          setJob(null);
          try {
            const result = await postJson<Job>(`/api/v1/repositories/${encodeURIComponent(repositoryId)}/index`, {});
            setJob(result);
            onQueued?.(result);
          } catch (cause) {
            setError(formatApiError(cause));
          } finally {
            setPending(false);
          }
        }}
      >
        <RefreshCcw className="size-3.5" aria-hidden />
        {label}
      </Button>
      {job ? (
        <p className="mt-1.5 max-w-xs text-[11px] leading-4 text-success-strong" role="status">
          Job <span className="font-mono">{job.id}</span> is {job.status}. A worker must be running
          to process it.
        </p>
      ) : null}
      {error ? (
        <p className="mt-1.5 max-w-xs text-[11px] leading-4 text-danger-strong" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

const inputClasses =
  "h-10 w-full rounded-lg border border-line bg-surface px-3 text-sm text-ink placeholder:text-muted/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40";

export function RegisterRepositoryForm({ onRegistered }: { onRegistered?: (repository: Repository) => void }) {
  const { meta } = useMeta();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [form, setForm] = useState({
    owner: "local",
    name: "",
    local_path: "",
    default_branch: "main",
  });

  const registrationDisabled = meta ? !meta.capabilities.local_repository_registration : false;

  if (registrationDisabled) {
    return (
      <InlineNotice tone="neutral" title="Local registration is disabled">
        This deployment is configured as production. Register repositories through the GitHub App
        instead of a local checkout path.
      </InlineNotice>
    );
  }

  return (
    <div>
      <Button variant="secondary" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <Plus className="size-4" aria-hidden />
        Register checkout
      </Button>
      {open ? (
        <form
          className="mt-3 rounded-xl border border-line bg-surface p-4"
          onSubmit={async (event) => {
            event.preventDefault();
            setPending(true);
            setError(null);
            setSuccess(null);
            try {
              const repository = await postJson<Repository>("/api/v1/repositories", {
                owner: form.owner.trim(),
                name: form.name.trim(),
                local_path: form.local_path.trim(),
                default_branch: form.default_branch.trim() || "main",
                provider: "local",
              });
              setSuccess(`Registered ${repository.full_name}. Use “Index now” to parse it.`);
              setForm((current) => ({ ...current, name: "", local_path: "" }));
              onRegistered?.(repository);
            } catch (cause) {
              setError(formatApiError(cause));
            } finally {
              setPending(false);
            }
          }}
        >
          <p className="mb-3 text-xs leading-5 text-muted">
            Development-only. The backend verifies that the path is an existing directory on the
            machine running the API. It never accepts a path from a browser in production.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block text-xs font-medium text-ink">
              Owner
              <input
                className={`${inputClasses} mt-1`}
                required
                maxLength={255}
                value={form.owner}
                onChange={(event) => setForm((current) => ({ ...current, owner: event.target.value }))}
              />
            </label>
            <label className="block text-xs font-medium text-ink">
              Repository name
              <input
                className={`${inputClasses} mt-1`}
                required
                maxLength={255}
                placeholder="patchlens-demo"
                value={form.name}
                onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
              />
            </label>
            <label className="block text-xs font-medium text-ink sm:col-span-2">
              Local checkout path (absolute)
              <input
                className={`${inputClasses} mt-1 font-mono text-xs`}
                required
                maxLength={1024}
                placeholder="/Users/you/projects/my-python-service"
                value={form.local_path}
                onChange={(event) => setForm((current) => ({ ...current, local_path: event.target.value }))}
              />
            </label>
            <label className="block text-xs font-medium text-ink">
              Default branch
              <input
                className={`${inputClasses} mt-1`}
                maxLength={255}
                value={form.default_branch}
                onChange={(event) => setForm((current) => ({ ...current, default_branch: event.target.value }))}
              />
            </label>
          </div>
          <div className="mt-4 flex items-center gap-2">
            <Button type="submit" loading={pending}>
              Register repository
            </Button>
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </div>
          {error ? (
            <p className="mt-3 text-xs text-danger-strong" role="alert">
              {error}
            </p>
          ) : null}
          {success ? (
            <p className="mt-3 text-xs text-success-strong" role="status">
              {success}
            </p>
          ) : null}
        </form>
      ) : null}
    </div>
  );
}
