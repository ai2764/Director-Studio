// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SongTransport } from "./SongTransport";
import { getSongSegments } from "./api";

const master = vi.hoisted(() => ({ filename: "song.wav", duration_s: 30, content_sha256: "hash" }));
vi.mock("../../shared/project/ProjectContext", () => ({ useProject: () => ({ projectId: "mv", project: {
  music_master: master,
} }) }));
vi.mock("./api", () => ({ getSongSegments: vi.fn(), songAudioUrl: () => "/audio" }));
beforeEach(() => {
  master.duration_s = 30;
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
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

function mobileSong() {
  master.duration_s = 300;
  vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  vi.mocked(getSongSegments).mockResolvedValue({ master_stale: false, document: {
    revision: 3, master_sha256: "hash", segments: [
      { id: "one", start_s: 30, end_s: 35, text: "First lyric" },
      { id: "two", start_s: 40, end_s: 45, text: "Second lyric" },
      { id: "three", start_s: 90, end_s: 95, text: "Third lyric" },
    ],
  } });
}

it("shows the real position in mobile zoom, including the instrumental intro and later playback", async () => {
  mobileSong();
  const selection = vi.fn();
  const { container } = render(<SongTransport active onSelectionChange={selection} />);
  await screen.findByRole("button", { name: "Audition segment 1" });
  const slider = screen.getByRole("slider", { name: "Song position" }) as HTMLInputElement;
  expect(Number(slider.min)).toBe(0);
  expect(Number(slider.value)).toBe(0);
  const audio = container.querySelector("audio")!;
  audio.currentTime = 8;
  fireEvent.timeUpdate(audio);
  expect(Number(slider.min)).toBe(0);
  expect(Number(slider.value)).toBe(8);
  audio.currentTime = 85;
  fireEvent.timeUpdate(audio);
  expect(Number(slider.value)).toBe(85);
  expect(Number(slider.min)).toBeLessThan(85);
  expect(Number(slider.max)).toBeGreaterThan(85);
  expect(selection).toHaveBeenLastCalledWith(undefined);
});

it("captures a mobile pan and lets the next tap select a segment after capture is lost", async () => {
  mobileSong();
  class Pointer extends MouseEvent {
    pointerId: number;
    constructor(type: string, options: MouseEventInit & { pointerId?: number } = {}) {
      super(type, options); this.pointerId = options.pointerId ?? 1;
    }
  }
  vi.stubGlobal("PointerEvent", Pointer);
  const selection = vi.fn();
  const { container } = render(<SongTransport active onSelectionChange={selection} />);
  await screen.findByRole("button", { name: "Audition segment 1" });
  const pan = container.querySelector(".mv-song-anchors")! as HTMLDivElement;
  const capture = vi.fn();
  pan.setPointerCapture = capture;
  pan.hasPointerCapture = vi.fn(() => false);
  pan.releasePointerCapture = vi.fn();
  vi.spyOn(pan, "getBoundingClientRect").mockReturnValue({ width: 300 } as DOMRect);
  fireEvent.pointerDown(pan, { pointerId: 7, button: 0, clientX: 250, clientY: 10 });
  fireEvent.pointerMove(pan, { pointerId: 7, clientX: 50, clientY: 10 });
  expect(capture).toHaveBeenCalledWith(7);
  fireEvent.lostPointerCapture(pan, { pointerId: 7 });
  const slider = screen.getByRole("slider", { name: "Song position" }) as HTMLInputElement;
  const pannedStart = slider.min;
  fireEvent.pointerMove(pan, { pointerId: 7, clientX: 20, clientY: 10 });
  expect(slider.min).toBe(pannedStart);
  const anchor = screen.getByRole("button", { name: "Select segment 1: First lyric" });
  fireEvent.pointerDown(anchor, { pointerId: 8, button: 0, clientX: 150, clientY: 10 });
  fireEvent.pointerUp(anchor, { pointerId: 8 });
  fireEvent.click(anchor);
  expect(selection).toHaveBeenLastCalledWith({ revision: 3, ids: ["one"] });
});

it("resumes following playback after browsing a different mobile time window", async () => {
  mobileSong();
  class Pointer extends MouseEvent {
    pointerId = 1;
  }
  vi.stubGlobal("PointerEvent", Pointer);
  const { container } = render(<SongTransport active onSelectionChange={vi.fn()} />);
  await screen.findByRole("button", { name: "Audition segment 1" });
  const pan = container.querySelector(".mv-song-anchors")! as HTMLDivElement;
  pan.setPointerCapture = vi.fn();
  pan.hasPointerCapture = vi.fn(() => true);
  pan.releasePointerCapture = vi.fn();
  vi.spyOn(pan, "getBoundingClientRect").mockReturnValue({ width: 300 } as DOMRect);
  fireEvent.pointerDown(pan, { button: 0, clientX: 250, clientY: 10 });
  fireEvent.pointerMove(pan, { clientX: 50, clientY: 10 });
  fireEvent.pointerUp(pan);
  expect(pan.releasePointerCapture).toHaveBeenCalledWith(1);
  const slider = screen.getByRole("slider", { name: "Song position" }) as HTMLInputElement;
  expect(Number(slider.min)).toBe(20);
  fireEvent.click(screen.getByRole("button", { name: "Play song" }));
  expect(Number(slider.min)).toBe(0);
  const audio = container.querySelector("audio")!;
  audio.currentTime = 85;
  fireEvent.timeUpdate(audio);
  expect(Number(slider.value)).toBe(85);
});

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
