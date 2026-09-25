// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ManagedRunControls } from "./ManagedRunControls";
import { getManagedRun, planManagedRun, runManagedSelection, stopManagedRun } from "./api";

vi.mock("./api", () => ({
  getManagedRun: vi.fn(), planManagedRun: vi.fn(),
  runManagedSelection: vi.fn(), stopManagedRun: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

const draftWithSelection = {
  run_id: "mrun_1", project_id: "prj_1", state: "draft",
  resolution_preset: null, current_index: 0, current_job_id: null,
  pending_event_id: null, paused_reason: "", is_stale: false,
  stale_reason: "", selected_shot_ids: ["sht_1", "sht_2"],
  pending_shot_ids: [], skipped_shots: {}, tail_source_job_ids: {},
  completed_job_ids: {}, steps: [
    { shot_id: "sht_1", tail_from_shot_id: null, tail_reason: "" },
    { shot_id: "sht_2", tail_from_shot_id: "sht_1", tail_reason: "Match the door" },
  ],
} as const;

const stoppedRun = {
  ...draftWithSelection,
  run_id: "mrun_2",
  state: "stopped",
  resolution_preset: "portrait-768",
  selected_shot_ids: ["sht_2"],
  pending_shot_ids: ["sht_2"],
  completed_job_ids: { sht_1: "job_done" },
} as const;

function renderControls() {
  return render(<ManagedRunControls projectId="prj_1" shots={[
    { id: "sht_1", title: "Open" }, { id: "sht_2", title: "Enter" },
  ]} provider="local" presets={[
    { id: "portrait-768", label: "Portrait 768", width: 768, height: 1376 },
  ]} onProjectChanged={() => {}} />);
}

async function openManager() {
  const trigger = await screen.findByRole("button", { name: /Managed run/ });
  fireEvent.click(trigger);
  return screen.findByRole("dialog", { name: "Managed run" });
}

it("keeps managed run controls behind a compact dialog trigger", async () => {
  vi.mocked(getManagedRun).mockResolvedValue(null);
  renderControls();

  expect(await screen.findByRole("button", { name: "Managed run" })).toBeTruthy();
  expect(screen.queryByRole("dialog", { name: "Managed run" })).toBeNull();

  await openManager();
  expect(screen.getByRole("button", { name: "Plan managed run" })).toBeTruthy();
});

it("defaults a fresh plan to all shots and submits only checked shots", async () => {
  vi.mocked(getManagedRun).mockResolvedValue(null);
  vi.mocked(planManagedRun).mockResolvedValue(draftWithSelection as never);
  vi.mocked(runManagedSelection).mockResolvedValue({
    ...draftWithSelection, state: "active", pending_shot_ids: ["sht_1"],
  } as never);
  renderControls();
  await openManager();

  fireEvent.click(await screen.findByRole("button", { name: "Plan managed run" }));
  expect((await screen.findByRole("checkbox", { name: /Open/ }) as HTMLInputElement).checked).toBe(true);
  expect((screen.getByRole("checkbox", { name: /Enter/ }) as HTMLInputElement).checked).toBe(true);
  fireEvent.click(screen.getByRole("checkbox", { name: /Enter/ }));
  fireEvent.change(screen.getByLabelText("Managed run resolution"), { target: { value: "portrait-768" } });
  fireEvent.click(screen.getByRole("button", { name: "Run selected" }));
  await waitFor(() => expect(runManagedSelection).toHaveBeenCalledWith(
    "prj_1", "mrun_1", ["sht_1"], "portrait-768",
  ));
});

it("allows a successful shot to be selected again after stop", async () => {
  vi.mocked(getManagedRun).mockResolvedValue(stoppedRun as never);
  vi.mocked(runManagedSelection).mockResolvedValue(stoppedRun as never);
  renderControls();
  await openManager();

  expect(await screen.findByText("Generated")).toBeTruthy();
  expect((screen.getByRole("checkbox", { name: /Enter/ }) as HTMLInputElement).disabled).toBe(false);
  fireEvent.click(screen.getByRole("checkbox", { name: /Open/ }));
  fireEvent.click(screen.getByRole("button", { name: "Resume selected" }));
  await waitFor(() => expect(runManagedSelection).toHaveBeenCalledWith(
    "prj_1", "mrun_2", ["sht_1", "sht_2"], undefined,
  ));
});

it("disables a dependent shot until its ungenerated source is selected", async () => {
  vi.mocked(getManagedRun).mockResolvedValue({
    ...draftWithSelection,
    state: "completed",
    selected_shot_ids: [],
  } as never);
  renderControls();
  await openManager();

  const source = await screen.findByRole("checkbox", { name: /Open/ }) as HTMLInputElement;
  const dependent = screen.getByRole("checkbox", { name: /Enter/ }) as HTMLInputElement;
  expect(dependent.disabled).toBe(true);
  expect(screen.getByText("Select Open first or generate it successfully.")).toBeTruthy();

  fireEvent.click(source);
  expect(dependent.disabled).toBe(false);
});

it("clears dependent selections when their ungenerated source is unchecked", async () => {
  vi.mocked(getManagedRun).mockResolvedValue(draftWithSelection as never);
  renderControls();
  await openManager();

  const source = await screen.findByRole("checkbox", { name: /Open/ }) as HTMLInputElement;
  const dependent = screen.getByRole("checkbox", { name: /Enter/ }) as HTMLInputElement;
  await waitFor(() => expect(dependent.checked).toBe(true));

  fireEvent.click(source);
  expect(dependent.checked).toBe(false);
  expect(dependent.disabled).toBe(true);
});

it("shows persisted dependency warnings", async () => {
  vi.mocked(getManagedRun).mockResolvedValue({
    ...stoppedRun,
    state: "paused",
    pending_shot_ids: [],
    skipped_shots: { sht_2: "Source Shot has no successful video" },
  } as never);
  renderControls();
  await openManager();

  expect(await screen.findByText("Source Shot has no successful video")).toBeTruthy();
});

it("defaults a completed batch to no selected shots", async () => {
  vi.mocked(getManagedRun).mockResolvedValue({
    ...stoppedRun,
    state: "completed",
    pending_shot_ids: [],
  } as never);
  renderControls();
  await openManager();

  const action = await screen.findByRole("button", { name: "Run selected" });
  expect((screen.getByRole("checkbox", { name: /Open/ }) as HTMLInputElement).checked).toBe(false);
  expect((screen.getByRole("checkbox", { name: /Enter/ }) as HTMLInputElement).checked).toBe(false);
  expect((action as HTMLButtonElement).disabled).toBe(true);
});

it("returns to clean planning state when the saved inactive plan is stale", async () => {
  vi.mocked(getManagedRun).mockResolvedValue({
    ...stoppedRun,
    is_stale: true,
    stale_reason: "Shot brief or audio changed",
  } as never);
  renderControls();
  await openManager();

  expect(screen.queryByText("Shot brief or audio changed")).toBeNull();
  expect(screen.getByRole("button", { name: "Plan managed run" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Resume selected" })).toBeNull();
});

it("disables Run selected when no shots are checked", async () => {
  vi.mocked(getManagedRun).mockResolvedValue(draftWithSelection as never);
  renderControls();
  await openManager();

  fireEvent.click(await screen.findByRole("checkbox", { name: /Open/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: /Enter/ }));
  expect((screen.getByRole("button", { name: "Run selected" }) as HTMLButtonElement).disabled).toBe(true);
});

it("offers stop for an active local run", async () => {
  const active = {
    run_id: "mrun_2", project_id: "prj_1", state: "active", resolution_preset: "portrait-768",
    current_index: 0, current_job_id: "job_1", pending_event_id: null, paused_reason: "",
    is_stale: false, stale_reason: "", selected_shot_ids: ["sht_1"],
    pending_shot_ids: ["sht_1"], skipped_shots: {}, tail_source_job_ids: {},
    completed_job_ids: {},
    steps: [{ shot_id: "sht_1", tail_from_shot_id: null, tail_reason: "" }],
  } as const;
  vi.mocked(getManagedRun).mockResolvedValue(active as never);
  vi.mocked(stopManagedRun).mockResolvedValue({ ...active, state: "stopped" } as never);
  render(<ManagedRunControls projectId="prj_1" shots={[{ id: "sht_1", title: "Open" }]}
    provider="local" presets={[]} onProjectChanged={() => {}} />);
  await openManager();
  fireEvent.click(await screen.findByRole("button", { name: "Stop managed run" }));
  await waitFor(() => expect(stopManagedRun).toHaveBeenCalledWith("prj_1", "mrun_2"));
});

it("explains what planning does without starting a video", async () => {
  vi.mocked(getManagedRun).mockResolvedValue(null);
  render(<ManagedRunControls projectId="prj_1" shots={[{ id: "sht_1", title: "Open" }]}
    provider="local" presets={[]} onProjectChanged={() => {}} />);
  await openManager();

  const info = await screen.findByRole("button", { name: "About Plan managed run" });
  expect(info.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(info);
  expect(info.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText(/existing Shots.*tail-frame handoffs/)).toBeTruthy();
  expect(screen.getByText(/No video starts until/)).toBeTruthy();
});

it("closes the dialog without stopping an active run", async () => {
  const active = {
    ...draftWithSelection,
    state: "active",
    resolution_preset: "portrait-768",
    current_job_id: "job_1",
    pending_shot_ids: ["sht_1", "sht_2"],
  } as const;
  vi.mocked(getManagedRun).mockResolvedValue(active as never);
  renderControls();
  await openManager();

  fireEvent.keyDown(window, { key: "Escape" });

  expect(screen.queryByRole("dialog", { name: "Managed run" })).toBeNull();
  expect(stopManagedRun).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: /Managed run/ })).toBeTruthy();
});
