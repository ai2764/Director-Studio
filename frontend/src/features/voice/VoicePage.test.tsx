// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VoicePage } from "./VoicePage";
import { importExternalAsset, listLibraryAssets } from "../library/api";

vi.mock("../../shared/project/ProjectContext", () => ({
  useProject: () => ({ projectId: "prj_test", project: { name: "Voice film" } }),
}));

vi.mock("../library/api", () => ({
  listLibraryAssets: vi.fn(),
  importExternalAsset: vi.fn(),
  deleteLibraryAsset: vi.fn(),
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

describe("VoicePage", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listLibraryAssets).mockResolvedValue([voiceAsset]);
    vi.mocked(importExternalAsset).mockResolvedValue(voiceAsset);
  });

  it("lists project voice assets without switching a Library kind tab", async () => {
    const { container } = render(<VoicePage />);

    expect(screen.getByRole("heading", { name: "Voice" })).toBeTruthy();
    await screen.findByText("Mia");
    expect(listLibraryAssets).toHaveBeenCalledWith("voices", "prj_test");
    expect(screen.getByText("H3 Ready")).toBeTruthy();
    const player = container.querySelector("audio");
    expect(player?.getAttribute("src")).toBe(
      "/api/files/library/voices/voi_mia/reference.wav",
    );
  });

  it("opens the voice upload dialog from the plus button", async () => {
    render(<VoicePage />);
    await waitFor(() =>
      expect(listLibraryAssets).toHaveBeenCalledWith("voices", "prj_test"),
    );

    expect(screen.queryByRole("dialog", { name: "Import Voices" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Import Voices" }));
    expect(screen.getByRole("dialog", { name: "Import Voices" })).toBeTruthy();
    expect(screen.getByLabelText("Name")).toHaveProperty("required", true);
    const file = screen.getByLabelText("Audio file") as HTMLInputElement;
    expect(file.accept).toBe("audio/*,.wav,.mp3,.m4a,.aac,.flac,.ogg");
    expect(screen.getByLabelText("Description")).toBeTruthy();
    expect(screen.queryByLabelText("Kind")).toBeNull();
  });

  it("uploads a named audio sample as a voices library asset", async () => {
    render(<VoicePage />);
    await waitFor(() =>
      expect(listLibraryAssets).toHaveBeenCalledWith("voices", "prj_test"),
    );
    fireEvent.click(screen.getByRole("button", { name: "Import Voices" }));

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Mia" } });
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Warm neutral English" },
    });
    const audio = new File(["voice"], "mia.wav", { type: "audio/wav" });
    fireEvent.change(screen.getByLabelText("Audio file"), {
      target: { files: [audio] },
    });

    await waitFor(() => expect(importExternalAsset).toHaveBeenCalledTimes(1));
    expect(importExternalAsset).toHaveBeenCalledWith({
      file: audio,
      kind: "voices",
      name: "Mia",
      notes: "Warm neutral English",
      projectId: "prj_test",
    });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Import Voices" })).toBeNull());
  });
});
