import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from "react";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import { cn } from "./cn";

export const Tabs = TabsPrimitive.Root;

export const TabsList = forwardRef<
  ElementRef<typeof TabsPrimitive.List>,
  ComponentPropsWithoutRef<typeof TabsPrimitive.List> & { variant?: "segmented" | "underline" }
>(({ className, variant = "segmented", ...props }, ref) => (
  <TabsPrimitive.List
    ref={ref}
    className={cn(
      variant === "segmented"
        ? "inline-flex items-center gap-0.5 rounded-md border border-border-subtle bg-elevated p-0.5 text-ink-secondary"
        : "inline-flex items-center gap-4 border-b border-border-subtle text-ink-secondary",
      className,
    )}
    {...props}
  />
));
TabsList.displayName = "TabsList";

export const TabsTrigger = forwardRef<
  ElementRef<typeof TabsPrimitive.Trigger>,
  ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger> & { variant?: "segmented" | "underline" }
>(({ className, variant = "segmented", ...props }, ref) => (
  <TabsPrimitive.Trigger
    ref={ref}
    className={cn(
      "inline-flex items-center justify-center gap-1.5 whitespace-nowrap font-medium transition-colors cursor-pointer",
      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
      "disabled:pointer-events-none disabled:opacity-45",
      variant === "segmented"
        ? "h-6 rounded px-2.5 text-xs data-[state=active]:bg-panel data-[state=active]:text-ink data-[state=active]:shadow-[var(--shadow-card)] hover:text-ink"
        : "-mb-px border-b-2 border-transparent pb-2 text-xs data-[state=active]:border-accent data-[state=active]:text-ink hover:text-ink",
      className,
    )}
    {...props}
  />
));
TabsTrigger.displayName = "TabsTrigger";

export const TabsContent = forwardRef<
  ElementRef<typeof TabsPrimitive.Content>,
  ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Content
    ref={ref}
    className={cn("focus-visible:outline-none", className)}
    {...props}
  />
));
TabsContent.displayName = "TabsContent";
