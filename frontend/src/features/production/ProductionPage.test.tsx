// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ProjectDetail, PromptSections, Shot } from "../../shared/api/types";
import { ProductionPage } from "./ProductionPage";
import { deleteLayout, getH3Job, getProject, patchShot, submitShot } from "./api";
import { listLibraryAssets } from "../library/api";

const replaceShotMaterialsMock = vi.hoisted(() => vi.fn());
const getH3ProviderStatusMock = vi.hoisted(() => vi.fn());
const fetchH3ProfilesMock = vi.hoisted(() => vi.fn());
vi.mock("../../shared/api/client", () => ({ fetchH3Profiles: fetchH3ProfilesMock }));

let currentProjectId = "prj_test";

vi.mock("../../shared/project/ProjectContext", () => ({
  useProject: () => ({ projectId: currentProjectId }),
}));

vi.mock("./api", () => ({
  getProject: vi.fn(),
  cancelH3Job: vi.fn(),
  deleteLayout: vi.fn(),
  getH3Job: vi.fn(),
  getH3ProviderStatus: getH3ProviderStatusMock,
  insertLayoutRefFrame: vi.fn(),
  patchShot: vi.fn(),
  skipLayout: vi.fn(),
  submitShot: vi.fn(),
}));

vi.mock("../library/api", () => ({
  listLibraryAssets: vi.fn(),
}));

vi.mock("../director/api", () => ({
  replaceShotMaterials: replaceShotMaterialsMock,
}));

const emptyPrompt: PromptSections = {
  subject_definitions: "",
  summary: "",
  retention_analysis: "",
  detailed_description: "",
  overall_soundscape: "",
  non_diegetic_music: "",
};

const generatedPrompt: PromptSections = {
  subject_definitions: "The actor in the approved wardrobe.",
  summary: "The actor enters the casting hallway.",
  retention_analysis: "Forward motion creates the visual hook.",
  detailed_description: "A locked wide shot with natural corridor lighting.",
  overall_soundscape: "Soft footsteps and room tone.",
  non_diegetic_music: "Minimal restrained pulse.",
};

function shot(prompt_sections: PromptSections): Shot {
  return {
    id: "sht_1",
    project_id: "prj_test",
    scene_id: "sc01",
    title: "Corridor walk-in",
    script_beat: "The actor enters.",
    duration_s: 6,
    status: "needs_review",
    refs: [],
    voice_refs: [],
    prompt_sections,
    dialogue: [],
    layout_asset_id: null,
    layout_review_status: "approved",
    ref_frame_job_id: null,
    layout_refs: [],
    h3_job_id: null,
    source_audio_path: null,
    feedback: "",
    blocked_reasons: [],
    meta: {},
  };
}

function detail(currentShot: Shot): ProjectDetail {
  return {
    project: {
      id: "prj_test",
      name: "Test project",
      script_text: "INT. HALLWAY - DAY",
      mode: "director",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      shot_ids: [currentShot.id],
    },
    shots: [currentShot],
  };
}

