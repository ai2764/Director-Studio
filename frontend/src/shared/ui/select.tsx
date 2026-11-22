import { forwardRef, type SelectHTMLAttributes } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "./cn";

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "size"> {
  size?: "sm" | "md";
  wrapperClassName?: string;
  hasError?: boolean;
}

/**
 * Styled native `<select>` wrapper so standard form events and `fireEvent.change`
 * work identically while rendering Linear-grade chrome.
 */
export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, wrapperClassName, size = "md", hasError = false, children, ...props }, ref) => {
    return (
      <div className={cn("relative inline-flex items-center", wrapperClassName)}>
        <select
          ref={ref}
          aria-invalid={hasError || undefined}
          className={cn(
            "w-full appearance-none rounded-md border bg-panel pr-7 text-ink transition-colors cursor-pointer",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 focus-visible:border-accent",
            "disabled:cursor-not-allowed disabled:opacity-50",
            size === "sm" ? "h-7 pl-2 text-xs" : "h-8 pl-2.5 text-[13px]",
            hasError ? "border-status-danger" : "border-border-subtle hover:border-border-strong",
            className,
          )}
          {...props}
        >
          {children}
        </select>
        <ChevronDown
          className="pointer-events-none absolute right-2 h-3.5 w-3.5 text-ink-muted"
          aria-hidden="true"
        />
      </div>
    );
  },
);
Select.displayName = "Select";
