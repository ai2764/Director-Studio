// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LibraryPage } from "./LibraryPage";
import { importExternalAsset, listLibraryAssets } from "./api";

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
  id: "voi_mia",
  kind: "voices",
  name: "Mia",
  notes: "Warm neutral English, intimate delivery",
  pipeline_id: "external",
  job_id: "",
  seed: null,
  created_at: "2026-08-25T00:00:00Z",
  files: { source: "source.m4a", reference: "reference.wav" },
  meta: {
    duration_s: 8.4,
    h3_ready: true,
    source_filename: "mia.m4a",
  },
  urls: {
    source: "/api/files/library/voices/voi_mia/source.m4a",
    reference: "/api/files/library/voices/voi_mia/reference.wav",
  },
  project_id: "prj_test",
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
    fireEvent.click(screen.getByRole("button", { name: /Voices/ }));

    await screen.findByText("Mia");
    expect(screen.getByText("8.4s")).toBeTruthy();
    expect(screen.getByText("H3 Ready")).toBeTruthy();
    const player = container.querySelector("audio");
    expect(player).toBeTruthy();
    expect(player?.getAttribute("src")).toBe(
      "/api/files/library/voices/voi_mia/reference.wav",
    );
  });

  it("switches the import form to required Name and Audio file", async () => {
    render(<LibraryPage />);
    fireEvent.click(screen.getByRole("button", { name: /Voices/ }));
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
      name: "Mia close-up",
      notes: "Warm cyan smile",
    });
    render(<LibraryPage />);
    fireEvent.click(screen.getByRole("button", { name: /Voices/ }));
    await screen.findByText("Mia");

    fireEvent.click(screen.getByRole("button", { name: "Edit Mia metadata" }));
    expect(screen.getByRole("dialog", { name: "Edit Mia metadata" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Mia close-up" },
    });
    fireEvent.change(screen.getByLabelText("Notes"), {
      target: { value: "Warm cyan smile" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("Mia close-up")).toBeTruthy();
    expect(screen.getByText("Warm cyan smile")).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "Edit Mia metadata" })).toBeNull();
  });

  it("closes the dialog and refreshes the category after import", async () => {
    vi.mocked(importExternalAsset).mockResolvedValue({} as never);
    render(<LibraryPage />);
    await waitFor(() => expect(listLibraryAssets).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "Import Actors" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Mara" } });
    fireEvent.change(screen.getByLabelText("Image file"), {
      target: { files: [new File(["image"], "mara.png", { type: "image/png" })] },
    });

    await waitFor(() => expect(importExternalAsset).toHaveBeenCalledWith(expect.objectContaining({
      kind: "actors", name: "Mara", projectId: "prj_test",
    })));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Import Actors" })).toBeNull());
    expect(listLibraryAssets).toHaveBeenCalledTimes(2);
  });
});
