import type { ReactNode } from "react";

type Props = {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
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
  width = "full",
}: Props) {
  return (
    <div className={`page-shell ${width === "wide" ? "page-shell-wide" : ""} ${className}`.trim()}>
      <header className="page-shell-header">
        <div className="page-shell-titles">
          <h1 className="page-shell-title">{title}</h1>
          {subtitle ? <div className="page-shell-sub">{subtitle}</div> : null}
        </div>
        {actions ? <div className="page-shell-actions">{actions}</div> : null}
      </header>
      <div className="page-shell-body">{children}</div>
    </div>
  );
}
