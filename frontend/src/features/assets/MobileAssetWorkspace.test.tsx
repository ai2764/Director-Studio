// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Project } from "../../shared/api/types";
import { MobileAssetWorkspace } from "./MobileAssetWorkspace";

const state = vi.hoisted(() => ({
  project: {
    id: "prj_1", name: "Night film", script_text: "", mode: "director",
    created_at: "2026-01-01", updated_at: "2026-01-01", shot_ids: [],
  } as Project | null,
}));

vi.mock("../../shared/project/ProjectContext", () => ({
  useProject: () => ({ project: state.project, projectId: state.project?.id ?? null }),
}));
vi.mock("../library/LibraryPage", () => ({
  LibraryPage: ({ lockedKind, mobile }: { lockedKind?: string; mobile?: boolean }) => (
    <div data-testid="mobile-library" data-kind={lockedKind} data-mobile={String(Boolean(mobile))} />
  ),
}));
vi.mock("../library/api", () => ({
  importExternalAsset: vi.fn(),
  listLibraryAssets: vi.fn(async (kind: string) => kind === "actors" ? [{
    id: "actor_1", kind: "actors", name: "Mara", notes: "Lead", pipeline_id: "external",
    job_id: "", seed: null, created_at: "2026-01-01", files: {}, meta: {},
    urls: { master: "/mara.png" }, project_id: "prj_1",
  }] : []),
}));
vi.mock("../casting/CastingPage", () => ({
  CastingPage: () => <label>Actor generator<input aria-label="Mobile actor draft" /></label>,
}));
vi.mock("../set/SetDesignPage", () => ({ SetDesignPage: () => <div>Scene generator</div> }));
vi.mock("../props/PropsPage", () => ({ PropsPage: () => <div>Prop generator</div> }));

describe("MobileAssetWorkspace", () => {
  afterEach(cleanup);
  beforeEach(() => {
    state.project = {
      id: "prj_1", name: "Night film", script_text: "", mode: "director",
      created_at: "2026-01-01", updated_at: "2026-01-01", shot_ids: [],
    };
  });

  it("opens on a cross-category Library overview", async () => {
    render(<MobileAssetWorkspace />);

    for (const category of ["Library", "Actors", "Scenes", "Props", "Voices"]) {
      expect(screen.getByRole("button", { name: category })).toBeTruthy();
    }
    expect(screen.queryByRole("button", { name: "Costumes" })).toBeNull();
    expect(screen.queryByText("No wardrobe prepared")).toBeNull();
    expect(screen.queryByRole("button", { name: "Layouts" })).toBeNull();
    expect(screen.getByRole("heading", { name: "Project library" })).toBeTruthy();
    await waitFor(() => expect(screen.getByText("Mara")).toBeTruthy());
    expect(screen.queryByTestId("mobile-library")).toBeNull();
  });

  it("shows the preparation input directly without a category library", () => {
    render(<MobileAssetWorkspace />);

    fireEvent.click(screen.getByRole("button", { name: "Actors" }));
    expect(screen.getByRole("heading", { name: "Actor workflow" })).toBeTruthy();
    expect(screen.getByText("Actor generator")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Import actor" })).toBeTruthy();
    expect(screen.queryByTestId("mobile-library")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Voices" }));
    expect(screen.getByRole("heading", { name: "Voice workflow" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Import voice" })).toBeTruthy();
    expect(screen.queryByTestId("mobile-library")).toBeNull();
  });

  it("does not show legacy coverage state", () => {
    state.project = {
      id: "prj_1", name: "Night film", script_text: "", mode: "director",
      created_at: "2026-01-01", updated_at: "2026-01-01", shot_ids: [],
      asset_coverage_review: { script_hash: "abc", status: "reviewed", recommendations: [], notes: "Ready" },
    };
    render(<MobileAssetWorkspace />);

    expect(screen.queryByText("Coverage reviewed")).toBeNull();
    expect(screen.queryByText("Coverage not reviewed")).toBeNull();
  });

  it("opens import from the labeled action inside a mobile category", () => {
    render(<MobileAssetWorkspace />);

    const categories = screen.getByRole("navigation", { name: "Asset categories" });
    expect(within(categories).queryByRole("button", { name: /Import/i })).toBeNull();
    fireEvent.click(within(categories).getByRole("button", { name: "Props" }));
    fireEvent.click(screen.getByRole("button", { name: "Import prop" }));

    expect(screen.getByRole("dialog", { name: "Import Props" })).toBeTruthy();
    expect(screen.queryByTestId("mobile-library")).toBeNull();
  });

  it("preserves an asset draft while switching mobile preparation categories", () => {
    render(<MobileAssetWorkspace />);

    fireEvent.click(screen.getByRole("button", { name: "Actors" }));
    const draft = screen.getByRole("textbox", { name: "Mobile actor draft" });
    fireEvent.change(draft, { target: { value: "Keep this mobile reference" } });

    fireEvent.click(screen.getByRole("button", { name: "Props" }));
    fireEvent.click(screen.getByRole("button", { name: "Actors" }));

    expect((screen.getByRole("textbox", { name: "Mobile actor draft" }) as HTMLInputElement).value).toBe("Keep this mobile reference");
  });
});
