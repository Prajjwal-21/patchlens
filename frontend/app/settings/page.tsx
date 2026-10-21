"use client";

import Link from "next/link";
import { ExternalLink, Lock, RefreshCcw, Settings2, ShieldCheck } from "lucide-react";

import { Badge, ProviderBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { KeyValue } from "@/components/ui/data-display";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState, ErrorState, InlineNotice, LoadingState } from "@/components/ui/states";
import { Table, Td, Th, Tr } from "@/components/ui/table";
import { formatBytes, formatDateTime, formatDuration, humanizeStatus } from "@/lib/format";
import { useApiQuery } from "@/lib/hooks/use-api";
import type {
  GitHubInstallation,
  Pagination,
  SettingsResponse,
  WebhookDelivery,
} from "@/lib/types";

interface InstallationsResponse {
  items: GitHubInstallation[];
  pagination: Pagination;
}

interface DeliveriesResponse {
  items: WebhookDelivery[];
  pagination: Pagination;
}

function SectionCard({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader title={title} description={description} />
      <CardBody>{children}</CardBody>
    </Card>
  );
}

export default function SettingsPage() {
  const settings = useApiQuery<SettingsResponse>("/api/v1/settings");
  const installations = useApiQuery<InstallationsResponse>("/api/v1/github/installations?limit=10");
  const deliveries = useApiQuery<DeliveriesResponse>("/api/v1/github/deliveries?limit=10");

  if (settings.loading && !settings.data) {
    return (
      <>
        <PageHeader title="Settings" description="Loading effective runtime configuration…" />
        <LoadingState rows={4} />
      </>
    );
  }
  if (settings.error && !settings.data) {
    return (
      <>
        <PageHeader title="Settings" description="Effective runtime configuration and integration state." />
        <ErrorState error={settings.error} onRetry={settings.reload} title="Configuration could not be loaded" />
      </>
    );
  }
  if (!settings.data) return null;

  const config = settings.data;

  return (
    <div>
      <PageHeader
        eyebrow="System"
        title="Settings"
        description="Effective, read-only configuration. Changing a provider, sandbox tier or publication switch is a deployment change, not a dashboard toggle."
        actions={
          <Button
            variant="secondary"
            onClick={() => {
              settings.reload();
              installations.reload();
              deliveries.reload();
            }}
          >
            <RefreshCcw className="size-3.5" aria-hidden /> Refresh
          </Button>
        }
      />

      <InlineNotice tone="neutral" title="Configuration is environment-driven" className="mb-5">
        <p className="flex items-start gap-2">
          <Lock className="mt-0.5 size-4 shrink-0" aria-hidden />
          This page cannot mutate secrets or switches. Restart the API and worker after changing
          environment variables. Secrets are never returned, only whether they are configured.
        </p>
      </InlineNotice>

      <div className="grid gap-5 xl:grid-cols-2">
        <SectionCard
          title="Providers"
          description="The identity recorded on every review, finding and evaluation run."
        >
          <dl className="divide-y divide-line/70">
            <KeyValue label="Review provider">
              <ProviderBadge provider={config.llm.provider} isMock={config.llm.is_mock} />
            </KeyValue>
            <KeyValue label="Review model" mono>{config.llm.model}</KeyValue>
            <KeyValue label="Base URL" mono>{config.llm.base_url ?? "Provider default"}</KeyValue>
            <KeyValue label="API key">
              {config.llm.api_key_configured ? "Configured (not shown)" : "Not configured"}
            </KeyValue>
            <KeyValue label="Output budget">
              {config.llm.max_output_tokens ?? "UNKNOWN"} tokens · {config.llm.timeout_seconds ?? "UNKNOWN"}s timeout
            </KeyValue>
            <KeyValue label="Embedding provider">{config.embedding.provider}</KeyValue>
            <KeyValue label="Embedding model" mono>{config.embedding.model}</KeyValue>
            <KeyValue label="Embedding dimension">{config.embedding.dim ?? "UNKNOWN"}</KeyValue>
            <KeyValue label="Neural embeddings">
              {config.embedding.is_neural === true ? "Yes" : config.embedding.is_neural === false ? "No (deterministic hashing)" : "UNKNOWN"}
            </KeyValue>
          </dl>
          {config.llm.is_mock ? (
            <InlineNotice tone="warning" title="Deterministic mock provider" className="mt-3">
              No model is called. Findings are produced by the rule engine and retrieval pipeline; metrics
              from this configuration do not measure model quality.
            </InlineNotice>
          ) : null}
        </SectionCard>

        <SectionCard
          title="Retrieval"
          description="Hybrid retrieval configuration and the effective semantic backend."
        >
          <dl className="divide-y divide-line/70">
            <KeyValue label="Strategy" mono>{config.retrieval.strategy}</KeyValue>
            <KeyValue label="Top K">{config.retrieval.top_k}</KeyValue>
            <KeyValue label="Candidate K">{config.retrieval.candidate_k}</KeyValue>
            <KeyValue label="RRF k">{config.retrieval.rrf_k}</KeyValue>
            <KeyValue label="Reranker" mono>{config.retrieval.reranker}</KeyValue>
            <KeyValue label="Semantic backend">{config.retrieval.semantic_backend}</KeyValue>
            <KeyValue label="Max per file">{config.retrieval.max_results_per_file ?? "UNKNOWN"}</KeyValue>
          </dl>
          {config.retrieval.note ? (
            <p className="mt-3 text-[11px] leading-5 text-muted">{config.retrieval.note}</p>
          ) : null}
        </SectionCard>

        <SectionCard
          title="Test intelligence"
          description="Generation and execution are separate permissions. Generation does not imply execution."
        >
          <dl className="divide-y divide-line/70">
            <KeyValue label="Generation">
              <Badge tone={config.test_generation.enabled ? "success" : "neutral"}>
                {config.test_generation.enabled ? "Enabled" : "Disabled"}
              </Badge>
            </KeyValue>
            <KeyValue label="Max candidates per request">{config.test_generation.max_candidates}</KeyValue>
            <KeyValue label="Execution mode">{config.test_execution.mode}</KeyValue>
            <KeyValue label="Network">
              {config.test_execution.network_enabled ? "Enabled" : "Disabled"}
            </KeyValue>
            <KeyValue label="Timeout">{config.test_execution.timeout_seconds}s</KeyValue>
            <KeyValue label="Memory cap">{config.test_execution.memory_mb} MB</KeyValue>
          </dl>
          <p className="mt-3 text-[11px] leading-5 text-muted">{config.test_execution.detail}</p>
        </SectionCard>

        <SectionCard
          title="Security and limits"
          description="Deployment guards. Values reflect this process, not a previous configuration."
        >
          <dl className="divide-y divide-line/70">
            <KeyValue label="API auth">
              <Badge tone={config.security.auth_required ? "success" : "warning"}>
                {config.security.auth_required ? "Bearer token required" : "Open in this environment"}
              </Badge>
            </KeyValue>
            <KeyValue label="CORS origins">
              {config.security.cors_origins.length > 0 ? config.security.cors_origins.join(", ") : "None (same-origin proxy)"}
            </KeyValue>
            <KeyValue label="Trust proxy headers">{config.security.trust_proxy_headers ? "Yes" : "No"}</KeyValue>
            <KeyValue label="Max request">{formatBytes(config.security.max_request_bytes)}</KeyValue>
            <KeyValue label="Webhook signatures">
              {config.security.webhook_signature_verification ? "HMAC verified" : "Not verified"}
            </KeyValue>
            <KeyValue label="Index limits">
              {config.limits.index_max_files} files · {formatBytes(config.limits.index_max_file_bytes)} per file
            </KeyValue>
            <KeyValue label="Review finding cap">{config.limits.review_max_findings}</KeyValue>
            <KeyValue label="Job / LLM timeouts">
              {formatDuration(config.limits.job_timeout_seconds * 1_000)} /{" "}
              {formatDuration(config.limits.llm_timeout_seconds * 1_000)}
            </KeyValue>
          </dl>
        </SectionCard>
      </div>

      <div className="mt-5">
        <SectionCard
          title="GitHub integration"
          description="Webhook ingestion and publication are independently gated. A configured webhook does not enable publishing."
        >
          <div className="grid gap-4 lg:grid-cols-2">
            <dl className="divide-y divide-line/70">
              <KeyValue label="App configured">
                <Badge tone={config.github.app_configured ? "success" : "neutral"}>
                  {config.github.app_configured ? "Yes" : "No"}
                </Badge>
              </KeyValue>
              <KeyValue label="Webhook secret">
                <Badge tone={config.github.webhook_secret_configured ? "success" : "neutral"}>
                  {config.github.webhook_secret_configured ? "Configured" : "Not configured"}
                </Badge>
              </KeyValue>
              <KeyValue label="Publishing">
                <Badge tone={config.github.publish_enabled ? "warning" : "neutral"}>
                  {config.github.publish_enabled ? "Enabled" : "Disabled"}
                </Badge>
              </KeyValue>
              <KeyValue label="Comment severities">
                {config.github.comment_severities.join(", ") || "None"}
              </KeyValue>
              <KeyValue label="Max comments per review">{config.github.max_comments_per_review}</KeyValue>
            </dl>
            <div>
              <p className="text-[11px] leading-5 text-muted">{config.github.note}</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Link
                  href="/reviews"
                  className="inline-flex items-center gap-1 text-xs font-medium text-accent-strong hover:underline"
                >
                  Reviews and publish state <ExternalLink className="size-3" aria-hidden />
                </Link>
              </div>
              <p className="mt-3 flex items-start gap-2 text-[11px] leading-5 text-muted">
                <ShieldCheck className="mt-0.5 size-4 shrink-0 text-success" aria-hidden />
                Webhook bodies are verified with HMAC-SHA256 before parsing. Deliveries that are ignored
                still record why, rather than returning a misleading success.
              </p>
            </div>
          </div>
        </SectionCard>
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-2">
        <Card>
          <CardHeader
            title="Installations"
            description="GitHub App installations seen through webhook deliveries."
            action={<Badge tone="neutral">{installations.data?.pagination.total ?? 0}</Badge>}
          />
          {installations.loading && !installations.data ? (
            <CardBody>
              <p className="text-sm text-muted">Loading installations…</p>
            </CardBody>
          ) : installations.error && !installations.data ? (
            <ErrorState error={installations.error} onRetry={installations.reload} />
          ) : installations.data && installations.data.items.length === 0 ? (
            <EmptyState
              title="No installations recorded"
              description="Install the GitHub App and send an installation event; it will appear here with the permissions GitHub reported."
              icon={Settings2}
            />
          ) : installations.data ? (
            <Table label="GitHub installations">
              <thead>
                <tr>
                  <Th>Account</Th>
                  <Th>Installation</Th>
                  <Th>Selection</Th>
                  <Th>Repositories</Th>
                  <Th>Suspended</Th>
                </tr>
              </thead>
              <tbody>
                {installations.data.items.map((installation) => (
                  <Tr key={installation.id}>
                    <Td>
                      <span className="text-xs font-medium text-ink">{installation.account_login}</span>
                      <p className="text-[10px] text-muted">{installation.account_type}</p>
                    </Td>
                    <Td className="font-mono text-[11px] text-muted">{installation.installation_id}</Td>
                    <Td className="text-xs text-muted">{humanizeStatus(installation.repository_selection)}</Td>
                    <Td className="text-xs tabular-nums text-muted">{installation.repository_count}</Td>
                    <Td>
                      <Badge tone={installation.suspended ? "warning" : "success"}>
                        {installation.suspended ? "Yes" : "No"}
                      </Badge>
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          ) : null}
        </Card>

        <Card>
          <CardHeader
            title="Recent webhook deliveries"
            description="Signature verification and processing outcome per delivery."
            action={<Badge tone="neutral">{deliveries.data?.pagination.total ?? 0}</Badge>}
          />
          {deliveries.loading && !deliveries.data ? (
            <CardBody>
              <p className="text-sm text-muted">Loading deliveries…</p>
            </CardBody>
          ) : deliveries.error && !deliveries.data ? (
            <ErrorState error={deliveries.error} onRetry={deliveries.reload} />
          ) : deliveries.data && deliveries.data.items.length === 0 ? (
            <EmptyState
              title="No deliveries"
              description="Deliveries appear after GitHub sends a signed webhook. Unconfigured webhooks are not accepted."
              icon={Settings2}
            />
          ) : deliveries.data ? (
            <Table label="Recent webhook deliveries">
              <thead>
                <tr>
                  <Th>Event</Th>
                  <Th>Repository</Th>
                  <Th>Verified</Th>
                  <Th>Status</Th>
                  <Th>Received</Th>
                </tr>
              </thead>
              <tbody>
                {deliveries.data.items.map((delivery) => (
                  <Tr key={delivery.id}>
                    <Td>
                      <span className="text-xs font-medium text-ink">{delivery.event}</span>
                      <p className="text-[10px] text-muted">{delivery.action ?? "—"}</p>
                    </Td>
                    <Td className="max-w-[12rem] truncate text-xs text-muted">{delivery.repository_full_name ?? "—"}</Td>
                    <Td>
                      <Badge tone={delivery.signature_verified ? "success" : "danger"}>
                        {delivery.signature_verified ? "Yes" : "No"}
                      </Badge>
                    </Td>
                    <Td>
                      <Badge tone={delivery.status === "processed" ? "success" : delivery.status === "failed" ? "danger" : "neutral"}>
                        {humanizeStatus(delivery.status)}
                      </Badge>
                    </Td>
                    <Td className="whitespace-nowrap text-xs text-muted">{formatDateTime(delivery.received_at)}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          ) : null}
        </Card>
      </div>
    </div>
  );
}
