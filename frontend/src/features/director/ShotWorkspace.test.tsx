// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Shot } from "../../shared/api/types";
import { listLibraryAssets } from "../library/api";
import { ShotWorkspace } from "./ShotWorkspace";

const replaceShotMaterialsMock = vi.hoisted(() => vi.fn());

vi.mock("../library/api", () => ({
  listLibraryAssets: vi.fn(),
}));

vi.mock("./api", () => ({
  replaceShotMaterials: replaceShotMaterialsMock,
}));

function shot(id: string, title: string): Shot {
  return {
    id, title, project_id: "prj_1", scene_id: "sc_1", script_beat: `${title} beat`,
    duration_s: 5, status: "draft", refs: [], voice_refs: [], dialogue: [],
    prompt_sections: {
      subject_definitions: `${title} subject`, summary: "", retention_analysis: "",
      detailed_description: "", overall_soundscape: "", non_diegetic_music: "",
    },
    layout_asset_id: null, layout_review_status: null, ref_frame_job_id: null,
    layout_refs: [], h3_job_id: null, source_audio_path: null, feedback: "",
    blocked_reasons: [], meta: {},
  };
}

describe("ShotWorkspace", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listLibraryAssets).mockResolvedValue([]);
  });

  it("starts an Agent review after materials are saved from the Director Shot panel", async () => {
    const selected = shot("s1", "Arrival");
    selected.refs = [
      { role: "actor", asset_id: "act_1", file_key: "master", picture_index: 1 },
      { role: "layout_ref_frame", asset_id: "lay_1", file_key: "layout", picture_index: 2 },
    ];
    const updated = {
      ...selected,
      refs: [selected.refs[0]],
      meta: { material_review_pending: true },
    };
    replaceShotMaterialsMock.mockResolvedValue(updated);
    const onSend = vi.fn();

    render(
      <ShotWorkspace
        shots={[selected]}
        busy={false}
        onRegenerate={vi.fn()}
        onSend={onSend}
        onOpenImage={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Edit materials" }));
    fireEvent.click(await screen.findByRole("button", { name: "Remove Picture 2 · lay_1" }));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      expect(onSend).toHaveBeenCalledWith(expect.stringContaining(
        "Shot 01 references changed",
      ));
    });
  });

  it("does not repeat the Shot count in the workspace header", () => {
    const { container } = render(
      <ShotWorkspace
        shots={[shot("s1", "Arrival"), shot("s2", "Reveal")]}
        busy={false}
        onRegenerate={vi.fn()}
        onSend={vi.fn()}
        onOpenImage={vi.fn()}
      />,
    );

    expect(container.querySelector(".shot-workspace-header > span")).toBeNull();
    const board = screen.getByRole("img", { name: "Storyboard shot board" });
    expect(board.getAttribute("src")).toBe("/storyboard-shot-board.png");
    expect(screen.queryByText("Project storyboard")).toBeNull();
  });

  it("does not show an optional Layout issue as a blocking error when the H3 prompt is ready", () => {
    const selected = shot("s1", "Arrival");
    selected.status = "blocked";
    selected.refs = [
      { role: "actor", asset_id: "act_1", file_key: "master", picture_index: 1 },
    ];
    selected.prompt_sections = {
      subject_definitions: "subject",
      summary: "summary",
      retention_analysis: "retention",
      detailed_description: "detail",
      overall_soundscape: "sound",
      non_diegetic_music: "none",
    };
    selected.blocked_reasons = ["ref_frame requires a scene library ref"];

    const { container } = render(
      <ShotWorkspace
        shots={[selected]}
        busy={false}
        onRegenerate={vi.fn()}
        onSend={vi.fn()}
        onOpenImage={vi.fn()}
      />,
    );

    expect(screen.getAllByText("Ready for H3").length).toBeGreaterThan(0);
    expect(container.querySelector(".banner.error")).toBeNull();
  });

  it("renders every Shot section as one continuously readable design document", () => {
    render(
      <ShotWorkspace
        shots={[shot("s1", "Arrival"), shot("s2", "Reveal")]}
        busy={false}
        onRegenerate={vi.fn()}
        onSend={vi.fn()}
        onOpenImage={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Shot 2 · Reveal" }));
    expect(screen.getByRole("heading", { name: "2. Reveal" })).toBeTruthy();
    for (const section of [
      "Creative brief",
      "Cast & continuity",
      "Layout studies",
      "Generation prompt",
      "Production record",
    ]) {
      expect(screen.getByRole("heading", { name: section })).toBeTruthy();
    }
    expect(screen.getByRole("navigation", { name: "Shot document sections" })).toBeTruthy();
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.queryByText(/^\d+ versions?$/)).toBeNull();
    expect(screen.getByText("Reveal subject")).toBeTruthy();
    expect(screen.getByText("Reveal beat")).toBeTruthy();
  });

  it("frames the Shot header, navigation, and document as one clapperboard", () => {
    const { container } = render(
      <ShotWorkspace
        shots={[shot("s1", "Arrival")]}
        busy={false}
        onRegenerate={vi.fn()}
        onSend={vi.fn()}
        onOpenImage={vi.fn()}
      />,
    );

    const frame = container.querySelector(".shot-detail-panel.shot-detail-panel-clapperboard");
    const document = frame?.querySelector(".shot-document");
    expect(frame).toBeTruthy();
    expect(document?.contains(container.querySelector(".shot-detail-header"))).toBe(true);
    expect(document?.contains(screen.getByRole("navigation", { name: "Shot document sections" }))).toBe(true);
    expect(container.querySelector("#shot-brief .shot-brief-clapper-stripe")).toBeNull();
    expect(screen.queryByRole("button", { name: "Reference in chat" })).toBeNull();
  });

  it("labels each dialogue line with the matching character", () => {
    const selected = shot("s1", "Question");
    selected.dialogue = ["What am I directing?", "This opening."];
    selected.voice_refs = [
      {
        asset_id: "voice_agent",
        audio_index: 1,
        file_key: "reference",
        speaker: "Agent",
        notes: "Agent asks 'What am I directing?' with a curious delivery.",
      },
      {
        asset_id: "voice_mia",
        audio_index: 2,
        file_key: "reference",
        speaker: "Mia",
        notes: "Mia answers 'This opening.' with clipped finality.",
      },
    ];

    render(
      <ShotWorkspace
        shots={[selected]}
        busy={false}
        onRegenerate={vi.fn()}
        onSend={vi.fn()}
        onOpenImage={vi.fn()}
      />,
    );

    expect(screen.getByText("Agent").parentElement?.textContent).toBe(
      "AgentWhat am I directing?",
    );
    expect(screen.getByText("Mia").parentElement?.textContent).toBe(
      "MiaThis opening.",
    );
  });

  it("selecting a Shot does not expose the retired chat attachment action", () => {
    render(
      <ShotWorkspace
        shots={[shot("s1", "Arrival"), shot("s2", "Reveal")]}
        busy={false}
        onRegenerate={vi.fn()}
        onSend={vi.fn()}
        onOpenImage={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Shot 2 · Reveal" }));
    expect(screen.queryByRole("button", { name: "Reference in chat" })).toBeNull();
  });

  it("opens a Picture material editor for the selected Shot", async () => {
    const selected = shot("s1", "Arrival");
    selected.refs = [
      { role: "actor", asset_id: "act_1", file_key: "master", picture_index: 1 },
      { role: "layout_ref_frame", asset_id: "lay_1", file_key: "layout", picture_index: 2 },
    ];
    selected.layout_refs = [
      {
        id: "lref_1",
        provider: "comfy",
        asset_id: "lay_1",
        job_id: null,
        job_status: "succeeded",
        job_error: "",
        purpose: "arrival composition",
        state_description: "",
        time_hint: "",
        source_refs: [],
        review_status: "usable",
        review_feedback: "",
        feedback_source: "",
        feedback_quote: "",
        revision_of: null,
        superseded_by: null,
        selected_for_h3: true,
        activation_mode: "append",
        created_at: "2026-01-01T00:00:00Z",
      },
    ];

    render(
      <ShotWorkspace
        shots={[selected]}
        busy={false}
        onRegenerate={vi.fn()}
        onSend={vi.fn()}
        onOpenImage={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Edit materials" }));

    expect(await screen.findByRole("dialog", { name: "Edit Shot 01 materials" })).toBeTruthy();
    expect(screen.getByText("Pictures 2 / 9")).toBeTruthy();
    await waitFor(() => {
      expect(listLibraryAssets).toHaveBeenCalledWith("actors", "prj_1");
      expect(listLibraryAssets).toHaveBeenCalledWith("layouts", "prj_1");
    });
  });
});
