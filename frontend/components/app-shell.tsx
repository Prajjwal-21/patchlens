"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  BarChart3,
  ChevronRight,
  FlaskConical,
  FolderGit2,
  GitPullRequest,
  LayoutDashboard,
  ListChecks,
  Menu,
  ScanSearch,
  Settings,
  ShieldCheck,
  X,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { useMeta } from "@/lib/hooks/use-meta";
import { safeHttpUrl } from "@/lib/format";

const NAV_GROUPS: Array<{
  label: string;
  items: Array<{ href: string; label: string; icon: typeof LayoutDashboard }>;
}> = [
  {
    label: "Workspace",
    items: [
      { href: "/", label: "Overview", icon: LayoutDashboard },
      { href: "/repositories", label: "Repositories", icon: FolderGit2 },
      { href: "/reviews", label: "Reviews", icon: GitPullRequest },
      { href: "/findings", label: "Findings", icon: ScanSearch },
      { href: "/tests", label: "Test intelligence", icon: FlaskConical },
    ],
  },
  {
    label: "Insights",
    items: [
      { href: "/evaluation", label: "Evaluation", icon: BarChart3 },
      { href: "/jobs", label: "Jobs", icon: ListChecks },
    ],
  },
  {
    label: "System",
    items: [{ href: "/settings", label: "Settings", icon: Settings }],
  },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

function Brand() {
  return (
    <Link
      href="/"
      className="group flex items-center gap-2.5 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
    >
      <span className="grid size-8 place-items-center rounded-lg bg-gradient-to-br from-indigo-400 to-violet-600 shadow-lg shadow-indigo-950/40">
        <ScanSearch className="size-4.5 text-white" aria-hidden />
      </span>
      <span className="leading-tight">
        <span className="block text-sm font-semibold tracking-tight text-white">PatchLens</span>
        <span className="block text-[10px] font-medium uppercase tracking-[0.13em] text-slate-400">
          Review intelligence
        </span>
      </span>
    </Link>
  );
}

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { meta, error, loading } = useMeta();

  return (
    <div className="flex h-full flex-col">
      <div className="px-5 pb-5 pt-5">
        <Brand />
      </div>
      <nav aria-label="Primary" className="flex-1 overflow-y-auto px-3 pb-4">
        {NAV_GROUPS.map((group) => (
          <div key={group.label} className="mb-5">
            <p className="mb-1.5 px-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
              {group.label}
            </p>
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const active = isActive(pathname, item.href);
                const Icon = item.icon;
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      onClick={onNavigate}
                      aria-current={active ? "page" : undefined}
                      className={`group flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/30 ${
                        active
                          ? "bg-white/10 text-white shadow-sm"
                          : "text-slate-400 hover:bg-white/5 hover:text-slate-100"
                      }`}
                    >
                      <Icon
                        className={`size-4 ${active ? "text-indigo-300" : "text-slate-500 group-hover:text-slate-300"}`}
                        aria-hidden
                      />
                      <span className="flex-1">{item.label}</span>
                      {active ? <span className="size-1.5 rounded-full bg-indigo-400" aria-hidden /> : null}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>
      <div className="border-t border-white/10 p-4">
        <div className="rounded-lg border border-white/10 bg-white/[0.04] px-3 py-3">
          <div className="flex items-center gap-2">
            <span
              className={`size-1.5 rounded-full ${
                error ? "bg-rose-400" : meta ? "bg-emerald-400" : "bg-amber-300"
              }`}
              aria-hidden
            />
            <span className="text-[11px] font-medium text-slate-300">
              {error ? "API unreachable" : meta ? "API connected" : "Connecting…"}
            </span>
            {meta ? <span className="ml-auto font-mono text-[10px] text-slate-500">v{meta.version}</span> : null}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {loading ? (
              <span className="text-[11px] text-slate-500">Reading provider status…</span>
            ) : meta?.llm.is_mock ? (
              <Badge tone="warning" className="border-amber-400/20 bg-amber-400/10 text-amber-200">
                Mock provider
              </Badge>
            ) : (
              <Badge tone="success" className="border-emerald-400/20 bg-emerald-400/10 text-emerald-200">
                {meta?.llm.provider ?? "Provider"}
              </Badge>
            )}
            {meta?.demo_mode ? (
              <Badge tone="accent" className="border-indigo-400/20 bg-indigo-400/10 text-indigo-200">
                Demo mode
              </Badge>
            ) : null}
          </div>
          {meta ? (
            <p className="mt-2 line-clamp-2 text-[10px] leading-4 text-slate-500" title={meta.llm.model}>
              {meta.llm.is_mock
                ? "Deterministic findings. No AI model is invoked."
                : `Model: ${meta.llm.model}`}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function ProviderBanner() {
  const { meta, error, loading, reload } = useMeta();
  const docsUrl = safeHttpUrl(process.env.NEXT_PUBLIC_API_DOCS_URL);

  if (loading) {
    return (
      <div className="mb-5 h-10 animate-pulse rounded-lg border border-line bg-surface" aria-hidden />
    );
  }
  if (error) {
    return (
      <div className="mb-5 flex flex-wrap items-center gap-2 rounded-lg border border-danger/20 bg-danger-soft px-4 py-2.5 text-xs text-danger-strong">
        <ShieldCheck className="size-4" aria-hidden />
        <span className="font-medium">The PatchLens API is not responding.</span>
        <span className="opacity-80">Start it with <code className="font-mono">make run</code>.</span>
        <button
          type="button"
          onClick={reload}
          className="ml-auto rounded px-2 py-1 font-semibold underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger/40"
        >
          Retry
        </button>
      </div>
    );
  }
  if (!meta) return null;

  if (!meta.llm.is_mock && !meta.demo_mode) return null;

  return (
    <div className="mb-5 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-warning/25 bg-warning-soft px-4 py-2.5 text-xs text-warning-strong">
      <FlaskConical className="size-4 shrink-0" aria-hidden />
      {meta.demo_mode ? <span className="font-semibold">Demo mode.</span> : null}
      {meta.llm.is_mock ? (
        <span>
          <span className="font-semibold">Review model: deterministic mock.</span> No AI model is called;
          findings come from the rule engine and retrieval pipeline.
        </span>
      ) : null}
      <span className="flex items-center gap-3 sm:ml-auto">
        <Link href="/settings" className="font-semibold underline-offset-2 hover:underline">
          Provider settings
        </Link>
        {docsUrl ? (
          <a
            href={docsUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-0.5 font-semibold underline-offset-2 hover:underline"
          >
            API docs <ChevronRight className="size-3" aria-hidden />
          </a>
        ) : null}
      </span>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const pathname = usePathname();
  const [lastPathname, setLastPathname] = useState(pathname);

  // React's "adjust state during render" pattern: close the drawer when the
  // route changes without an extra effect/commit.
  if (pathname !== lastPathname) {
    setLastPathname(pathname);
    if (mobileOpen) setMobileOpen(false);
  }

  useEffect(() => {
    if (!mobileOpen) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileOpen(false);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [mobileOpen]);

  return (
    <div className="min-h-screen bg-canvas">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[60] focus:rounded-lg focus:bg-ink focus:px-3 focus:py-2 focus:text-sm focus:text-white"
      >
        Skip to content
      </a>

      <aside className="fixed inset-y-0 left-0 z-40 hidden w-[248px] bg-sidebar lg:block">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_20%_0%,rgba(99,102,241,0.16),transparent_42%)]" />
        <div className="relative h-full">
          <SidebarContent />
        </div>
      </aside>

      {mobileOpen ? (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            type="button"
            className="absolute inset-0 bg-ink/50 backdrop-blur-[2px]"
            aria-label="Close navigation"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="absolute inset-y-0 left-0 w-[280px] bg-sidebar shadow-2xl">
            <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_20%_0%,rgba(99,102,241,0.16),transparent_42%)]" />
            <div className="relative flex h-full flex-col">
              <button
                type="button"
                onClick={() => setMobileOpen(false)}
                className="absolute right-3 top-4 z-10 rounded-lg p-2 text-slate-400 hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
                aria-label="Close navigation"
              >
                <X className="size-4" aria-hidden />
              </button>
              <SidebarContent onNavigate={() => setMobileOpen(false)} />
            </div>
          </aside>
        </div>
      ) : null}

      <div className="lg:pl-[248px]">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-line bg-canvas/85 px-4 backdrop-blur-md sm:px-6 lg:px-8">
          <button
            type="button"
            onClick={() => setMobileOpen(true)}
            className="rounded-lg p-2 text-muted hover:bg-surface-muted hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 lg:hidden"
            aria-label="Open navigation"
            aria-expanded={mobileOpen}
          >
            <Menu className="size-5" aria-hidden />
          </button>
          <div className="flex min-w-0 items-center gap-2 text-xs text-muted">
            <Activity className="size-3.5 text-accent" aria-hidden />
            <span className="truncate font-medium text-ink">Review workspace</span>
            <span className="hidden text-line-strong sm:inline">/</span>
            <span className="hidden truncate sm:inline">Evidence before claims</span>
          </div>
          <div className="ml-auto flex items-center gap-2 text-xs text-muted">
            <span className="hidden rounded-full border border-line bg-surface px-2.5 py-1 sm:inline-flex">
              Local-first · Python
            </span>
            <Link
              href="/reviews/new"
              className="inline-flex h-8 items-center rounded-lg bg-ink px-3 text-xs font-semibold text-white transition-colors hover:bg-sidebar focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 focus-visible:ring-offset-2"
            >
              New review
            </Link>
          </div>
        </header>

        <main id="main-content" className="mx-auto w-full max-w-[1360px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          <ProviderBanner />
          {children}
          <footer className="mt-12 border-t border-line pt-5 text-xs text-muted">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p>
                PatchLens records how every finding was produced. Mock output is labelled everywhere.
              </p>
              <p className="font-mono text-[10px] text-muted/80">evidence · retrieval · verification</p>
            </div>
          </footer>
        </main>
      </div>
    </div>
  );
}
