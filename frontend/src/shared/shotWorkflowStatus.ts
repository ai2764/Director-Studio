import {
  PROMPT_SECTION_KEYS,
  type PromptSections,
  type Shot,
} from "./api/types";

export type ShotWorkflowStatus = {
  key: string;
  label: string;
};

export function promptReady(sections: PromptSections | null | undefined): boolean {
  if (!sections) return false;
  return PROMPT_SECTION_KEYS.every(({ key }) => Boolean((sections[key] || "").trim()));
}

export function shotWorkflowStatus(shot: Shot): ShotWorkflowStatus {
  if (shot.h3_job_id) {
    if (shot.status === "queued") return { key: "h3-queued", label: "H3 queued" };
    if (shot.status === "running") return { key: "h3-running", label: "H3 running" };
  }

  if (shot.meta?.material_review_pending === true) {
    return { key: "prompt-stale", label: "Prompt needs refresh" };
  }

  if (shot.h3_job_id) {
    if (shot.status === "failed") return { key: "h3-failed", label: "H3 failed" };
    if (shot.status === "succeeded") return { key: "h3-succeeded", label: "H3 succeeded" };
  }

  if (promptReady(shot.prompt_sections)) {
    return { key: "ready-for-h3", label: "Ready for H3" };
  }
  if (shot.status === "blocked" || shot.blocked_reasons.length > 0) {
    return { key: "blocked", label: "Blocked" };
  }
  return { key: "needs-prompt", label: "Needs prompt" };
}
