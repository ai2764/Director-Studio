import { forwardRef, type TextareaHTMLAttributes } from "react";
import { cn } from "./cn";

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  hasError?: boolean;
}

/** Multi-line textarea styled to match Linear form density. */
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, hasError = false, ...props }, ref) => {
    return (
      <textarea
        ref={ref}
        aria-invalid={hasError || undefined}
        className={cn(
          "flex min-h-[76px] w-full rounded-md border bg-panel px-2.5 py-2 text-[13px] leading-relaxed text-ink",
          "placeholder:text-ink-muted transition-colors resize-y",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 focus-visible:border-accent",
          "disabled:cursor-not-allowed disabled:opacity-50",
          hasError ? "border-status-danger focus-visible:ring-status-danger/30" : "border-border-subtle hover:border-border-strong",
          className,
        )}
        {...props}
      />
    );
  },
);
Textarea.displayName = "Textarea";
