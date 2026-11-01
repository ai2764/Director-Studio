// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LibraryOverview } from "./LibraryOverview";
import { deleteLibraryAsset, listLibraryAssets } from "../library/api";

const updateLibraryAssetMock = vi.hoisted(() => vi.fn());

vi.mock("../../shared/project/ProjectContext", () => ({
  useProject: () => ({ projectId: "prj_test" }),
}));

vi.mock("../library/api", () => ({
  deleteLibraryAsset: vi.fn(),
  importExternalAsset: vi.fn(),
  listLibraryAssets: vi.fn(),
  updateLibraryAsset: updateLibraryAssetMock,
}));

const actor = {
  id: "act_sample",
  kind: "actors",
  name: "Actor sample",
  notes: "Lead performer identity set",
  pipeline_id: "gpt_actor",
  job_id: "job_actor",
  seed: 42,
  created_at: "2026-08-28T00:00:00Z",
  files: {
    master: "master.png",
    fullbody_threeview: "fullbody.png",
  },
  meta: {},
  urls: {
    master: "/assets/sample-master.png",
    fullbody_threeview: "/assets/sample-fullbody.png",
  },
  project_id: "prj_test",
};

describe("LibraryOverview asset details", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listLibraryAssets).mockImplementation(async (kind) =>
      kind === "actors" ? [actor] : [],
    );
  });

  it("opens the complete asset set when a Library thumbnail is clicked", async () => {
    render(<LibraryOverview onSelectKind={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "View Actor sample asset set" }));

    expect(screen.getByRole("dialog", { name: "Actor sample assets" })).toBeTruthy();
    expect(screen.getByText("Master")).toBeTruthy();
    expect(screen.getByText("Full-body three-view")).toBeTruthy();
    expect(screen.getByText("Lead performer identity set")).toBeTruthy();
  });

  it("renders every asset when a Library group contains more than six", async () => {
    const actors = ["Actor sample", "Tao", "Mia", "Noah", "June", "Iris", "Omar"].map((name, index) => ({
      ...actor,
      id: `act_${index + 1}`,
      name,
    }));
    vi.mocked(listLibraryAssets).mockImplementation(async (kind) =>
      kind === "actors" ? actors : [],
    );

    render(<LibraryOverview onSelectKind={vi.fn()} />);

    expect(await screen.findByRole("button", { name: "View Omar asset set" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /^View .* asset set$/ })).toHaveLength(7);
  });

  it("deletes an asset from its Library overview detail and refreshes that group", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<LibraryOverview onSelectKind={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "View Actor sample asset set" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(deleteLibraryAsset).toHaveBeenCalledWith("actors", "act_sample");
      expect(screen.queryByRole("dialog", { name: "Actor sample assets" })).toBeNull();
    });
    expect(vi.mocked(listLibraryAssets).mock.calls.filter(([kind]) => kind === "actors")).toHaveLength(2);
  });

  it("edits asset metadata from the mobile overview detail", async () => {
    updateLibraryAssetMock.mockResolvedValueOnce({
      ...actor,
      name: "Actor sample profile",
      notes: "Right-facing continuity angle",
    });
    render(<LibraryOverview onSelectKind={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "View Actor sample asset set" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Actor sample profile" },
    });
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Right-facing continuity angle" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByRole("dialog", { name: "Actor sample profile assets" })).toBeTruthy();
    expect(screen.getByText("Right-facing continuity angle")).toBeTruthy();
  });
});
