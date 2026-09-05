// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Shot } from "../../shared/api/types";
import { LayoutReferenceList } from "./LayoutReferenceList";

vi.mock("./api", () => ({
  queueLayout: vi.fn(),
  reviewLayout: vi.fn(),
  selectLayout: vi.fn(),
}));

const baseShot: Shot = {
  id: "sht_layouts",
  project_id: "prj_test",
  scene_id: "sc01",
  title: "Doorway",
  script_beat: "The actor crosses the threshold.",
  duration_s: 6,
  status: "needs_review",
  refs: [
    {
      role: "layout_ref_frame",
      asset_id: "lay_before",
      picture_index: 4,
      file_key: "layout",
    },
  ],
  voice_refs: [],
  prompt_sections: {
    subject_definitions: "",
    summary: "",
    retention_analysis: "",
    detailed_description: "",
    overall_soundscape: "",
    non_diegetic_music: "",
  },
  dialogue: [],
  layout_asset_id: "lay_before",
  layout_review_status: "approved",
  ref_frame_job_id: null,
  h3_job_id: null,
  source_audio_path: null,
  feedback: "",
  blocked_reasons: [],
  meta: {},
  layout_refs: [
    {
      id: "lr_before",
      asset_id: "lay_before",
      job_id: "job_before",
      purpose: "before entry",
      state_description: "The threshold is clear.",
      time_hint: "before the actor enters",
      source_refs: [{ role: "scene", asset_id: "sc_hall", file_key: "master" }],
      review_status: "usable",
      review_feedback: "Keep the doorway framing.",
      feedback_source: "director_chat",
      feedback_quote: "Keep this doorway framing, but regenerate the actor position.",
      selected_for_h3: true,
      created_at: "2026-08-25T10:00:00Z",
    },
    {
      id: "lr_after",
      asset_id: "lay_after",
      job_id: "job_after",
      purpose: "post-entry blocking",
      state_description: "The actor blocks the far wall.",
      time_hint: "after entry",
      source_refs: [
        { role: "scene", asset_id: "sc_hall", file_key: "master" },
        { role: "actor", asset_id: "act_lead", file_key: "fullbody_threeview" },
      ],
      review_status: "usable",
      review_feedback: "",
      selected_for_h3: false,
      created_at: "2026-08-25T10:01:00Z",
    },
  ],
};

function renderList(
  shot: Shot,
  onDiscussAddReference = vi.fn(),
) {
  return render(
    <LayoutReferenceList
      shot={shot}
      busy={false}
      onDiscussAddReference={onDiscussAddReference}
      onOpenImage={vi.fn()}
    />,
  );
}

