// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Project, ProjectMode, Shot } from "../shared/api/types";
import App from "./App";

const projectState = vi.hoisted(() => ({
  project: {
    id: "prj_director",
    name: "Director film",
    script_text: "",
    mode: "director" as ProjectMode,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    shot_ids: [],
  } as Project | null,
}));

vi.mock("../shared/project/ProjectContext", () => ({
  ProjectProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useProject: () => ({
    projects: projectState.project ? [projectState.project] : [],
    projectId: projectState.project?.id ?? null,
    project: projectState.project,
    loading: false,
    error: null,
    setProjectId: vi.fn(),
    refreshProjects: vi.fn(),
    createAndSelect: vi.fn(),
  }),
}));

vi.mock("../shared/project/ProjectPicker", () => ({
  ProjectPicker: () => <div data-testid="project-picker" />,
}));

vi.mock("../features/settings/WorkflowSettingsPage", () => ({
  WorkflowSettingsPage: () => <h1>Workflow settings</h1>,
}));

vi.mock("../shared/api/client", () => ({
  fetchHealth: vi.fn().mockResolvedValue({
    comfy_reachable: true,
    comfy_error: null,
  }),
}));

vi.mock("../features/library/LibraryPage", () => ({
  LibraryPage: ({ mobile }: { mobile?: boolean }) => (
    <div data-testid="library-page" data-mobile={String(Boolean(mobile))} />
  ),
}));
vi.mock("../features/assets/AssetWorkspace", () => ({
  AssetWorkspace: () => <div data-testid="asset-workspace" />,
}));
vi.mock("../features/assets/MobileAssetWorkspace", () => ({
  MobileAssetWorkspace: () => (
    <label data-testid="mobile-asset-workspace">
      Mobile asset draft
      <input aria-label="Mobile workspace draft" />
    </label>
  ),
}));
vi.mock("../features/voice/VoicePage", () => ({
  VoicePage: () => <div data-testid="voice-page" />,
}));
vi.mock("../features/casting/CastingPage", () => ({
  CastingPage: () => <div data-testid="casting-page" />,
}));
vi.mock("../features/set/SetDesignPage", () => ({
  SetDesignPage: () => <div data-testid="set-page" />,
}));
vi.mock("../features/props/PropsPage", () => ({
  PropsPage: () => <div data-testid="props-page" />,
}));
vi.mock("../features/director/DirectorPage", () => ({
  DirectorPage: ({
    chatOnly,
    mobile,
    requestedMessage,
  }: {
    chatOnly?: boolean;
    mobile?: boolean;
    requestedMessage?: { id: string; projectId: string; message: string } | null;
  }) => (
    <div
      data-testid="director-page"
      data-chat-only={String(Boolean(chatOnly))}
      data-mobile={String(Boolean(mobile))}
    >
      {requestedMessage?.message || ""}
    </div>
  ),
}));
vi.mock("../features/production/ProductionPage", () => ({
  ProductionPage: ({
    active,
    mobile,
    onReviewMaterials,
  }: {
    active?: boolean;
    mobile?: boolean;
    onReviewMaterials?: (shot: Shot, shotNumber: number) => void;
  }) => (
    <main
      aria-label={mobile ? "Mobile production review" : "Desktop production workspace"}
      data-testid="production-page"
      data-active={String(Boolean(active))}
      data-mobile={String(Boolean(mobile))}
    >
      <button
        type="button"
        onClick={() => onReviewMaterials?.({ id: "sht_3", title: "Taking the Chair" } as Shot, 3)}
      >
        Simulate material save
      </button>
    </main>
  ),
}));
vi.mock("../features/json-production/JsonProductionPage", () => ({
  JsonProductionPage: ({ active }: { active?: boolean }) => (
    <div data-testid="json-production-page" data-active={String(Boolean(active))} />
  ),
}));

