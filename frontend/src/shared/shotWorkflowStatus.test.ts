import { describe, expect, it } from "vitest";
import type { Shot } from "./api/types";
import { shotWorkflowStatus } from "./shotWorkflowStatus";

function succeededShot(meta: Record<string, unknown>): Shot {
  return {
    id: "sht_status",
    project_id: "prj_status",
    scene_id: "sc01",
    title: "The Wait",
    script_beat: "The Agent waits.",
    shot_type: "wide",
    camera_angle: "eye level",
    camera_motion: "locked-off",
    composition: "Agent left, empty chair right",
    duration_s: 6,
    status: "succeeded",
    refs: [
      {
        role: "actor",
        asset_id: "act_agent",
        file_key: "master",
        picture_index: 1,
      },
    ],
    voice_refs: [],
    prompt_sections: {
      subject_definitions: "subject",
      summary: "summary",
      retention_analysis: "retention",
      detailed_description: "detail",
      overall_soundscape: "sound",
      non_diegetic_music: "none",
    },
    dialogue: [],
    layout_asset_id: null,
    layout_review_status: null,
    ref_frame_job_id: null,
    layout_refs: [],
    h3_job_id: "job_old_success",
    source_audio_path: null,
    feedback: "",
    blocked_reasons: [],
    meta,
  };
}

describe("shotWorkflowStatus", () => {
  it("shows prompt refresh instead of an old H3 success after materials change", () => {
    const status = shotWorkflowStatus(
      succeededShot({
        prompt_picture_signature: "",
        material_review_pending: true,
      }),
    );

    expect(status).toEqual({
      key: "prompt-stale",
      label: "Prompt needs refresh",
    });
  });

  it("keeps the H3 success status after the refreshed prompt clears review", () => {
    const status = shotWorkflowStatus(
      succeededShot({
        prompt_picture_signature: "fresh-signature",
        material_review_pending: false,
      }),
    );

    expect(status).toEqual({ key: "h3-succeeded", label: "H3 succeeded" });
  });

  it("is ready for H3 when the prompt is complete even while an optional Layout runs", () => {
    const shot = succeededShot({ material_review_pending: false });
    shot.status = "ref_frame_pending";
    shot.h3_job_id = null;
    shot.ref_frame_job_id = "job_layout";
    shot.layout_refs = [{
      id: "lref_running",
      asset_id: null,
      job_id: "job_layout",
      job_status: "running",
      purpose: "optional composition",
      state_description: "",
      time_hint: "",
      source_refs: [],
      review_status: null,
      review_feedback: "",
      selected_for_h3: false,
      created_at: "2026-09-01T00:00:00Z",
    }];

    expect(shotWorkflowStatus(shot)).toEqual({ key: "ready-for-h3", label: "Ready for H3" });
  });

  it("is ready for H3 when only the optional Layout is blocked", () => {
    const shot = succeededShot({ material_review_pending: false });
    shot.status = "blocked";
    shot.h3_job_id = null;
    shot.blocked_reasons = [
      "ref_frame requires a scene library ref (generate Set Design plate first)",
    ];

    expect(shotWorkflowStatus(shot)).toEqual({ key: "ready-for-h3", label: "Ready for H3" });
  });

  it("asks for a prompt rather than references when both are absent", () => {
    const shot = succeededShot({ material_review_pending: false });
    shot.status = "draft";
    shot.h3_job_id = null;
    shot.refs = [];
    shot.prompt_sections = {
      subject_definitions: "",
      summary: "",
      retention_analysis: "",
      detailed_description: "",
      overall_soundscape: "",
      non_diegetic_music: "",
    };

    expect(shotWorkflowStatus(shot)).toEqual({ key: "needs-prompt", label: "Needs prompt" });
  });
});
