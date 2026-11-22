import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "./cn";

export interface EmptyStateProps extends HTMLAttributes<HTMLDivElement> {
  icon?: ReactNode;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
}

/** Quiet, centered empty-state placeholder for panels and lists. */
export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
  ...props
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border-subtle bg-elevated/40 px-6 py-8 text-center",
        className,
      )}
      {...props}
    >
      {icon ? (
        <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-border-subtle bg-panel text-ink-muted">
          {icon}
        </div>
      ) : null}
      <div className="text-[13px] font-medium text-ink">{title}</div>
      {description ? (
        <p className="max-w-sm text-xs leading-relaxed text-ink-secondary">{description}</p>
      ) : null}
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  );
}
