// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectPicker } from "./ProjectPicker";

const createAndSelect = vi.fn();

vi.mock("./ProjectContext", () => ({
  useProject: () => ({
    projects: [],
    projectId: null,
    project: null,
    loading: false,
    error: null,
    setProjectId: vi.fn(),
    refreshProjects: vi.fn(),
    createAndSelect,
  }),
}));

describe("ProjectPicker", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
    createAndSelect.mockResolvedValue({
      id: "prj_test",
      name: "External board",
      script_text: "",
      mode: "json_production",
      created_at: "",
      updated_at: "",
      shot_ids: [],
    });
  });

  it("creates a JSON Production project with the selected mode", async () => {
    const { container } = render(<ProjectPicker />);

    fireEvent.click(screen.getByRole("button", { name: "New" }));
    const dialog = screen.getByRole("dialog", { name: "Create project" });
    expect(dialog).toBeTruthy();
    expect(container.contains(dialog)).toBe(false);
    expect(container.querySelector(".project-picker")?.querySelector("[placeholder='Project name']")).toBeNull();
    fireEvent.change(screen.getByPlaceholderText("Project name"), {
      target: { value: "External board" },
    });
    fireEvent.change(screen.getByLabelText("Mode"), {
      target: { value: "json_production" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(createAndSelect).toHaveBeenCalledWith(
        "External board",
        "",
        "json_production",
      ),
    );
    expect(screen.queryByRole("dialog", { name: "Create project" })).toBeNull();
  });

  it("cancels project creation without changing the header controls", () => {
    render(<ProjectPicker />);

    fireEvent.click(screen.getByRole("button", { name: "New" }));
    fireEvent.change(screen.getByPlaceholderText("Project name"), {
      target: { value: "Discard me" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("dialog", { name: "Create project" })).toBeNull();
    expect(screen.getByRole("button", { name: "New" })).toBeTruthy();
  });
});
