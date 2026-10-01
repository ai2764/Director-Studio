/**
 * App navigation — ordered as a production pipeline.
 * `comingSoon: true` shows a disabled nav chip without a page.
 */
export type NavId = "assets" | "music" | "director" | "production";
/** Settings is application configuration, outside the numbered project stages. */
export type DesktopPage = NavId | "settings";

export interface NavItem {
  id: NavId;
  label: string;
  step: string;
}

export const NAV_ITEMS: NavItem[] = [
  { id: "assets", label: "Assets", step: "01" },
  { id: "music", label: "Music", step: "02" },
  { id: "director", label: "Director", step: "03" },
  { id: "production", label: "Production", step: "04" },
];

export function navigationForMode(mode?: string): NavItem[] {
  return NAV_ITEMS.filter((item) => item.id !== "music" || mode === "mv")
    .map((item, index) => ({ ...item, step: String(index + 1).padStart(2, "0") }));
}
