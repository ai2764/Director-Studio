// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PropsPage } from "./PropsPage";
import { generateProp, listPropJobs } from "./api";
import type { PropJobRecord } from "./api";

vi.mock("../../shared/project/ProjectContext", () => ({
  useProject: () => ({
    projectId: "prj_test",
    project: { id: "prj_test", name: "Test project" },
  }),
}));

vi.mock("./api", () => ({
  generateProp: vi.fn(),
  getPropJob: vi.fn(),
  cancelPropJob: vi.fn(),
  savePropJob: vi.fn(),
  listPropJobs: vi.fn(),
}));

const finishedPropJob: PropJobRecord = {
  id: "propjob_old", status: "succeeded", name: "Discarded prop result", notes: "",
  seed: 19, fixed_seed: false, error: null, comfy_prompt_id: "prompt_old",
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:01:00Z",
  outputs: {}, output_order: [], input_previews: {}, prop_id: null,
};

describe("PropsPage", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listPropJobs).mockResolvedValue([]);
  });

  it("presents the prop result as a multi-view reference sheet", async () => {
    render(<PropsPage onOpenLibrary={() => undefined} />);

    expect(await screen.findByRole("heading", { name: "Props · Reference Sheet" })).toBeTruthy();
    expect(screen.getByText(/one multi-view reference sheet.*H3 receives/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Prepare Reference Sheet" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Prepare Reference Sheet" }));
    expect(await screen.findByText("Name is required")).toBeTruthy();
    expect(screen.getByText("Prop photo is required")).toBeTruthy();
    expect(generateProp).not.toHaveBeenCalled();
  });

  it("does not restore an old finished prop job as the current result", async () => {
    vi.mocked(listPropJobs).mockResolvedValue([finishedPropJob]);

    render(<PropsPage onOpenLibrary={() => undefined} />);
    await waitFor(() => expect(listPropJobs).toHaveBeenCalled());

    expect((screen.getByLabelText(/Name/) as HTMLInputElement).value).toBe("");
    expect(screen.queryByText("propjob_old")).toBeNull();
    expect(screen.getByText("Idle")).toBeTruthy();
  });
});
