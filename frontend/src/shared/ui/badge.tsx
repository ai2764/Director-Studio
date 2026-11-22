import type { HTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "./cn";

export const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-tight whitespace-nowrap",
  {
    variants: {
      tone: {
        neutral: "border-border-subtle bg-elevated text-ink-secondary",
        accent: "border-accent/25 bg-accent-soft text-accent",
        ok: "border-status-ok/25 bg-status-ok-soft text-status-ok",
        warn: "border-status-warn/25 bg-status-warn-soft text-status-warn",
        danger: "border-status-danger/25 bg-status-danger-soft text-status-danger",
        info: "border-status-info/25 bg-status-info-soft text-status-info",
      },
    },
    defaultVariants: {
      tone: "neutral",
    },
  },
);

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

/** Low-saturation status badge pill. */
export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}

export interface StatusDotProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: "neutral" | "accent" | "ok" | "warn" | "danger" | "info";
  pulse?: boolean;
}

const DOT_TONES: Record<NonNullable<StatusDotProps["tone"]>, string> = {
  neutral: "bg-ink-muted",
  accent: "bg-accent",
  ok: "bg-status-ok",
  warn: "bg-status-warn",
  danger: "bg-status-danger",
  info: "bg-status-info",
};

/** 6px status indicator dot for dense status bars and cards. */
export function StatusDot({ tone = "neutral", pulse = false, className, ...props }: StatusDotProps) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "inline-block h-1.5 w-1.5 shrink-0 rounded-full",
        DOT_TONES[tone],
        pulse && "animate-pulse",
        className,
      )}
      {...props}
    />
  );
}
