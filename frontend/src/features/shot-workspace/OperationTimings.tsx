import { useEffect, useState } from "react";
import { cn } from "../../shared/ui";

export type OperationTiming = {
  id: string;
  stage: string;
  duration_s: number;
  status: string;
  finished_at: string;
};

export const timingLabel: Record<string, string> = {
  refinement: "Refine",
  assistant: "Assistant",
  prompt_planning: "Prompt planning",
  prompt: "Write prompt",
  reference_selection: "Select references",
};

export function useOperationTimings(shotId: string, active: boolean) {
  const [records, setRecords] = useState<OperationTiming[]>([]);
  useEffect(() => {
    let current = true;
    async function refresh() {
      try {
        const response = await fetch(`/api/shot-workspace/shots/${shotId}/timings`, { cache: "no-store" });
        if (!response.ok) return;
        const data = (await response.json()) as { records?: OperationTiming[] };
        if (current) setRecords((data.records || []).filter((item) => Number.isFinite(item.duration_s)));
      } catch {
        /* Timing display must not interrupt editing. */
      }
    }
    void refresh();
    const timer = setInterval(refresh, active ? 1500 : 15000);
    window.addEventListener("focus", refresh);
    return () => {
      current = false;
      clearInterval(timer);
      window.removeEventListener("focus", refresh);
    };
  }, [shotId, active]);
  return records;
}

export function TimingNote({ entry, className }: { entry?: OperationTiming; className?: string }) {
  if (!entry) return null;
  const label = timingLabel[entry.stage] || entry.stage;
  return (
    <small
      className={cn(
        "inline-flex items-center whitespace-nowrap rounded border border-border-subtle bg-elevated/80 px-1.5 py-0.5 font-mono text-[11px] font-normal leading-tight text-ink-muted",
        className,
      )}
      title={`${label}: ${entry.status}`}
    >
      {label} · {entry.duration_s.toFixed(1)}s{entry.status !== "completed" ? ` · ${entry.status}` : ""}
    </small>
  );
}
