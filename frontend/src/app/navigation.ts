/**
 * App navigation — ordered as a production pipeline.
 * `comingSoon: true` shows a disabled nav chip without a page.
 */
export type NavId = "assets" | "director" | "production";

export interface NavItem {
  id: NavId;
  label: string;
  step: string;
}

export const NAV_ITEMS: NavItem[] = [
  { id: "assets", label: "Assets", step: "01" },
  { id: "director", label: "Director", step: "02" },
  { id: "production", label: "Production", step: "03" },
];
