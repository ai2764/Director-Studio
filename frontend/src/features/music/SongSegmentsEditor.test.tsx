// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SongSegmentsEditor } from "./SongSegmentsEditor";
import { getSongSegments, previewSongSegments, saveSongSegments } from "./api";

const state = vi.hoisted(() => ({ projectId: "prj_mv" }));
vi.mock("../../shared/project/ProjectContext", () => ({
  useProject: () => ({
    projectId: state.projectId,
    project: { id: state.projectId, mode: "mv", music_master: {
      filename: "song.wav", duration_s: 20, content_sha256: "a".repeat(64),
    } },
  }),
}));
vi.mock("./api", () => ({
  getSongSegments: vi.fn(), previewSongSegments: vi.fn(), saveSongSegments: vi.fn(),
  songAudioUrl: (id: string) => `/api/projects/${id}/music-master/audio`,
}));
afterEach(() => { cleanup(); vi.clearAllMocks(); state.projectId = "prj_mv"; });

it("imports external text, lets the user correct times, notifies the player after saving", async () => {
  vi.mocked(getSongSegments).mockResolvedValue({ document: null, master_stale: false });
  vi.mocked(previewSongSegments).mockResolvedValue({
    rows: [{ id: "seg_a", start_s: 0, end_s: null, text: "Opening lyric" }],
    unresolved: ["End time missing"],
  });
  vi.mocked(saveSongSegments).mockResolvedValue({
    revision: 1, master_sha256: "a".repeat(64), raw_input: "Opening lyric",
    segments: [{ id: "seg_a", start_s: 0, end_s: 4, text: "Opening lyric" }],
  });
  const saved = vi.fn();
  window.addEventListener("song-segments-changed", saved, { once: true });
  render(<SongSegmentsEditor />);
  fireEvent.change(await screen.findByLabelText("Lyrics and time notes"), { target: { value: "Opening lyric" } });
  fireEvent.click(screen.getByRole("button", { name: "Prepare segments" }));
  expect(await screen.findByText("End time missing")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Segment 1 end"), { target: { value: "4" } });
  fireEvent.click(screen.getByRole("button", { name: "Save segments" }));
  await waitFor(() => expect(saveSongSegments).toHaveBeenCalledWith(
    "prj_mv", 0, "Opening lyric", [{ id: "seg_a", start_s: 0, end_s: 4, text: "Opening lyric" }],
  ));
  await waitFor(() => expect(saved).toHaveBeenCalled());
});

it("ignores a preview that finishes after the user changes the source or project", async () => {
  vi.mocked(getSongSegments).mockResolvedValue({ document: null, master_stale: false });
  let resolvePreview!: (value: { rows: { id: string; start_s: number; end_s: number; text: string }[]; unresolved: string[] }) => void;
  vi.mocked(previewSongSegments).mockImplementation(() => new Promise((resolve) => { resolvePreview = resolve; }));
  const { rerender } = render(<SongSegmentsEditor />);
  const input = await screen.findByLabelText("Lyrics and time notes");
  fireEvent.change(input, { target: { value: "old notes" } });
  fireEvent.click(screen.getByRole("button", { name: "Prepare segments" }));
  fireEvent.change(input, { target: { value: "new notes" } });
  await act(async () => resolvePreview({ rows: [
    { id: "old", start_s: 0, end_s: 1, text: "Old lyric" },
  ], unresolved: [] }));
  expect(screen.queryByText("Old lyric")).toBeNull();
  expect((screen.getByLabelText("Lyrics and time notes") as HTMLTextAreaElement).value).toBe("new notes");

  fireEvent.click(screen.getByRole("button", { name: "Prepare segments" }));
  state.projectId = "prj_other";
  rerender(<SongSegmentsEditor />);
  await act(async () => resolvePreview({ rows: [
    { id: "old2", start_s: 0, end_s: 1, text: "Other project's lyric" },
  ], unresolved: [] }));
  expect(screen.queryByText("Other project's lyric")).toBeNull();
});
