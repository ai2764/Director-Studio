import type { HTMLAttributes, ReactNode } from "react";
import { AlertCircle, CheckCircle2, Info, AlertTriangle } from "lucide-react";
import { cn } from "./cn";

export type BannerTone = "neutral" | "info" | "ok" | "warn" | "danger";

export interface BannerProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  tone?: BannerTone;
  title?: ReactNode;
  icon?: ReactNode;
}

const TONE_STYLES: Record<BannerTone, string> = {
  neutral: "border-border-subtle bg-elevated text-ink-secondary",
  info: "border-status-info/25 bg-status-info-soft text-status-info",
  ok: "border-status-ok/25 bg-status-ok-soft text-status-ok",
  warn: "border-status-warn/30 bg-status-warn-soft text-status-warn",
  danger: "border-status-danger/30 bg-status-danger-soft text-status-danger",
};

const TONE_ICONS: Record<BannerTone, ReactNode> = {
  neutral: <Info className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />,
  info: <Info className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />,
  ok: <CheckCircle2 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />,
  warn: <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />,
  danger: <AlertCircle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />,
};

/** Inline alert/status banner preserving semantic `role` defaults. */
export function Banner({
  tone = "neutral",
  title,
  icon,
  role,
  className,
  children,
  ...props
}: BannerProps) {
  const resolvedRole = role ?? (tone === "danger" || tone === "warn" ? "alert" : "status");
  return (
    <div
      role={resolvedRole}
      className={cn(
        "flex items-start gap-2 rounded-md border px-3 py-2 text-xs leading-relaxed",
        TONE_STYLES[tone],
        className,
      )}
      {...props}
    >
      <span className="mt-0.5 shrink-0">{icon ?? TONE_ICONS[tone]}</span>
      <div className="min-w-0 flex-1">
        {title ? <div className="font-semibold text-ink">{title}</div> : null}
        <div className={cn(title ? "mt-0.5" : "")}>{children}</div>
      </div>
    </div>
  );
}
