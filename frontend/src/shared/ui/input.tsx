import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "./cn";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  hasError?: boolean;
}

/** Single-line text input with hairline border and high-contrast focus ring. */
export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, type = "text", hasError = false, ...props }, ref) => {
    return (
      <input
        ref={ref}
        type={type}
        aria-invalid={hasError || undefined}
        className={cn(
          "flex h-8 w-full rounded-md border bg-panel px-2.5 py-1.5 text-[13px] text-ink",
          "placeholder:text-ink-muted transition-colors",
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
Input.displayName = "Input";
