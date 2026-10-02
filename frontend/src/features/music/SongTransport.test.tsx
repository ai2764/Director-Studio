// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SongTransport } from "./SongTransport";
import { getSongSegments } from "./api";

vi.mock("../../shared/project/ProjectContext", () => ({ useProject: () => ({ projectId: "mv", project: {
  music_master: { filename: "song.wav", duration_s: 30, content_sha256: "hash" },
} }) }));
vi.mock("./api", () => ({ getSongSegments: vi.fn(), songAudioUrl: () => "/audio" }));
beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(function(this: HTMLMediaElement) {
    this.dispatchEvent(new Event("play")); return Promise.resolve();
  });
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(function(this: HTMLMediaElement) {
    this.dispatchEvent(new Event("pause"));
  });
  vi.mocked(getSongSegments).mockResolvedValue({ master_stale: false, document: {
    revision: 3, master_sha256: "hash", segments: [
      { id: "one", start_s: 2, end_s: 5, text: "First lyric" },
      { id: "two", start_s: 7, end_s: 10, text: "Second lyric" },
      { id: "three", start_s: 11, end_s: 15, text: "Third lyric" },
    ],
  } });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.clearAllMocks(); });

it("selects discussion context while seeking and auditioning remain independent", async () => {
  const selection = vi.fn();
  const { container } = render(<SongTransport active onSelectionChange={selection} />);
  fireEvent.click(await screen.findByRole("button", { name: "Select segment 2: Second lyric" }));
  expect(selection).toHaveBeenLastCalledWith({ revision: 3, ids: ["two"] });
  const audio = container.querySelector("audio")!;
  expect(audio.currentTime).toBe(7);
  fireEvent.click(screen.getByRole("button", { name: "Audition segment 3" }));
  expect(audio.currentTime).toBe(11);
  audio.currentTime = 15.2;
  fireEvent.timeUpdate(audio);
  expect(audio.currentTime).toBe(15);
  expect(screen.getByRole("button", { name: "Play song" })).toBeTruthy();
  fireEvent.change(screen.getByRole("slider", { name: "Song position" }), { target: { value: "20" } });
  expect(selection).toHaveBeenLastCalledWith({ revision: 3, ids: ["two"] });
  expect(audio.currentTime).toBe(20);
});

it("loops an audition and pauses when leaving Director", async () => {
  const selection = vi.fn();
  const { container, rerender } = render(<SongTransport active onSelectionChange={selection} />);
  await screen.findByRole("button", { name: "Audition segment 1" });
  fireEvent.click(screen.getByRole("button", { name: "Loop segment" }));
  fireEvent.click(screen.getByRole("button", { name: "Audition segment 1" }));
  const audio = container.querySelector("audio")!;
  audio.currentTime = 5.1;
  fireEvent.timeUpdate(audio);
  expect(audio.currentTime).toBe(2);
  rerender(<SongTransport active={false} onSelectionChange={selection} />);
  expect(screen.getByRole("button", { name: "Play song" })).toBeTruthy();
});

it("refreshes saved segments and clears stale discussion context", async () => {
  const selection = vi.fn();
  render(<SongTransport active onSelectionChange={selection} />);
  fireEvent.click(await screen.findByRole("button", { name: "Select segment 2: Second lyric" }));
  vi.mocked(getSongSegments).mockResolvedValue({ master_stale: true, document: null });
  act(() => { window.dispatchEvent(new CustomEvent("song-segments-changed", { detail: "mv" })); });
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("song has changed"));
  expect(screen.queryByRole("button", { name: "Select segment 2: Second lyric" })).toBeNull();
  expect(selection).toHaveBeenLastCalledWith(undefined);
});
