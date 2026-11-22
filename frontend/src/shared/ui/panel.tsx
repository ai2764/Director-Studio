import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "./cn";

/** Base panel surface with 1px hairline border and light-mode card shadow. */
export function Panel({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-lg border border-border-subtle bg-panel text-ink shadow-[var(--shadow-card)]",
        className,
      )}
      {...props}
    />
  );
}

/** Panel top bar with hairline bottom divider. */
export function PanelHeader({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 border-b border-border-subtle px-4 py-2.5",
        className,
      )}
      {...props}
    />
  );
}

/** Compact panel title. */
export function PanelTitle({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3
      className={cn("text-[13px] font-semibold tracking-[-0.01em] text-ink", className)}
      {...props}
    />
  );
}

/** Panel body padding container. */
export function PanelBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-4", className)} {...props} />;
}

/** Semantic alias for Card surfaces. */
export const Card = Panel;

export interface SectionCardProps extends Omit<HTMLAttributes<HTMLElement>, "title"> {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}

/** Structured section card with optional header, description, and action slot. */
export function SectionCard({
  title,
  description,
  actions,
  className,
  children,
  ...props
}: SectionCardProps) {
  return (
    <section
      className={cn(
        "rounded-lg border border-border-subtle bg-panel text-ink shadow-[var(--shadow-card)]",
        className,
      )}
      {...props}
    >
      {title || description || actions ? (
        <div className="flex items-start justify-between gap-3 border-b border-border-subtle px-4 py-3">
          <div className="min-w-0">
            {title ? (
              <h2 className="text-[13px] font-semibold tracking-[-0.01em] text-ink">
                {title}
              </h2>
            ) : null}
            {description ? (
              <p className="mt-0.5 text-xs text-ink-secondary">{description}</p>
            ) : null}
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
        </div>
      ) : null}
      <div className="p-4">{children}</div>
    </section>
  );
}
