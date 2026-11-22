import { forwardRef, type ButtonHTMLAttributes } from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "./cn";

export const buttonVariants = cva(
  [
    "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium",
    "transition-colors duration-150 select-none cursor-pointer",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1 focus-visible:ring-offset-canvas",
    "disabled:pointer-events-none disabled:opacity-45",
  ],
  {
    variants: {
      variant: {
        primary: "bg-accent text-accent-fg hover:bg-accent-hover shadow-xs",
        secondary:
          "border border-border-subtle bg-panel text-ink hover:bg-hover hover:border-border-strong shadow-[var(--shadow-card)]",
        outline:
          "border border-border-strong bg-transparent text-ink hover:bg-hover",
        ghost:
          "bg-transparent text-ink-secondary hover:bg-hover hover:text-ink",
        danger:
          "border border-status-danger/30 bg-status-danger-soft text-status-danger hover:bg-status-danger/20",
        "danger-solid":
          "bg-status-danger text-white hover:bg-status-danger/90 shadow-xs",
      },
      size: {
        xs: "h-6 px-2 text-[11px] leading-none rounded",
        sm: "h-7 px-2.5 text-xs leading-none",
        md: "h-8 px-3 text-[13px] leading-none",
        lg: "h-9 px-4 text-sm leading-none",
        icon: "h-7 w-7 p-0",
      },
    },
    defaultVariants: {
      variant: "secondary",
      size: "md",
    },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

/** Crisp Linear-style button supporting Radix Slot composition. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, type = "button", ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        ref={ref}
        type={asChild ? undefined : type}
        className={cn(buttonVariants({ variant, size }), className)}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";
