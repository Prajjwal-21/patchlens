import Link from "next/link";

import { buttonClasses } from "@/components/ui/button";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-xl rounded-xl border border-line bg-surface p-8 text-center">
      <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-muted">404</p>
      <h1 className="mt-2 text-xl font-semibold tracking-tight text-ink">That view does not exist</h1>
      <p className="mt-2 text-sm leading-6 text-muted">
        The page may have moved, or the review, finding or job id is unknown.
      </p>
      <Link href="/" className={buttonClasses("primary", "md", "mt-5")}>
        Back to overview
      </Link>
    </div>
  );
}
