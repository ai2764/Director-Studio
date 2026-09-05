// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CastingPage } from "./CastingPage";
import { fetchDefaults, generateActor, listActorJobs, type JobRecord } from "./api";

vi.mock("../../shared/project/ProjectContext", () => ({
  useProject: () => ({ projectId: "prj_test" }),
}));

vi.mock("./api", () => ({
  fetchDefaults: vi.fn(),
  listActorJobs: vi.fn(),
  generateActor: vi.fn(),
  getJob: vi.fn(),
  cancelJob: vi.fn(),
  saveJob: vi.fn(),
}));

const finishedJob: JobRecord = {
  id: "actjob_old",
  status: "succeeded",
  mode: "text",
  name: "Discarded actor result",
  notes: "",
  description: "",
  body_description: "",
  hair_description: "",
  negative_prompt: "",
  has_actor_ref: false,
  has_wardrobe_ref: false,
  include_headwear: false,
  include_footwear: false,
  seed: 42,
  fixed_seed: false,
  error: null,
  comfy_prompt_id: "prompt_old",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:01:00Z",
  outputs: {},
  input_previews: {},
  actor_id: null,
};

describe("CastingPage", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchDefaults).mockResolvedValue({
      default_negative: "",
      default_description: "",
      max_upload_mb: 20,
      output_slots: [],
    });
    vi.mocked(listActorJobs).mockResolvedValue([]);
  });

  it("does not restore an old finished actor job as the current result", async () => {
    vi.mocked(listActorJobs).mockResolvedValue([finishedJob]);

    render(<CastingPage onOpenLibrary={() => undefined} />);
    await waitFor(() => expect(listActorJobs).toHaveBeenCalled());

    expect((screen.getByLabelText(/Name/) as HTMLInputElement).value).toBe("");
    expect(screen.queryByText("actjob_old")).toBeNull();
    expect(screen.getByText("Idle")).toBeTruthy();
  });

  it("offers wardrobe accessory options and submits them", async () => {
    vi.mocked(generateActor).mockResolvedValue({
      ...finishedJob,
      id: "actjob_new",
      status: "queued",
      name: "Pirate Mia",
      has_wardrobe_ref: true,
      include_headwear: true,
      include_footwear: true,
    });

    render(<CastingPage onOpenLibrary={() => undefined} />);
    await waitFor(() => expect(fetchDefaults).toHaveBeenCalled());

    expect(screen.queryByLabelText("Include hat / headwear")).toBeNull();
    const wardrobeInput = screen.getByLabelText("Wardrobe") as HTMLInputElement;
    fireEvent.change(wardrobeInput, {
      target: { files: [new File(["wardrobe"], "pirate.png", { type: "image/png" })] },
    });

    fireEvent.click(screen.getByLabelText("Include hat / headwear"));
    fireEvent.click(screen.getByLabelText("Include shoes / footwear"));
    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: "Pirate Mia" } });
    fireEvent.change(screen.getByLabelText(/Actor description/), {
      target: { value: "Adult pirate actor" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Generate Actor" }));

    await waitFor(() => expect(generateActor).toHaveBeenCalledOnce());
    const form = vi.mocked(generateActor).mock.calls[0][0];
    expect(form.get("include_headwear")).toBe("true");
    expect(form.get("include_footwear")).toBe("true");
  });
});
