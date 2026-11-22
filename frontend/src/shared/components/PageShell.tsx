import type { ReactNode } from "react";
import { cn } from "../ui/cn";

type Props = {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  hideHeader?: boolean;
  /** full | wide content max-width */
  width?: "full" | "wide";
};

/** Consistent page chrome: title row + scrollable body. */
export function PageShell({
  title,
  subtitle,
  actions,
  children,
  className = "",
  hideHeader = false,
  width = "full",
}: Props) {
  return (
    <div
      className={cn(
        "page-shell flex min-h-0 flex-1 flex-col",
        width === "wide" && "page-shell-wide mx-auto w-full max-w-[1440px]",
        className,
      )}
    >
      {!hideHeader ? (
        <header className="page-shell-header flex shrink-0 items-center justify-between gap-4 border-b border-border-subtle bg-panel/70 px-5 py-3">
          <div className="page-shell-titles flex min-w-0 items-baseline gap-3">
            <h1 className="page-shell-title text-sm font-semibold tracking-[-0.015em] text-ink">
              {title}
            </h1>
            {subtitle ? (
              <div className="page-shell-sub truncate text-xs text-ink-muted">
                {subtitle}
              </div>
            ) : null}
          </div>
          {actions ? (
            <div className="page-shell-actions flex shrink-0 items-center gap-2">
              {actions}
            </div>
          ) : null}
        </header>
      ) : null}
      <div className="page-shell-body min-h-0 flex-1 overflow-y-auto p-5">{children}</div>
    </div>
  );
}