describe("App mode routing", () => {
  afterEach(cleanup);

  it("opens desktop Settings outside the project workflow navigation", async () => {
    render(<App />);
    const settings = screen.getByRole("button", { name: "Settings" });
    expect(screen.getByRole("navigation", { name: "Project workflow" }).contains(settings)).toBe(false);
    fireEvent.click(settings);
    expect(await screen.findByRole("heading", { name: "Workflow settings" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Director" }));
    expect(screen.queryByRole("heading", { name: "Workflow settings" })).toBeNull();
  });

  it("does not expose workflow setup in the mobile shell", () => {
    window.history.replaceState({}, "", "/mobile");
    render(<App />);
    expect(screen.queryByRole("button", { name: "Settings" })).toBeNull();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    window.history.replaceState({}, "", "/");
    projectState.project = {
      id: "prj_director",
      name: "Director film",
      script_text: "",
      mode: "director",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      shot_ids: [],
    };
  });

  it("mounts ProductionPage for director-mode projects on the Production tab", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Production" }));

    expect(screen.getByTestId("production-page")).toBeTruthy();
    expect(screen.queryByTestId("json-production-page")).toBeNull();
  });

  it("mounts JsonProductionPage for json_production projects on the Production tab", () => {
    projectState.project = {
      id: "prj_json",
      name: "JSON board",
      script_text: "",
      mode: "json_production",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      shot_ids: [],
    };
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Production" }));

    expect(screen.getByTestId("json-production-page")).toBeTruthy();
    expect(screen.queryByTestId("production-page")).toBeNull();
  });

  it("unmounts the previous Production page when project mode changes", () => {
    const { rerender } = render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Production" }));
    expect(screen.getByTestId("production-page")).toBeTruthy();

    projectState.project = {
      id: "prj_json",
      name: "JSON board",
      script_text: "",
      mode: "json_production",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      shot_ids: [],
    };
    rerender(<App />);

    expect(screen.getByTestId("json-production-page")).toBeTruthy();
    expect(screen.queryByTestId("production-page")).toBeNull();
  });

  it("uses the three-stage desktop workflow and opens Assets as one workspace", () => {
    render(<App />);
    expect(screen.getByRole("button", { name: "Assets" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Director" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Production" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Voice" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Assets" }));
    expect(screen.getByTestId("asset-workspace")).toBeTruthy();
  });

  it("opens desktop Director chat with a saved-material review request", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Production" }));
    fireEvent.click(screen.getByRole("button", { name: "Simulate material save" }));

    expect(screen.getByRole("button", { name: "Director" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByTestId("director-page").textContent).toContain(
      "Shot 03 references changed",
    );
  });

  it("opens mobile Director chat with a saved-material review request", () => {
    window.history.replaceState({}, "", "/mobile");
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Production" }));
    fireEvent.click(screen.getByRole("button", { name: "Simulate material save" }));

    expect(screen.getByRole("button", { name: "Director" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByTestId("director-page").textContent).toContain(
      "Shot 03 references changed",
    );
  });

  it("keeps the desktop brand, workflow, project controls, and health in one header row", () => {
    render(<App />);

    const header = screen.getByRole("banner", { name: "Application header" });
    expect(header.querySelectorAll(":scope > .topbar-row")).toHaveLength(1);
    expect(header.querySelector(".workflow-nav")).toBeTruthy();
    expect(header.querySelector("[data-testid='project-picker']")).toBeTruthy();
    expect(screen.queryByText("01")).toBeNull();
    expect(screen.queryByText("02")).toBeNull();
    expect(screen.queryByText("03")).toBeNull();
  });

  it("uses the Director mark and labels the compact ComfyUI status", async () => {
    render(<App />);

    const mark = screen.getByLabelText("Director Studio brand");
    expect(mark.querySelector("svg")).toBeTruthy();
    expect(mark.textContent).not.toContain("DS");
    expect(await screen.findByLabelText("ComfyUI online")).toBeTruthy();
    expect(screen.getByText("ComfyUI")).toBeTruthy();
  });

  it("starts desktop projects in the project-wide Director workspace", () => {
    render(<App />);

    expect(document.querySelector(".app.director-page-active")).toBeTruthy();
    expect(screen.getByTestId("director-page").closest("[hidden]")).toBeNull();
    expect(screen.getByTestId("asset-workspace").closest("[hidden]")).not.toBeNull();
  });

  it("puts three mobile workspace tabs in the second header row", () => {
    window.history.replaceState({}, "", "/mobile");
    render(<App />);

    const header = screen.getByRole("banner", { name: "Mobile application header" });
    expect(header.querySelectorAll(":scope > .mobile-topbar-row")).toHaveLength(2);
    expect(header.querySelector(".mobile-workspace-nav")).toBeTruthy();
    expect(document.querySelector(".mobile-tabbar")).toBeNull();
    expect(screen.getByRole("button", { name: "Asset" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Director" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Production" })).toBeTruthy();
    expect(screen.getByTestId("director-page").dataset.mobile).toBe("true");
    expect(screen.getByTestId("director-page").dataset.chatOnly).toBe("true");

    fireEvent.click(screen.getByRole("button", { name: "Asset" }));
    expect(screen.getByTestId("mobile-asset-workspace")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Production" }));
    expect(screen.getByRole("main", { name: "Mobile production review" })).toBeTruthy();
  });

  it("preserves the mobile asset workspace while switching main tabs", () => {
    window.history.replaceState({}, "", "/mobile");
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "Asset" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Mobile workspace draft" }), {
      target: { value: "Do not discard this input" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Director" }));
    fireEvent.click(screen.getByRole("button", { name: "Asset" }));

    expect((screen.getByRole("textbox", { name: "Mobile workspace draft" }) as HTMLInputElement).value).toBe("Do not discard this input");
  });

  it("automatically uses the mobile shell at the root URL on a narrow device", () => {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: query.includes("max-width: 840px"),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));

    render(<App />);

    expect(screen.getByRole("banner", { name: "Mobile application header" })).toBeTruthy();
    expect(screen.queryByRole("banner", { name: "Application header" })).toBeNull();
  });

  it("applies the Oat & Walnut workspace theme to desktop and mobile shells", () => {
    const { container, rerender } = render(<App />);
    expect(container.querySelector(".app")?.getAttribute("data-theme")).toBe("oat-walnut");

    window.history.replaceState({}, "", "/mobile");
    rerender(<App />);
    expect(container.querySelector(".mobile-app")?.getAttribute("data-theme")).toBe("oat-walnut");
  });
});
