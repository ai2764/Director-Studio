// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Shot } from "../../shared/api/types";
import { MobileShotDrawer } from "./MobileShotDrawer";

const shots: Shot[] = ["Arrival", "Reveal"].map((title, index) => ({
  id: `s${index + 1}`, project_id: "prj", scene_id: "sc", title,
  script_beat: `${title} beat`, duration_s: 5, status: "draft", refs: [], voice_refs: [],
  prompt_sections: { subject_definitions: "", summary: "", retention_analysis: "", detailed_description: "", overall_soundscape: "", non_diegetic_music: "" },
  dialogue: [], layout_asset_id: null, layout_review_status: null, ref_frame_job_id: null,
  layout_refs: [], h3_job_id: null, source_audio_path: null, feedback: "", blocked_reasons: [], meta: {},
}));

describe("MobileShotDrawer", () => {
  afterEach(cleanup);

  it("keeps the filmstrip collapsed until the user opens it", () => {
    render(<MobileShotDrawer shots={shots} onOpenImage={vi.fn()} />);

    const toggle = screen.getByRole("button", { name: "Shots, 2 planned" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("button", { name: "Shot 1 · Arrival" })).toBeNull();

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("button", { name: "Shot 1 · Arrival" })).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "Shot document sections" })).toBeTruthy();
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.getByRole("heading", { name: "Creative brief" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Cast & continuity" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Layout studies" })).toBeTruthy();
    expect(screen.getByText("Arrival beat")).toBeTruthy();
    expect(screen.getByText("No reusable assets assigned to this Shot.")).toBeTruthy();
    expect(screen.getByText("No Layouts generated for this Shot.")).toBeTruthy();
  });

  it("does not expose the retired chat attachment action", () => {
    render(<MobileShotDrawer shots={shots} onOpenImage={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Shots, 2 planned" }));
    fireEvent.click(screen.getByRole("button", { name: "Shot 2 · Reveal" }));
    expect(screen.queryByRole("button", { name: "Reference in chat" })).toBeNull();
  });

  it("keeps retired Layouts in a closed history section", () => {
    const withLayouts: Shot[] = [{
      ...shots[0],
      layout_refs: [
        {
          id: "lr_current", asset_id: "lay_current", job_id: "job_current",
          purpose: "current framing", state_description: "Current", time_hint: "",
          source_refs: [], review_status: "usable", review_feedback: "", selected_for_h3: true,
          created_at: "2026-09-01T00:00:00Z",
        },
        {
          id: "lr_old", asset_id: "lay_old", job_id: "job_old",
          purpose: "old framing", state_description: "Old", time_hint: "",
          source_refs: [], review_status: "usable", review_feedback: "", selected_for_h3: false,
          superseded_by: "lr_current", created_at: "2026-08-31T00:00:00Z",
        },
      ],
    }];
    render(<MobileShotDrawer shots={withLayouts} onOpenImage={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Shots, 1 planned" }));

    const history = screen.getByText("Previous layouts (1)")
      .closest("details") as HTMLDetailsElement;
    expect(history).toBeTruthy();
    expect(history.open).toBe(false);
    expect(history.textContent).toContain("old framing");
    expect(screen.getByText("current framing").closest("details.mobile-layout-history")).toBeNull();
  });

  it("does not render a failed Layout even when it has a partial asset id", () => {
    const failedOnly: Shot[] = [{
      ...shots[0],
      layout_refs: [{
        id: "lr_failed", asset_id: "lay_partial", job_id: "job_failed",
        job_status: "failed", job_error: "worker stopped",
        purpose: "failed framing", state_description: "Failed", time_hint: "",
        source_refs: [], review_status: null, review_feedback: "", selected_for_h3: false,
        created_at: "2026-09-01T00:00:00Z",
      }],
    }];
    render(<MobileShotDrawer shots={failedOnly} onOpenImage={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Shots, 1 planned" }));

    expect(screen.queryByText("failed framing")).toBeNull();
    expect(screen.getByText("No Layouts generated for this Shot.")).toBeTruthy();
  });
});