describe("LayoutReferenceList", () => {
  afterEach(cleanup);

  it("collapses completed Layout details until the user opens them", () => {
    const { container } = renderList(baseShot);
    const details = Array.from(
      container.querySelectorAll<HTMLDetailsElement>("details.layout-reference-details"),
    );

    expect(details).toHaveLength(2);
    expect(details.every((item) => !item.open)).toBe(true);

    fireEvent.click(details[0].querySelector("summary")!);
    expect(details[0].open).toBe(true);
  });

  it("keeps a Layout awaiting review expanded", () => {
    const { container } = renderList({
      ...baseShot,
      layout_refs: [
        {
          ...baseShot.layout_refs![0],
          review_status: "pending_review",
          selected_for_h3: false,
        },
      ],
    });

    expect(
      container.querySelector<HTMLDetailsElement>("details.layout-reference-details")?.open,
    ).toBe(true);
  });

  it("collects rejected and superseded Layouts in a closed history section", () => {
    renderList({
      ...baseShot,
      layout_refs: [
        ...baseShot.layout_refs!,
        {
          ...baseShot.layout_refs![0],
          id: "lr_rejected",
          asset_id: "lay_rejected",
          purpose: "rejected angle",
          review_status: "reject",
          selected_for_h3: false,
        },
        {
          ...baseShot.layout_refs![0],
          id: "lr_superseded",
          asset_id: "lay_superseded",
          purpose: "superseded repair",
          superseded_by: "lr_after",
          selected_for_h3: false,
        },
      ],
    });

    const history = screen.getByText("Previous layouts (2)")
      .closest("details") as HTMLDetailsElement;
    expect(history).toBeTruthy();
    expect(history.open).toBe(false);
    expect(history.querySelectorAll("article.layout-reference-card")).toHaveLength(2);
    expect(screen.getByRole("article", { name: "before entry" }).closest("details.layout-history")).toBeNull();
  });

  it("does not render failed or cancelled Layout records", () => {
    renderList({
      ...baseShot,
      layout_refs: [
        {
          ...baseShot.layout_refs![0],
          id: "lr_failed",
          asset_id: null,
          purpose: "failed framing",
          job_status: "failed",
          job_error: "worker stopped",
          review_status: null,
          selected_for_h3: false,
        },
        {
          ...baseShot.layout_refs![0],
          id: "lr_cancelled",
          asset_id: "lay_partial",
          purpose: "cancelled framing",
          job_status: "cancelled",
          review_status: null,
          selected_for_h3: false,
        },
        baseShot.layout_refs![1],
      ],
    });

    expect(screen.queryByRole("article", { name: "failed framing" })).toBeNull();
    expect(screen.queryByRole("article", { name: "cancelled framing" })).toBeNull();
    expect(screen.getByRole("article", { name: "post-entry blocking" })).toBeTruthy();
    expect(screen.queryByText(/Reference generation failed/i)).toBeNull();
  });

  it("distinguishes the current submitted Layout from historical alternatives", () => {
    renderList(baseShot);

    expect(screen.getByText("before entry")).toBeTruthy();
    expect(screen.getByText("post-entry blocking")).toBeTruthy();
    expect(screen.getAllByText("Used automatically in prompt and H3")).toHaveLength(1);
    expect(screen.getByText("Previous Layout — not submitted")).toBeTruthy();
    expect(screen.queryByText("Selected for H3")).toBeNull();
    expect(screen.queryByText("Not selected for H3")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("uses the synchronized Shot ref for the Picture badge", () => {
    renderList(baseShot);

    expect(screen.getByText("Picture 4")).toBeTruthy();
    expect(screen.queryByText("Picture 1")).toBeNull();
  });

  it("does not offer H3 selection for a rejected Layout", () => {
    renderList({
      ...baseShot,
      layout_refs: [
        {
          ...baseShot.layout_refs![0],
          id: "lr_rejected",
          asset_id: "lay_rejected",
          purpose: "rejected frame",
          review_status: "reject",
          selected_for_h3: false,
        },
      ],
    });

    expect(screen.queryByLabelText("Use rejected frame in H3")).toBeNull();
  });

  it("keeps QC evidence visible without exposing manual review controls", () => {
    renderList(baseShot);

    expect(screen.getByRole("article", { name: "before entry" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "before entry" })).toBeTruthy();
    expect(screen.getByText("Keep the doorway framing.")).toBeTruthy();
    expect(screen.getByText("From Director chat")).toBeTruthy();
    expect(screen.queryByText(/^Review before entry$/)).toBeNull();
    expect(screen.queryByLabelText("Feedback for before entry")).toBeNull();
    expect(screen.queryByRole("button", { name: "Mark before entry usable" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject before entry" })).toBeNull();
  });

  it("starts a Director discussion instead of opening a manual generation form", () => {
    const onDiscussAddReference = vi.fn();
    renderList(baseShot, onDiscussAddReference);

    fireEvent.click(screen.getByRole("button", { name: "Add reference frame" }));
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Show the moment after the second actor enters." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Discuss with Director" }));

    expect(onDiscussAddReference).toHaveBeenCalledWith(
      "Show the moment after the second actor enters.",
    );
    expect(screen.queryByLabelText("Purpose")).toBeNull();
    expect(screen.queryByLabelText("Time hint")).toBeNull();
    expect(screen.queryByRole("button", { name: "Queue reference frame" })).toBeNull();
  });

  it("shows a tail-frame origin label when origin.kind is clip_tail_frame", () => {
    renderList({
      ...baseShot,
      layout_refs: [
        {
          ...baseShot.layout_refs![0],
          purpose: "continuity from prior clip",
          origin: {
            kind: "clip_tail_frame",
            source_shot_id: "shot2",
            source_job_id: "job_src",
            source_generation: 2,
            output_kind: "enhanced",
            output_key: "video",
            source_filename: "enhanced.mp4",
          },
        },
      ],
    });

    expect(screen.getByText("Tail frame · shot2 · v2 · enhanced")).toBeTruthy();
  });
});
