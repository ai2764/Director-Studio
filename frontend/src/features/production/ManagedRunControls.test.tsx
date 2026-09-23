// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ManagedRunControls } from "./ManagedRunControls";
import { getManagedRun, planManagedRun, startManagedRun, stopManagedRun } from "./api";

vi.mock("./api", () => ({
  getManagedRun: vi.fn(), planManagedRun: vi.fn(),
  startManagedRun: vi.fn(), stopManagedRun: vi.fn(),
}));

afterEach(cleanup);

it("requires a reviewed plan and explicit local resolution before autonomous start", async () => {
  const draft = {
    run_id: "mrun_1", project_id: "prj_1", state: "draft", resolution_preset: null,
    current_index: 0, current_job_id: null, pending_event_id: null, paused_reason: "",
    steps: [
      { shot_id: "sht_1", tail_from_shot_id: null, tail_reason: "" },
      { shot_id: "sht_2", tail_from_shot_id: "sht_1", tail_reason: "Match the door" },
    ],
  } as const;
  vi.mocked(getManagedRun).mockResolvedValue(null);
  vi.mocked(planManagedRun).mockResolvedValue(draft as never);
  vi.mocked(startManagedRun).mockResolvedValue({ ...draft, state: "active" } as never);
  render(<ManagedRunControls projectId="prj_1" shots={[
    { id: "sht_1", title: "Open" }, { id: "sht_2", title: "Enter" },
  ]} provider="local" presets={[
    { id: "portrait-768", label: "Portrait 768", width: 768, height: 1376 },
  ]} onProjectChanged={() => {}} />);

  fireEvent.click(await screen.findByRole("button", { name: "Plan managed run" }));
  const summary = await screen.findByText("Plan details");
  const details = summary.closest("details");
  expect(details?.open).toBe(false);
  fireEvent.click(summary);
  expect(await screen.findByText(/Match the door/)).toBeTruthy();
  expect(details?.open).toBe(true);
  expect((screen.getByRole("button", { name: "Start managed run" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Managed run resolution"), { target: { value: "portrait-768" } });
  fireEvent.click(screen.getByRole("button", { name: "Start managed run" }));
  await waitFor(() => expect(startManagedRun).toHaveBeenCalledWith("prj_1", "mrun_1", "portrait-768"));
});

it("offers stop for an active local run", async () => {
  const active = {
    run_id: "mrun_2", project_id: "prj_1", state: "active", resolution_preset: "portrait-768",
    current_index: 0, current_job_id: "job_1", pending_event_id: null, paused_reason: "",
    steps: [{ shot_id: "sht_1", tail_from_shot_id: null, tail_reason: "" }],
  } as const;
  vi.mocked(getManagedRun).mockResolvedValue(active as never);
  vi.mocked(stopManagedRun).mockResolvedValue({ ...active, state: "stopped" } as never);
  render(<ManagedRunControls projectId="prj_1" shots={[{ id: "sht_1", title: "Open" }]}
    provider="local" presets={[]} onProjectChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "Stop managed run" }));
  await waitFor(() => expect(stopManagedRun).toHaveBeenCalledWith("prj_1", "mrun_2"));
});

it("explains what planning does without starting a video", async () => {
  vi.mocked(getManagedRun).mockResolvedValue(null);
  render(<ManagedRunControls projectId="prj_1" shots={[{ id: "sht_1", title: "Open" }]}
    provider="local" presets={[]} onProjectChanged={() => {}} />);

  const info = await screen.findByRole("button", { name: "About Plan managed run" });
  expect(info.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(info);
  expect(info.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText(/existing Shots.*tail-frame handoffs/)).toBeTruthy();
  expect(screen.getByText(/No video starts until/)).toBeTruthy();
});
