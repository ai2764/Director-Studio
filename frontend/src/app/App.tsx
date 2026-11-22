import { useEffect, useRef, useState } from "react";
import { ChevronDown, Settings } from "lucide-react";
import { AssetWorkspace } from "../features/assets/AssetWorkspace";
import { MobileAssetWorkspace } from "../features/assets/MobileAssetWorkspace";
import { DirectorPage, type DirectorChatRequest } from "../features/director/DirectorPage";
import { materialReviewMessage } from "../features/director/materialReview";
import { JsonProductionPage } from "../features/json-production/JsonProductionPage";
import { ProductionPage } from "../features/production/ProductionPage";
import { WorkflowSettingsPage } from "../features/settings/WorkflowSettingsPage";
import { fetchHealth } from "../shared/api/client";
import type { Shot } from "../shared/api/types";
import { DirectorStudioMark } from "../shared/components/DirectorStudioMark";
import { AuthoringModeSelector } from "../shared/project/AuthoringModeSelector";
import { ProjectProvider, useProject } from "../shared/project/ProjectContext";
import { ProjectPicker } from "../shared/project/ProjectPicker";
import { ThemeProvider, ThemeToggle, useTheme } from "../shared/theme/ThemeContext";
import { StatusDot } from "../shared/ui/badge";
import { cn } from "../shared/ui/cn";
import { TooltipProvider } from "../shared/ui/tooltip";
import { navigationForMode, type DesktopPage } from "./navigation";

function useShotsMode() {
  const { project } = useProject();
  return project?.mode === "director" && project.authoring_mode === "shot_workspace";
}

function materialReviewRequest(
  shot: Shot,
  shotNumber: number,
  sequence: number,
  message = "",
): DirectorChatRequest {
  return {
    id: `material-review-${shot.id}-${sequence}`,
    projectId: shot.project_id,
    message: materialReviewMessage(shot, shotNumber, message),
  };
}

function MobileAppShell() {
  const { theme } = useTheme();
  const shotsMode = useShotsMode();
  const [page, setPage] = useState<"asset" | "director" | "production">("director");
  const [directorRequest, setDirectorRequest] = useState<DirectorChatRequest | null>(null);
  const requestSequence = useRef(0);
  const { project } = useProject();
  const jsonProductionMode = project?.mode === "json_production";
  const activePage = jsonProductionMode
    ? "production"
    : shotsMode && page === "production"
      ? "director"
      : page;
  const reviewMaterials = (shot: Shot, shotNumber: number, message: string) => {
    requestSequence.current += 1;
    setDirectorRequest(materialReviewRequest(shot, shotNumber, requestSequence.current, message));
    setPage("director");
  };

  return (
    <div
      className={cn(
        "mobile-app flex h-dvh min-h-dvh flex-col overflow-hidden bg-canvas text-ink",
        jsonProductionMode && "json-production-mobile-app",
      )}
      data-theme={theme}
    >
      <header
        className="mobile-topbar sticky top-0 z-30 border-b border-border-subtle bg-panel/95 backdrop-blur-md"
        aria-label="Mobile application header"
      >
        <div className="mobile-topbar-row mobile-topbar-primary flex items-center justify-between gap-2 px-3 py-2">
          <div className="mobile-brand-row flex items-center gap-2">
            <span className="brand-mark inline-flex items-center" aria-label="Director Studio brand">
              <DirectorStudioMark />
            </span>
            <div className="mobile-project-name">
              <strong className="text-[13px] font-semibold tracking-[-0.015em] text-ink">
                Director Studio
              </strong>
            </div>
          </div>

          <div className="flex items-center gap-1.5">
            <ThemeToggle />
            <details className="mobile-project-menu relative">
              <summary
                aria-label="Change project"
                className="inline-flex h-7 cursor-pointer list-none items-center gap-1 rounded-md border border-border-subtle bg-elevated px-2.5 text-xs font-medium text-ink hover:bg-hover"
              >
                <span className="max-w-[130px] truncate">{project?.name || "Project"}</span>
                <ChevronDown className="h-3.5 w-3.5 text-ink-muted" aria-hidden="true" />
              </summary>
              <div className="mobile-project-popover absolute right-0 top-full z-40 mt-1.5 flex w-[min(320px,calc(100vw-24px))] flex-col gap-3 rounded-lg border border-border-subtle bg-panel p-3 shadow-[var(--shadow-popover)]">
                <ProjectPicker />
                <AuthoringModeSelector />
              </div>
            </details>
          </div>
        </div>

        {!jsonProductionMode ? (
          <nav
            className={cn(
              "mobile-topbar-row mobile-workspace-nav flex items-center gap-1 border-t border-border-subtle bg-canvas/85 px-3 py-1.5",
              shotsMode && "mobile-shots-nav",
            )}
            aria-label="Mobile workspace"
          >
            <button
              type="button"
              className={cn(
                "flex-1 cursor-pointer rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                activePage === "asset"
                  ? "active bg-panel text-ink ring-1 ring-border-subtle/80 shadow-[var(--shadow-card)] dark:bg-elevated dark:ring-border-strong/70"
                  : "text-ink-secondary hover:text-ink",
              )}
              aria-current={activePage === "asset" ? "page" : undefined}
              onClick={() => setPage("asset")}
            >
              Asset
            </button>
            <button
              type="button"
              className={cn(
                "flex-1 cursor-pointer rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                activePage === "director"
                  ? "active bg-panel text-ink ring-1 ring-border-subtle/80 shadow-[var(--shadow-card)] dark:bg-elevated dark:ring-border-strong/70"
                  : "text-ink-secondary hover:text-ink",
              )}
              aria-current={activePage === "director" ? "page" : undefined}
              onClick={() => setPage("director")}
            >
              {shotsMode ? "Shots" : "Director"}
            </button>
            {!shotsMode && (
              <button
                type="button"
                className={cn(
                  "flex-1 cursor-pointer rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                  activePage === "production"
                    ? "active bg-panel text-ink ring-1 ring-border-subtle/80 shadow-[var(--shadow-card)] dark:bg-elevated dark:ring-border-strong/70"
                    : "text-ink-secondary hover:text-ink",
                )}
                aria-current={activePage === "production" ? "page" : undefined}
                onClick={() => setPage("production")}
              >
                Production
              </button>
            )}
          </nav>
        ) : null}
      </header>

      <div
        className="mobile-page mobile-asset-page min-h-0 flex-1 overflow-y-auto"
        hidden={activePage !== "asset"}
      >
        <MobileAssetWorkspace />
      </div>
      <div
        className="mobile-page mobile-director-page min-h-0 flex-1 overflow-y-auto"
        hidden={activePage !== "director"}
      >
        <DirectorPage
          mobile
          chatOnly
          active={activePage === "director"}
          requestedMessage={directorRequest}
        />
      </div>
      {(!shotsMode || jsonProductionMode) && (
        <div
          className={cn(
            "mobile-page mobile-production-page min-h-0 flex-1 overflow-y-auto",
            project?.mode === "json_production" && "mobile-json-production-page",
          )}
          hidden={activePage !== "production"}
        >
          {jsonProductionMode ? (
            <JsonProductionPage active mobile />
          ) : (
            <ProductionPage
              active={activePage === "production"}
              mobile
              onReviewMaterials={reviewMaterials}
            />
          )}
        </div>
      )}
    </div>
  );
}