describe("ProductionPage prompt refresh", () => {
  afterEach(cleanup);

  beforeEach(() => {
    fetchH3ProfilesMock.mockResolvedValue({ active: { display_name: "My H3 Quality Profile", source: "custom", warning: null }, profiles: [] });
    vi.clearAllMocks();
    currentProjectId = "prj_test";
    vi.mocked(listLibraryAssets).mockResolvedValue([]);
    getH3ProviderStatusMock.mockResolvedValue({
      default_provider: "local",
      minimax_configured: true,
      minimax_resolution: "768P",
    });
  });

  it("shows the resolved custom workflow in Production", async () => {
    vi.mocked(getProject).mockResolvedValue(detail(shot(emptyPrompt)));
    render(<ProductionPage active />);
    expect(await screen.findByText("Workflow: My H3 Quality Profile")).toBeTruthy();
    expect(screen.getByText("Local · ComfyUI — My H3 Quality Profile")).toBeTruthy();
  });

  it("discloses fallback without blocking the Production workspace", async () => {
    fetchH3ProfilesMock.mockResolvedValue({ active: { display_name: "Built-in Official H3", source: "builtin", warning: { code: "custom_profile_unavailable", message: "Custom workflow hash changed" } }, profiles: [] });
    vi.mocked(getProject).mockResolvedValue(detail(shot(emptyPrompt)));
    render(<ProductionPage active />);
    expect(await screen.findByText("Using Built-in Official H3")).toBeTruthy();
    expect(screen.getByText("Custom workflow hash changed")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Production" })).toBeTruthy();
  });

  it("submits the selected H3 provider for one Production run", async () => {
    const ready = {
      ...shot(generatedPrompt),
      refs: [
        { role: "actor" as const, asset_id: "act_mia", picture_index: 1, file_key: "master" },
      ],
    };
    vi.mocked(getProject).mockResolvedValue(detail(ready));
    vi.mocked(submitShot).mockResolvedValue({
      ...ready,
      status: "queued",
      h3_job_id: "job_api_h3",
    });
    vi.mocked(getH3Job).mockResolvedValue({
      id: "job_api_h3",
      status: "queued",
      name: "h3:Corridor walk-in",
      notes: "The actor enters.",
      prompt: "production prompt",
      dialogue: [],
      frames: 90,
      error: null,
      comfy_prompt_id: null,
      external_task_id: "task_api_h3",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      outputs: {},
      input_previews: {},
      pipeline_id: "h3_ref2va",
      h3_provider: "minimax",
    });

    render(<ProductionPage active />);
    fireEvent.click(await screen.findByText("Corridor walk-in"));
    fireEvent.click(screen.getByRole("tab", { name: "Run H3" }));
    fireEvent.change(screen.getByRole("combobox", { name: "H3 provider" }), {
      target: { value: "minimax" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Submit H3" }));

    await waitFor(() => {
      expect(submitShot).toHaveBeenCalledWith("sht_1", "minimax", undefined);
    });
  });

  it("ignores a late not-found response from the previously selected project", async () => {
    let rejectStale!: (reason: Error) => void;
    const staleRequest = new Promise<ProjectDetail>((_resolve, reject) => {
      rejectStale = reject;
    });
    vi.mocked(getProject).mockImplementation((projectId) =>
      projectId === "prj_stale"
        ? staleRequest
        : Promise.resolve(detail(shot(generatedPrompt))),
    );

    currentProjectId = "prj_stale";
    const { rerender } = render(<ProductionPage active />);
    currentProjectId = "prj_test";
    rerender(<ProductionPage active />);

    await screen.findByText("Corridor walk-in");
    await act(async () => rejectStale(new Error("Project not found")));

    expect(screen.queryByText("Project not found")).toBeNull();
  });

  it("renders H3 submission in the Production action surface", async () => {
    vi.mocked(getProject).mockResolvedValue(detail(shot(generatedPrompt)));

    const { container } = render(<ProductionPage active />);
    fireEvent.click(await screen.findByText("Corridor walk-in"));
    fireEvent.click(screen.getByRole("tab", { name: "Run H3" }));

    expect(container.querySelector(".production-submit-actions")).toBeTruthy();
  });

  it("renders a read-only mobile Shot review with the final result first", async () => {
    const completed = {
      ...shot(generatedPrompt),
      status: "succeeded" as const,
      h3_job_id: "job_h3",
      layout_asset_id: "lay_1",
      shot_type: "medium close-up",
      camera_angle: "eye-level",
      camera_motion: "locked-off",
      composition: "Mia holds the Agent's eyeline across the table.",
      dialogue: ["You want the job?"],
      refs: [
        { role: "actor" as const, asset_id: "act_mia", picture_index: 1, file_key: "master" },
        { role: "layout_ref_frame" as const, asset_id: "lay_1", picture_index: 2, file_key: "layout" },
      ],
    };
    vi.mocked(getProject).mockResolvedValue(detail(completed));
    vi.mocked(getH3Job).mockResolvedValue({
      id: "job_h3",
      status: "succeeded",
      name: "h3:Corridor walk-in",
      notes: "The actor enters.",
      prompt: "production prompt",
      dialogue: ["You want the job?"],
      frames: 90,
      error: null,
      comfy_prompt_id: "prompt_1",
      external_task_id: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:01:00Z",
      outputs: {
        video: {
          key: "video",
          label: "Enhanced video",
          path: "C:/outputs/video.mp4",
          filename: "video.mp4",
          url: "/api/files/jobs/job_h3/outputs/video.mp4",
        },
      },
      input_previews: {},
      pipeline_id: "h3_ref2va",
    });

    const { container } = render(<ProductionPage active mobile />);

    expect(await screen.findByRole("button", { name: "Shot 01 · Corridor walk-in" })).toBeTruthy();
    expect(await screen.findByRole("heading", { name: "Final result" })).toBeTruthy();
    await waitFor(() => {
      expect(container.querySelector('video[src="/api/files/jobs/job_h3/outputs/video.mp4"]')).toBeTruthy();
    });
    expect(screen.getByRole("heading", { name: "Shot design" })).toBeTruthy();
    expect(screen.getByText("medium close-up")).toBeTruthy();
    expect(screen.getByText("Mia holds the Agent's eyeline across the table.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "References" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "H3 prompt" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Submit H3" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Save prompt" })).toBeNull();
  });

  it("opens the selected Shot's material editor from mobile Production references", async () => {
    const withReferences = {
      ...shot(generatedPrompt),
      refs: [
        { role: "actor" as const, asset_id: "act_mia", picture_index: 1, file_key: "master" },
      ],
    };
    vi.mocked(getProject).mockResolvedValue(detail(withReferences));

    render(<ProductionPage active mobile />);

    fireEvent.click(await screen.findByRole("button", { name: "Edit materials" }));
    expect(await screen.findByRole("dialog", { name: "Edit Shot 01 materials" })).toBeTruthy();
  });

  it("refreshes mobile Production references after saving material changes", async () => {
    const onReviewMaterials = vi.fn();
    const withReference = {
      ...shot(generatedPrompt),
      refs: [
        { role: "actor" as const, asset_id: "act_mia", picture_index: 1, file_key: "master" },
      ],
    };
    vi.mocked(getProject).mockResolvedValue(detail(withReference));
    const updated = { ...withReference, refs: [] };
    replaceShotMaterialsMock.mockResolvedValueOnce(updated);
    render(<ProductionPage active mobile onReviewMaterials={onReviewMaterials} />);

    fireEvent.click(await screen.findByRole("button", { name: "Edit materials" }));
    fireEvent.click(await screen.findByRole("button", { name: "Remove Picture 1 · act_mia" }));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("No references attached.")).toBeTruthy();
    expect(onReviewMaterials).toHaveBeenCalledWith(updated, 1);
  });

  it("runs a ready Shot from the mobile Production result surface", async () => {
    const ready = {
      ...shot(generatedPrompt),
      refs: [
        { role: "actor" as const, asset_id: "act_mia", picture_index: 1, file_key: "master" },
      ],
    };
    const queued = {
      ...ready,
      status: "queued" as const,
      h3_job_id: "job_mobile_h3",
    };
    vi.mocked(getProject).mockResolvedValue(detail(ready));
    vi.mocked(submitShot).mockResolvedValue(queued);
    vi.mocked(getH3Job).mockResolvedValue({
      id: "job_mobile_h3",
      status: "queued",
      name: "h3:Corridor walk-in",
      notes: "The actor enters.",
      prompt: "production prompt",
      dialogue: [],
      frames: 90,
      error: null,
      comfy_prompt_id: null,
      external_task_id: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      outputs: {},
      input_previews: {},
      pipeline_id: "h3_ref2va",
    });

    render(<ProductionPage active mobile />);

    const runButton = await screen.findByRole("button", { name: "Run H3" });
    expect(runButton.hasAttribute("disabled")).toBe(false);
    fireEvent.click(runButton);

    expect(await screen.findByRole("button", { name: "H3 running…" })).toBeTruthy();
  });

  it("does not show the legacy layout approval column or state", async () => {
    vi.mocked(getProject).mockResolvedValue(detail(shot(generatedPrompt)));

    render(<ProductionPage active />);
    await screen.findByText("Corridor walk-in");

    expect(screen.queryByRole("columnheader", { name: "Layout" })).toBeNull();
    expect(screen.queryByText("approved")).toBeNull();
  });

  it("does not offer manual insert or skip actions when no Layout exists", async () => {
    vi.mocked(getProject).mockResolvedValue(detail(shot(generatedPrompt)));

    render(<ProductionPage active />);
    fireEvent.click(await screen.findByText("Corridor walk-in"));

    expect(screen.queryByText("Insert image…")).toBeNull();
    expect(screen.queryByText("Skip layout")).toBeNull();
  });

  it("deletes an unwanted generated Layout from the selected Shot", async () => {
    const withLayout = {
      ...shot(generatedPrompt),
      layout_asset_id: "lay_delete",
      layout_review_status: "pending_review",
      layout_refs: [
        {
          id: "lref_delete",
          asset_id: "lay_delete",
          job_id: "job_layout",
          job_status: "succeeded" as const,
          purpose: "unwanted composition",
          state_description: "Wrong blocking.",
          time_hint: "opening",
          source_refs: [],
          review_status: "pending_review" as const,
          review_feedback: "",
          selected_for_h3: false,
          created_at: "2026-08-29T00:00:00Z",
        },
      ],
    };
    const withoutLayout = {
      ...withLayout,
      layout_asset_id: null,
      layout_review_status: null,
      layout_refs: [],
    };
    vi.mocked(getProject).mockResolvedValue(detail(withLayout));
    vi.mocked(deleteLayout).mockResolvedValue(withoutLayout);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<ProductionPage active />);
    fireEvent.click(await screen.findByText("Corridor walk-in"));
    fireEvent.click(screen.getByRole("button", { name: "Delete Layout" }));

    await waitFor(() =>
      expect(deleteLayout).toHaveBeenCalledWith("sht_1", "lref_delete"),
    );
  });

  it("shows workflow-oriented Production status instead of the stale shot status", async () => {
    const awaitingQc = {
      ...shot(emptyPrompt),
      id: "sht_qc",
      title: "Awaiting reference QC",
      status: "needs_review" as const,
      layout_asset_id: "lay_pending",
      layout_review_status: "pending_review",
    };
    const ready = {
      ...shot(generatedPrompt),
      id: "sht_ready",
      title: "Ready shot",
      status: "needs_review" as const,
      refs: [
        { role: "actor" as const, asset_id: "act_1", picture_index: 1 },
      ],
      layout_asset_id: "lay_selected",
      layout_review_status: "usable",
      layout_refs: [
        {
          id: "lr_selected",
          asset_id: "lay_selected",
          job_id: "job_selected",
          job_status: "succeeded" as const,
          purpose: "selected blocking",
          state_description: "The approved spatial relationship.",
          time_hint: "opening",
          source_refs: [],
          review_status: "usable" as const,
          review_feedback: "",
          selected_for_h3: true,
          created_at: "2026-08-26T10:00:00Z",
        },
        {
          id: "lr_pending_extra",
          asset_id: "lay_pending_extra",
          job_id: "job_pending_extra",
          job_status: "succeeded" as const,
          purpose: "optional alternate",
          state_description: "An alternate that is not selected for H3.",
          time_hint: "later",
          source_refs: [],
          review_status: "pending_review" as const,
          review_feedback: "",
          selected_for_h3: false,
          created_at: "2026-08-26T10:01:00Z",
        },
      ],
    };
    vi.mocked(getProject).mockResolvedValue({
      ...detail(awaitingQc),
      project: {
        ...detail(awaitingQc).project,
        shot_ids: [awaitingQc.id, ready.id],
      },
      shots: [awaitingQc, ready],
    });

    render(<ProductionPage active />);

    expect((await screen.findAllByText("Needs prompt")).length).toBeGreaterThan(0);
    expect(screen.getByText("Ready for H3")).toBeTruthy();
    expect(screen.queryByText("Reference QC")).toBeNull();
    expect(screen.queryByText("reference frame review")).toBeNull();
  });

  it("loads a generated prompt when the Production tab becomes active", async () => {
    vi.mocked(getProject)
      .mockResolvedValueOnce(detail(shot(emptyPrompt)))
      .mockResolvedValueOnce(detail(shot(generatedPrompt)));

    const { rerender } = render(<ProductionPage active={false} />);
    fireEvent.click(await screen.findByText("Corridor walk-in"));
    fireEvent.click(screen.getByRole("tab", { name: "Prompt" }));
    expect(
      (screen.getByLabelText("Summary") as HTMLTextAreaElement).value,
    ).toBe("");

    rerender(<ProductionPage active />);

    await waitFor(() =>
      expect(
        (screen.getByLabelText("Summary") as HTMLTextAreaElement).value,
      ).toBe("The actor enters the casting hallway."),
    );
  });

  it("preserves unsaved prompt edits during an activation refresh", async () => {
    const remoteRewrite = {
      ...generatedPrompt,
      summary: "A newer server-side summary.",
    };
    const refreshedShot = {
      ...shot(remoteRewrite),
      title: "Updated corridor",
    };
    vi.mocked(getProject)
      .mockResolvedValueOnce(detail(shot(generatedPrompt)))
      .mockResolvedValueOnce(detail(refreshedShot));

    const { rerender } = render(<ProductionPage active={false} />);
    fireEvent.click(await screen.findByText("Corridor walk-in"));
    fireEvent.click(screen.getByRole("tab", { name: "Prompt" }));
    const summary = screen.getByLabelText("Summary") as HTMLTextAreaElement;
    fireEvent.change(summary, { target: { value: "My unsaved manual edit." } });

    rerender(<ProductionPage active />);
    await screen.findAllByText("Updated corridor");
    await act(async () => {
      await Promise.resolve();
    });

    expect((screen.getByLabelText("Summary") as HTMLTextAreaElement).value).toBe(
      "My unsaved manual edit.",
    );
  });

  it("shows ordered playable Voice references in the Refs tab", async () => {
    const current = {
      ...shot(generatedPrompt),
      voice_refs: [
        {
          asset_id: "voi_mia",
          audio_index: 1,
          file_key: "reference",
          speaker: "Mia",
          notes: "name match",
        },
      ],
    };
    vi.mocked(getProject).mockResolvedValue(detail(current));
    vi.mocked(listLibraryAssets).mockResolvedValue([
      {
        id: "voi_mia",
        kind: "voices",
        name: "Mia",
        notes: "Warm neutral English",
        pipeline_id: "external",
        job_id: "",
        seed: null,
        created_at: "2026-08-25T00:00:00Z",
        files: { reference: "reference.wav" },
        meta: { duration_s: 4, h3_ready: true },
        urls: { reference: "/voice/mia.wav" },
        project_id: "prj_test",
      },
    ]);

    const { container } = render(<ProductionPage active />);
    fireEvent.click(await screen.findByText("Corridor walk-in"));
    fireEvent.click(screen.getByRole("tab", { name: "Refs" }));

    expect(await screen.findByText("Audio 1")).toBeTruthy();
    expect(screen.getByDisplayValue("Mia")).toBeTruthy();
    expect(screen.getByText("Warm neutral English")).toBeTruthy();
    expect(screen.getByText("Prompt will refresh before H3 submission.")).toBeTruthy();
    expect(container.querySelector('audio[src="/voice/mia.wav"]')).toBeTruthy();
  });

  it("saves a manual Voice selection with contiguous Audio order", async () => {
    const current = shot(generatedPrompt);
    const mia = {
      id: "voi_mia",
      kind: "voices",
      name: "Mia",
      notes: "Warm neutral English",
      pipeline_id: "external",
      job_id: "",
      seed: null,
      created_at: "2026-08-25T00:00:00Z",
      files: { reference: "reference.wav" },
      meta: { duration_s: 4, h3_ready: true },
      urls: { reference: "/voice/mia.wav" },
      project_id: "prj_test",
    };
    vi.mocked(getProject).mockResolvedValue(detail(current));
    vi.mocked(listLibraryAssets).mockResolvedValue([mia]);
    vi.mocked(patchShot).mockResolvedValue({
      ...current,
      voice_refs: [
        { asset_id: "voi_mia", audio_index: 1, file_key: "reference", speaker: "Mia" },
      ],
    });

    render(<ProductionPage active />);
    fireEvent.click(await screen.findByText("Corridor walk-in"));
    fireEvent.click(screen.getByRole("tab", { name: "Refs" }));
    fireEvent.change(await screen.findByLabelText("Add Voice reference"), {
      target: { value: "voi_mia" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add Voice" }));
    fireEvent.click(screen.getByRole("button", { name: "Save Voice refs" }));

    await waitFor(() =>
      expect(patchShot).toHaveBeenCalledWith("sht_1", {
        voice_refs: [
          {
            asset_id: "voi_mia",
            audio_index: 1,
            file_key: "reference",
            speaker: "Mia",
            notes: "",
          },
        ],
      }),
    );
  });

  it("disables Voice overrides when exact source audio controls the run", async () => {
    vi.mocked(getProject).mockResolvedValue(
      detail({ ...shot(generatedPrompt), source_audio_path: "C:/audio/exact.wav" }),
    );

    render(<ProductionPage active />);
    fireEvent.click(await screen.findByText("Corridor walk-in"));
    fireEvent.click(screen.getByRole("tab", { name: "Refs" }));

    expect(await screen.findByText("Exact source audio controls this run")).toBeTruthy();
    expect(
      (screen.getByLabelText("Add Voice reference") as HTMLSelectElement).disabled,
    ).toBe(true);
  });

  it("submits the selected H3 resolution preset", async () => {
    const ready = {
      ...shot(generatedPrompt),
      refs: [{ role: "actor" as const, asset_id: "act_1", picture_index: 1 }],
    };
    vi.mocked(getProject).mockResolvedValue(detail(ready));
    vi.mocked(submitShot).mockResolvedValue({
      ...ready,
      status: "queued",
      h3_job_id: "job_720",
    });
    vi.mocked(getH3Job).mockResolvedValue({
      id: "job_720",
      status: "queued",
      name: "h3:Corridor walk-in",
      notes: "The actor enters.",
      prompt: "production prompt",
      dialogue: [],
      frames: 124,
      error: null,
      comfy_prompt_id: null,
      external_task_id: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      outputs: {},
      input_previews: {},
      pipeline_id: "h3_ref2va",
    });

    render(<ProductionPage active />);
    fireEvent.click(await screen.findByText("Corridor walk-in"));
    fireEvent.click(screen.getByRole("tab", { name: "Run H3" }));
    fireEvent.change(screen.getByLabelText("Resolution"), {
      target: { value: "landscape-720" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Submit H3" }));

    await waitFor(() =>
      expect(submitShot).toHaveBeenCalledWith("sht_1", "local", {
        width: 1280,
        height: 704,
      }),
    );
  });
});
