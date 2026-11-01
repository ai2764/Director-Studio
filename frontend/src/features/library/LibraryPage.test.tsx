// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LibraryPage } from "./LibraryPage";
import { deleteLibraryAsset, importExternalAsset, listLibraryAssets } from "./api";

const updateLibraryAssetMock = vi.hoisted(() => vi.fn());

vi.mock("../../shared/project/ProjectContext", () => ({
  useProject: () => ({ projectId: "prj_test", project: { name: "Voice film" } }),
}));

vi.mock("./api", () => ({
  listLibraryAssets: vi.fn(),
  importExternalAsset: vi.fn(),
  deleteLibraryAsset: vi.fn(),
  updateLibraryAsset: updateLibraryAssetMock,
}));

const voiceAsset = {
  id: "voi_sample",
  kind: "voices",
  name: "Voice sample",
  notes: "Clear neutral English speech",
  pipeline_id: "external",
  job_id: "",
  seed: null,
  created_at: "2026-08-25T00:00:00Z",
  files: { source: "source.m4a", reference: "reference.wav" },
  meta: {
    duration_s: 8.4,
    h3_ready: true,
    source_filename: "sample.m4a",
  },
  urls: {
    source: "/api/files/library/voices/voi_sample/source.m4a",
    reference: "/api/files/library/voices/voi_sample/reference.wav",
  },
  project_id: "prj_test",
};

const generatedLayout = {
  id: "lay_generated",
  kind: "layouts",
  name: "Shot 2 composition",
  notes: "Generated composition reference",
  pipeline_id: "ref_frame",
  job_id: "job_layout",
  seed: 42,
  created_at: "2026-09-14T10:00:00Z",
  files: { layout: "layout.png" },
  meta: { review_status: "pending_review" },
  urls: { layout: "/api/files/library/layouts/lay_generated/layout.png" },
  project_id: "prj_test",
};

const extractedTailFrame = {
  ...generatedLayout,
  id: "lay_tail",
  name: "Shot 1 tail frame",
  job_id: "job_h3",
  seed: null,
  urls: { layout: "/api/files/library/layouts/lay_tail/layout.png" },
  meta: {
    review_status: "pending_review",
    origin: { kind: "clip_tail_frame", source_shot_id: "sht_1" },
  },
};

describe("LibraryPage Voices", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listLibraryAssets).mockImplementation(async (kind) =>
      kind === "voices" ? [voiceAsset] : [],
    );
  });

  it("shows a Voice asset as playable H3-ready audio", async () => {
    const { container } = render(<LibraryPage />);

    await screen.findByText("Voice sample");
    expect(screen.getByText("8.4s")).toBeTruthy();
    expect(screen.getByText("H3 Ready")).toBeTruthy();
    const player = container.querySelector("audio");
    expect(player).toBeTruthy();
    expect(player?.getAttribute("src")).toBe(
      "/api/files/library/voices/voi_sample/reference.wav",
    );
  });

  it("shows categories together and searches saved descriptions without expanding them", async () => {
    vi.mocked(listLibraryAssets).mockImplementation(async (kind) => kind === "voices" ? [voiceAsset] : kind === "layouts" ? [generatedLayout] : []);
    render(<LibraryPage />);
    await screen.findByText("Voice sample");
    expect(screen.getByText("Shot 2 composition")).toBeTruthy();
    expect(screen.getByRole("region", { name: "Actors" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "Wardrobe" })).toBeTruthy();
    expect(screen.queryByText(voiceAsset.notes)).toBeNull();
    fireEvent.change(screen.getByRole("searchbox", { name: "Search assets" }), {target: {value: "neutral English"}});
    expect(screen.getByText("Voice sample")).toBeTruthy();
    expect(screen.queryByText("Shot 2 composition")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "View files" }));
    expect(screen.getByText(voiceAsset.notes)).toBeTruthy();
  });

  it("switches the import form to required Name and Audio file", async () => {
    render(<LibraryPage />);
    await waitFor(() =>
      expect(listLibraryAssets).toHaveBeenCalledWith("voices", "prj_test"),
    );
    fireEvent.click(screen.getByRole("button", { name: "Import Voices" }));

    expect(screen.getByRole("dialog", { name: "Import Voices" })).toBeTruthy();
    expect(screen.getByLabelText("Name")).toHaveProperty("required", true);
    const file = screen.getByLabelText("Audio file") as HTMLInputElement;
    expect(file.accept).toBe("audio/*,.wav,.mp3,.m4a,.aac,.flac,.ogg");
    expect(screen.getByLabelText("Description")).toBeTruthy();
  });

  it("does not duplicate import inside a locked category page", () => {
    render(<LibraryPage lockedKind="actors" mobile importOwnedByParent />);

    expect(screen.queryByRole("button", { name: "Import Actors" })).toBeNull();
  });

  it("edits an asset name and notes from its Library card", async () => {
    updateLibraryAssetMock.mockResolvedValueOnce({
      ...voiceAsset,
      name: "Updated voice sample",
      notes: "Warm cyan smile",
    });
    render(<LibraryPage />);
    await screen.findByText("Voice sample");

    fireEvent.click(screen.getByRole("button", { name: "Edit Voice sample metadata" }));
    expect(screen.getByRole("dialog", { name: "Edit Voice sample metadata" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Updated voice sample" },
    });
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Warm cyan smile" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("Updated voice sample")).toBeTruthy();
    expect(screen.queryByText("Warm cyan smile")).toBeNull();
    fireEvent.click(screen.getByRole("button", {name: "View files"}));
    expect(screen.getByText("Warm cyan smile")).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "Edit Voice sample metadata" })).toBeNull();
  });

  it("closes the dialog and refreshes the category after import", async () => {
    vi.mocked(importExternalAsset).mockResolvedValue({} as never);
    render(<LibraryPage />);
    await waitFor(() => expect(listLibraryAssets).toHaveBeenCalledTimes(6));

    fireEvent.click(screen.getByRole("button", { name: "Import Actors" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Actor sample" } });
    fireEvent.change(screen.getByLabelText("Image file"), {
      target: { files: [new File(["image"], "reference.png", { type: "image/png" })] },
    });

    fireEvent.click(screen.getByRole("button", { name: "Import image" }));
    await waitFor(() => expect(importExternalAsset).toHaveBeenCalledWith(expect.objectContaining({
      kind: "actors", name: "Actor sample", projectId: "prj_test",
    })));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Import Actors" })).toBeNull());
    expect(listLibraryAssets).toHaveBeenCalledTimes(12);
  });

  it("shows generated Layouts and extracted tail frames and allows manual deletion", async () => {
    vi.mocked(listLibraryAssets).mockImplementation(async (kind) =>
      kind === "layouts" ? [generatedLayout, extractedTailFrame] : [],
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<LibraryPage />);

    await screen.findByText("Shot 2 composition");
    expect(screen.getByText("Shot 1 tail frame")).toBeTruthy();

    const tailPreview = screen.getByRole("img", { name: "Shot 1 tail frame" });
    fireEvent.click(tailPreview.closest("button")!);
    fireEvent.click(within(screen.getByRole("dialog", { name: "Shot 1 tail frame assets" })).getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(deleteLibraryAsset).toHaveBeenCalledWith("layouts", "lay_tail");
    });
  });
});