function AppShell() {
  const { theme } = useTheme();
  const shotsMode = useShotsMode();
  const [page, setPage] = useState<DesktopPage>("director");
  const [settingsVisited, setSettingsVisited] = useState(false);
  const settingsReturnPage = useRef<Exclude<DesktopPage, "settings">>("director");
  const [directorRequest, setDirectorRequest] = useState<DirectorChatRequest | null>(null);
  const [jsonProductionToolbarTarget, setJsonProductionToolbarTarget] =
    useState<HTMLDivElement | null>(null);
  const requestSequence = useRef(0);
  const [health, setHealth] = useState<{
    comfy_reachable: boolean;
    comfy_error: string | null;
  } | null>(null);
  const { project } = useProject();
  const jsonProductionMode = project?.mode === "json_production";
  const activePage =
    jsonProductionMode && page !== "settings"
      ? "production"
      : shotsMode && page === "production"
        ? "director"
        : page;
  const openSettings = () => {
    if (activePage !== "settings") settingsReturnPage.current = activePage;
    setSettingsVisited(true);
    setPage("settings");
  };
  const closeSettings = () => setPage(settingsReturnPage.current);
  const reviewMaterials = (shot: Shot, shotNumber: number, message: string) => {
    requestSequence.current += 1;
    setDirectorRequest(materialReviewRequest(shot, shotNumber, requestSequence.current, message));
    setPage("director");
  };

  useEffect(() => {
    fetchHealth()
      .then((h) => setHealth(h))
      .catch(() => setHealth({ comfy_reachable: false, comfy_error: "unreachable" }));
  }, []);

  return (
    <div
      className={cn(
        "app flex min-h-screen flex-col bg-canvas text-ink",
        activePage === "director" && "director-page-active h-dvh min-h-0 overflow-hidden",
        jsonProductionMode && "json-production-app",
      )}
      data-theme={theme}
    >
      <header
        className="topbar sticky top-0 z-30 border-b border-border-subtle bg-panel/95 shadow-none backdrop-blur-md"
        aria-label="Application header"
      >
        <div className="topbar-row flex h-12 min-h-0 items-center gap-3 px-4 py-0">
          <div className="brand flex shrink-0 items-center gap-2">
            <span className="brand-mark inline-flex items-center" aria-label="Director Studio brand">
              <DirectorStudioMark />
            </span>
            <div className="brand-text">
              <div className="brand-title text-[13px] font-semibold tracking-[-0.015em] text-ink">
                Director Studio
              </div>
            </div>
          </div>

          <div className="topbar-project flex shrink-0 items-center border-l border-border-subtle pl-3">
            <ProjectPicker allowDelete />
          </div>

          {jsonProductionMode ? (
            <div
              className="json-production-topbar-tools flex min-w-0 flex-1 items-center gap-2"
              ref={setJsonProductionToolbarTarget}
              aria-label="JSON production controls"
            />
          ) : null}

          {!jsonProductionMode ? (
            <div className="flex shrink-0 items-center border-l border-border-subtle pl-3">
              <nav
                className="workflow-nav static left-auto inline-flex translate-x-0 items-center gap-0.5 rounded-md border border-border-subtle bg-canvas/90 p-0.5 dark:bg-canvas"
                aria-label="Project workflow"
              >
                {navigationForMode(project?.mode)
                  .filter((item) => !shotsMode || item.id !== "production")
                  .map((item) => {
                    const label = shotsMode && item.id === "director" ? "Shots" : item.label;
                    const isActive = activePage === item.id;
                    return (
                      <button
                        key={item.id}
                        type="button"
                        className={cn(
                          "h-6 cursor-pointer rounded px-2.5 py-0 text-xs font-medium transition-colors",
                          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                          isActive
                            ? "active bg-panel text-ink ring-1 ring-border-subtle/80 shadow-[var(--shadow-card)] dark:bg-elevated dark:ring-border-strong/70"
                            : "text-ink-secondary hover:text-ink",
                        )}
                        aria-label={label}
                        aria-current={isActive ? "page" : undefined}
                        onClick={() => setPage(item.id)}
                      >
                        {label}
                      </button>
                    );
                  })}
              </nav>
            </div>
          ) : null}

          <AuthoringModeSelector />

          <div className="ml-auto flex shrink-0 items-center gap-2">
            <div
              className={cn(
                "health inline-flex items-center gap-1.5 rounded-full border border-border-subtle bg-elevated/70 px-2.5 py-1 text-[11px] font-medium text-ink-secondary",
                health?.comfy_reachable ? "ok" : "bad",
              )}
              aria-label={`ComfyUI ${health?.comfy_reachable ? "online" : "offline"}`}
              title={`ComfyUI ${health?.comfy_reachable ? "online" : "offline"}`}
            >
              <StatusDot tone={health?.comfy_reachable ? "ok" : "danger"} />
              <span className="health-label">ComfyUI</span>
            </div>

            <ThemeToggle />

            <button
              type="button"
              className={cn(
                "topbar-settings inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                activePage === "settings"
                  ? "border-accent bg-accent-soft text-accent"
                  : "border-border-subtle bg-panel text-ink-secondary hover:bg-hover hover:text-ink",
              )}
              aria-current={activePage === "settings" ? "page" : undefined}
              onClick={openSettings}
            >
              <Settings className="h-3.5 w-3.5" aria-hidden="true" />
              Settings
            </button>
          </div>
        </div>
      </header>

      {/* Keep pages mounted so in-flight job UI/polling survives tab switches */}
      {settingsVisited ? (
        <div
          className={activePage === "settings" ? "page-pane active" : "page-pane"}
          hidden={activePage !== "settings"}
        >
          <WorkflowSettingsPage active={activePage === "settings"} onClose={closeSettings} />
        </div>
      ) : null}
      <div
        className={activePage === "assets" ? "page-pane active" : "page-pane"}
        hidden={activePage !== "assets"}
      >
        <AssetWorkspace />
      </div>
      <div
        className={activePage === "director" ? "page-pane active" : "page-pane"}
        hidden={activePage !== "director"}
      >
        <DirectorPage active={activePage === "director"} requestedMessage={directorRequest} />
      </div>
      {(!shotsMode || jsonProductionMode) && (
        <div
          className={activePage === "production" ? "page-pane active" : "page-pane"}
          hidden={activePage !== "production"}
        >
          {jsonProductionMode ? (
            <JsonProductionPage
              active={activePage === "production"}
              toolbarTarget={jsonProductionToolbarTarget}
            />
          ) : (
            <ProductionPage
              active={activePage === "production"}
              onReviewMaterials={reviewMaterials}
            />
          )}
        </div>
      )}
    </div>
  );
}

export default function App() {
  const forcedMobile = window.location.pathname.replace(/\/+$/, "") === "/mobile";
  const [narrowViewport, setNarrowViewport] = useState(
    () => window.matchMedia("(max-width: 840px)").matches,
  );

  useEffect(() => {
    document.title = "Director Studio";
  }, []);

  useEffect(() => {
    if (forcedMobile) return;
    const query = window.matchMedia("(max-width: 840px)");
    const update = () => setNarrowViewport(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, [forcedMobile]);

  const mobile = forcedMobile || narrowViewport;
  return (
    <ThemeProvider>
      <TooltipProvider delayDuration={300}>
        <ProjectProvider>{mobile ? <MobileAppShell /> : <AppShell />}</ProjectProvider>
      </TooltipProvider>
    </ThemeProvider>
  );
}
