// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SetDesignPage } from "./SetDesignPage";
import { fetchSceneDefaults, generateScene, listSceneJobs } from "./api";
import type { SceneJobRecord } from "./api";

vi.mock("../../shared/project/ProjectContext", () => ({
  useProject: () => ({
    projectId: "prj_test",
    project: { id: "prj_test", name: "Test project" },
  }),
}));

vi.mock("./api", () => ({
  fetchSceneDefaults: vi.fn(),
  listSceneJobs: vi.fn(),
  generateScene: vi.fn(),
  getSceneJob: vi.fn(),
  cancelSceneJob: vi.fn(),
  saveSceneJob: vi.fn(),
}));

const sevenAngles = [
  "left side view, eye level, medium shot (horizontal: 270, vertical: 0, zoom: 5.0)",
  "back view, eye level, medium shot (horizontal: 180, vertical: 0, zoom: 5.0)",
  "right side view, eye level, medium shot (horizontal: 90, vertical: 0, zoom: 5.0)",
  "front-left view, eye level, medium shot (horizontal: 315, vertical: 0, zoom: 5.0)",
  "front-right view, eye level, medium shot (horizontal: 45, vertical: 0, zoom: 5.0)",
  "front view, bird's eye view, medium shot (horizontal: 0, vertical: 45, zoom: 5.0)",
  "front view, low angle, medium shot (horizontal: 0, vertical: -30, zoom: 5.0)",
].join("\n");

const finishedSceneJob: SceneJobRecord = {
  id: "scenejob_old", status: "succeeded", name: "Discarded scene result", notes: "",
  angle_prompts: sevenAngles, prepend_text: "", append_text: "", used_angles: [],
  output_stems: [], seed: 11, fixed_seed: false, error: null, comfy_prompt_id: "prompt_old",
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:01:00Z",
  outputs: {}, output_order: [], input_previews: {}, scene_id: null,
};

describe("SetDesignPage", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:scene-reference"),
      revokeObjectURL: vi.fn(),
    });
    vi.mocked(fetchSceneDefaults).mockResolvedValue({
      default_angles: sevenAngles,
      default_prepend: "Keep the same set; only change the camera.",
      default_append: "",
      max_upload_mb: 20,
    });
    vi.mocked(listSceneJobs).mockResolvedValue([]);
  });

  it("presents the seven-view 1728×960 quality workflow from backend defaults", async () => {
    const { container } = render(<SetDesignPage onOpenLibrary={() => undefined} />);

    expect(await screen.findByText(/1728×960 quality mode/i)).toBeTruthy();
    expect(screen.getByText(/left_side_view_h270_v0\.png/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Generate 7 Angles" })).toBeTruthy();
    expect(container.querySelectorAll(".output-card")).toHaveLength(7);
  });

  it("excludes toggled-off views from the generated angle prompts", async () => {
    let submittedAngles = "";
    vi.mocked(generateScene).mockImplementation(async (formData) => {
      submittedAngles = String(formData.get("angle_prompts") || "");
      return undefined as never;
    });
    const { container } = render(<SetDesignPage onOpenLibrary={() => undefined} />);

    const leftSide = await screen.findByRole("button", { name: "Left side" });
    expect(leftSide.getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(leftSide);

    expect(leftSide.getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByRole("button", { name: "Generate 6 Angles" })).toBeTruthy();
    expect(container.querySelectorAll(".output-card")).toHaveLength(6);

    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: "Test set" } });
    fireEvent.change(screen.getByLabelText(/Scene image/), {
      target: { files: [new File(["scene"], "scene.png", { type: "image/png" })] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Generate 6 Angles" }));

    await waitFor(() => expect(submittedAngles).not.toBe(""));
    expect(submittedAngles).not.toContain("left side view");
    expect(submittedAngles.split("\n")).toHaveLength(6);
  });

  it("re-enables every view when the form is reset", async () => {
    const { container } = render(<SetDesignPage onOpenLibrary={() => undefined} />);
    const leftSide = await screen.findByRole("button", { name: "Left side" });

    fireEvent.click(leftSide);
    fireEvent.click(screen.getByRole("button", { name: "Reset form" }));

    expect(leftSide.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "Generate 7 Angles" })).toBeTruthy();
    expect(container.querySelectorAll(".output-card")).toHaveLength(7);
  });

  it("keeps the view toggle synchronized with prompt edits", async () => {
    render(<SetDesignPage onOpenLibrary={() => undefined} />);
    const leftSide = await screen.findByRole("button", { name: "Left side" });
    fireEvent.click(leftSide);

    fireEvent.change(screen.getByLabelText(/Angle prompts/), {
      target: { value: sevenAngles.replace("left side view", "wide establishing view") },
    });

    expect(screen.queryByRole("button", { name: "Left side" })).toBeNull();
    expect(screen.getByRole("button", { name: "Wide establishing" }).getAttribute("aria-pressed")).toBe("false");
  });

  it("does not restore an old finished scene job as the current result", async () => {
    vi.mocked(listSceneJobs).mockResolvedValue([finishedSceneJob]);

    render(<SetDesignPage onOpenLibrary={() => undefined} />);
    await waitFor(() => expect(listSceneJobs).toHaveBeenCalled());

    expect((screen.getByLabelText(/Name/) as HTMLInputElement).value).toBe("");
    expect(screen.queryByText("scenejob_old")).toBeNull();
    expect(screen.getByText("Idle")).toBeTruthy();
  });
});
