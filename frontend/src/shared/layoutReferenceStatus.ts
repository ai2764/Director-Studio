import type { LayoutReference } from "./api/types";

export function isRetiredLayout(layout: LayoutReference): boolean {
  return layout.review_status === "reject" || Boolean(layout.superseded_by);
}

export function isDisplayableLayout(layout: LayoutReference): boolean {
  return layout.job_status !== "failed" && layout.job_status !== "cancelled";
}
