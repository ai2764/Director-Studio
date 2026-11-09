// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { ExecutionProgress } from "./ExecutionProgress";
import type { DirectorExecutionProgress } from "./api";
import { acceptExecutionProgress } from "./executionProgressState";

afterEach(cleanup);
const state = (): DirectorExecutionProgress => ({
  version: 1, run_id: "r1", status: "running", started_at: "2026-11-03T10:00:00Z",
  updated_at: "2026-11-03T10:00:10Z", current_stage: "prompt", shot_id: "s2", shot_index: 2, shot_total: 5,
  activity: "Writing H3 prompt",
  stages: {
    plan: { status: "completed", started_at: "2026-11-03T10:00:00Z", finished_at: "2026-11-03T10:00:02Z" },
    materials: { status: "reused", started_at: null, finished_at: null },
    prompt: { status: "running", started_at: "2026-11-03T10:00:02Z", finished_at: null },
    generation: { status: "not_requested", started_at: null, finished_at: null },
  },
});

it("shows four observed stages, elapsed time and reuse without a fake percentage", () => {
  render(<ExecutionProgress progress={state()} now={Date.parse("2026-11-03T10:00:12Z")} />);
  const stages = screen.getAllByRole("listitem");
  expect(stages).toHaveLength(4);
  expect(within(stages[1]).getByText("Reused")).toBeTruthy();
  expect(within(stages[2]).getByText("10s")).toBeTruthy();
  expect(within(stages[3]).getByText("Not requested")).toBeTruthy();
  expect(screen.getByText("Shot 2 of 5")).toBeTruthy();
  expect(screen.queryByRole("progressbar")).toBeNull();
});

it("keeps prompt-only completion separate from video and freezes elapsed time", () => {
  const progress = state();
  progress.status = "completed";
  progress.stages.prompt = { ...progress.stages.prompt, status: "completed", finished_at: progress.updated_at };
  render(<ExecutionProgress progress={progress} now={Date.parse("2026-11-03T10:10:00Z")} />);
  expect(within(screen.getAllByRole("listitem")[2]).getByText("8s")).toBeTruthy();
  expect(within(screen.getAllByRole("listitem")[3]).getByText("Not requested")).toBeTruthy();
});

it("does not label a submitted video as quality approved", () => {
  const progress = state();
  progress.stages.generation.status = "submitted";
  progress.stages.generation.quality_status = "not_reviewed";
  render(<ExecutionProgress progress={progress} now={Date.now()} />);
  expect(screen.getByText("Submitted")).toBeTruthy();
  expect(screen.getByText("Quality review not performed")).toBeTruthy();
});

it("rejects stale polling within a run and across a newer run", () => {
  const current = { ...state(), run_sequence: 2, revision: 5 };
  expect(acceptExecutionProgress(current, { ...current, revision: 3 })).toBe(current);
  expect(acceptExecutionProgress(current, { ...current, run_id: "old", run_sequence: 1, revision: 99 })).toBe(current);
  const newer = { ...current, revision: 6 };
  expect(acceptExecutionProgress(current, newer)).toBe(newer);
});
