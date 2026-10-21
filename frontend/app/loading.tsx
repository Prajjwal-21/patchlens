import { LoadingState } from "@/components/ui/states";

export default function Loading() {
  return (
    <div>
      <div className="mb-6 h-8 w-52 animate-pulse rounded bg-line" />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <div key={index} className="h-28 animate-pulse rounded-xl border border-line bg-surface" />
        ))}
      </div>
      <LoadingState rows={2} className="mt-6" />
    </div>
  );
}
