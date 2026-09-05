// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Shot } from "../../shared/api/types";
import { listLibraryAssets, type LibraryAsset } from "../library/api";
import { ShotMaterialEditor } from "./ShotMaterialEditor";

const replaceShotMaterialsMock = vi.hoisted(() => vi.fn());

vi.mock("../library/api", () => ({
  listLibraryAssets: vi.fn(),
}));

vi.mock("./api", () => ({
  replaceShotMaterials: replaceShotMaterialsMock,
}));

function asset(kind: string, id: string, name: string, fileKey: string): LibraryAsset {
  return {
    id,
    kind,
    name,
    notes: `${name} metadata`,
    pipeline_id: "external",
    job_id: "",
    seed: null,
    created_at: "2026-01-01T00:00:00Z",
    files: { [fileKey]: `${fileKey}.png` },
    meta: {},
    urls: { [fileKey]: `/api/files/library/${kind}/${id}/${fileKey}.png` },
    project_id: "prj_1",
  };
}

function selectedShot(): Shot {
  return {
    id: "sht_1",
    project_id: "prj_1",
    scene_id: "sc01",
    title: "The Empty Room",
    script_beat: "The Agent waits.",
    duration_s: 6,
    status: "needs_review",
    refs: [
      { role: "actor", asset_id: "act_1", file_key: "master", picture_index: 1 },
      { role: "layout_ref_frame", asset_id: "lay_1", file_key: "layout", picture_index: 2 },
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
    layout_asset_id: "lay_1",
    layout_review_status: "usable",
    ref_frame_job_id: null,
    layout_refs: [],
    h3_job_id: null,
    source_audio_path: null,
    feedback: "",
    blocked_reasons: [],
    meta: {},
  };
}

describe("ShotMaterialEditor", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
    const inventory = [
      asset("actors", "act_1", "Agent", "master"),
      asset("scenes", "scn_1", "Interview room", "wide"),
      asset("layouts", "lay_1", "Waiting composition", "layout"),
    ];
    vi.mocked(listLibraryAssets).mockImplementation(async (kind) =>
      inventory.filter((item) => item.kind === kind),
    );
    replaceShotMaterialsMock.mockResolvedValue(selectedShot());
  });

  it("removes a selected Picture, adds a Library asset, and saves the new inventory", async () => {
    render(
      <ShotMaterialEditor
        shot={selectedShot()}
        shotNumber={1}
        onClose={vi.fn()}
        onOpenImage={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Remove Picture 1 · Agent" }));
    expect(screen.getByText("Pictures 1 / 9")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Add Interview room" }));
    expect(screen.getByText("Pictures 2 / 9")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      expect(replaceShotMaterialsMock).toHaveBeenCalledWith("sht_1", [
        { role: "scene", asset_id: "scn_1", file_key: "wide" },
        { role: "layout_ref_frame", asset_id: "lay_1", file_key: "layout" },
      ]);
    });
  });

  it("includes Layouts in the nine-Picture ceiling", async () => {
    const shot = selectedShot();
    shot.refs = Array.from({ length: 8 }, (_, index) => ({
      role: "actor" as const,
      asset_id: `act_${index + 1}`,
      file_key: "master",
      picture_index: index + 1,
    }));
    shot.refs.push({
      role: "layout_ref_frame",
      asset_id: "lay_1",
      file_key: "layout",
      picture_index: 9,
    });

    render(
      <ShotMaterialEditor
        shot={shot}
        shotNumber={1}
        onClose={vi.fn()}
        onOpenImage={vi.fn()}
      />,
    );

    expect(screen.getByText("Pictures 9 / 9")).toBeTruthy();
    expect(
      (await screen.findByRole("button", { name: "Add Interview room" })).hasAttribute("disabled"),
    ).toBe(true);
    expect(screen.getByText("Picture limit reached")).toBeTruthy();
  });

  it("filters the Library by material kind", async () => {
    render(
      <ShotMaterialEditor
        shot={selectedShot()}
        shotNumber={1}
        onClose={vi.fn()}
        onOpenImage={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Scenes" }));

    expect(screen.getByRole("button", { name: "Add Interview room" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Agent selected" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Waiting composition selected" })).toBeNull();
  });

  it("opens selected and Library previews without changing the Picture inventory", async () => {
    const onOpenImage = vi.fn();
    render(
      <ShotMaterialEditor
        shot={selectedShot()}
        shotNumber={1}
        onClose={vi.fn()}
        onOpenImage={onOpenImage}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Open Picture 1 · Agent" }));
    expect(onOpenImage).toHaveBeenLastCalledWith("/api/files/library/actors/act_1/master.png");
    expect(screen.getByText("Pictures 2 / 9")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Open Interview room preview" }));
    expect(onOpenImage).toHaveBeenLastCalledWith("/api/files/library/scenes/scn_1/wide.png");
    expect(screen.getByText("Pictures 2 / 9")).toBeTruthy();
  });

  it("adds multiple file variants from the same Library asset as separate Pictures", async () => {
    const angles = asset("actors", "act_angles", "Mia angles", "front");
    angles.files = { front: "front.png", profile: "profile.png" };
    angles.urls = {
      front: "/api/files/library/actors/act_angles/front.png",
      profile: "/api/files/library/actors/act_angles/profile.png",
    };
    vi.mocked(listLibraryAssets).mockImplementation(async (kind) =>
      kind === "actors" ? [angles] : [],
    );
    const empty = selectedShot();
    empty.refs = [];
    replaceShotMaterialsMock.mockResolvedValue(empty);

    render(
      <ShotMaterialEditor
        shot={empty}
        shotNumber={1}
        onClose={vi.fn()}
        onOpenImage={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Add Mia angles · front" }));
    fireEvent.click(screen.getByRole("button", { name: "Add Mia angles · profile" }));
    expect(screen.getByText("Pictures 2 / 9")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      expect(replaceShotMaterialsMock).toHaveBeenCalledWith("sht_1", [
        { role: "actor", asset_id: "act_angles", file_key: "front" },
        { role: "actor", asset_id: "act_angles", file_key: "profile" },
      ]);
    });
  });
});
