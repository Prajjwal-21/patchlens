import type { ReactNode } from "react";

export function Table({ children, label }: { children: ReactNode; label: string }) {
  return (
    <div className="w-full overflow-x-auto">
      <table className="w-full min-w-[36rem] border-collapse text-left text-sm" aria-label={label}>
        {children}
      </table>
    </div>
  );
}

export function Th({ children, className = "" }: { children?: ReactNode; className?: string }) {
  return (
    <th
      scope="col"
      className={`border-b border-line bg-surface-muted/70 px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted ${className}`}
    >
      {children}
    </th>
  );
}

export function Td({ children, className = "" }: { children?: ReactNode; className?: string }) {
  return <td className={`border-b border-line/70 px-4 py-3 align-top text-sm ${className}`}>{children}</td>;
}

export function Tr({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <tr className={`transition-colors hover:bg-surface-muted/50 ${className}`}>{children}</tr>;
}
