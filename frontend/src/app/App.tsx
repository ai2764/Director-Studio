import { useEffect, useRef, useState } from "react";
import { AssetWorkspace } from "../features/assets/AssetWorkspace";
import { MobileAssetWorkspace } from "../features/assets/MobileAssetWorkspace";
import { DirectorPage, type DirectorChatRequest } from "../features/director/DirectorPage";
import { materialReviewMessage } from "../features/director/materialReview";
import { JsonProductionPage } from "../features/json-production/JsonProductionPage";
import { ProductionPage } from "../features/production/ProductionPage";
import { fetchHealth } from "../shared/api/client";
import { ProjectProvider, useProject } from "../shared/project/ProjectContext";
import { ProjectPicker } from "../shared/project/ProjectPicker";
import { DirectorStudioMark } from "../shared/components/DirectorStudioMark";
import { NAV_ITEMS, type DesktopPage } from "./navigation";
import { WorkflowSettingsPage } from "../features/settings/WorkflowSettingsPage";
import type { Shot } from "../shared/api/types";

function materialReviewRequest(
  shot: Shot,
  shotNumber: number,
  sequence: number,
): DirectorChatRequest {
  return {
    id: `material-review-${shot.id}-${sequence}`,
    projectId: shot.project_id,
    message: materialReviewMessage(shot, shotNumber),
  };
}

function MobileAppShell() {
  const [page, setPage] = useState<"asset" | "director" | "production">("director");
  const [directorRequest, setDirectorRequest] = useState<DirectorChatRequest | null>(null);
  const requestSequence = useRef(0);
  const { project } = useProject();
  const jsonProductionMode = project?.mode === "json_production";
  const activePage = jsonProductionMode ? "production" : page;
  const reviewMaterials = (shot: Shot, shotNumber: number) => {
    requestSequence.current += 1;
    setDirectorRequest(materialReviewRequest(shot, shotNumber, requestSequence.current));
    setPage("director");
  };

  return (
    <div
      className={jsonProductionMode ? "mobile-app json-production-mobile-app" : "mobile-app"}
      data-theme="oat-walnut"
    >
      <header className="mobile-topbar" aria-label="Mobile application header">
        <div className="mobile-topbar-row mobile-topbar-primary">
          <div className="mobile-brand-row">
            <span className="brand-mark" aria-label="Director Studio brand"><DirectorStudioMark /></span>
            <div className="mobile-project-name">
              <strong>Director Studio</strong>
            </div>
          </div>
          <details className="mobile-project-menu">
            <summary aria-label="Change project">{project?.name || "Project"}</summary>
            <div className="mobile-project-popover"><ProjectPicker /></div>
          </details>
        </div>

        {!jsonProductionMode ? <nav className="mobile-topbar-row mobile-workspace-nav" aria-label="Mobile workspace">
          <button
            type="button"
            className={activePage === "asset" ? "active" : ""}
            aria-current={activePage === "asset" ? "page" : undefined}
            onClick={() => setPage("asset")}
          >
            Asset
          </button>
          <button
            type="button"
            className={activePage === "director" ? "active" : ""}
            aria-current={activePage === "director" ? "page" : undefined}
            onClick={() => setPage("director")}
          >
            Director
          </button>
          <button
            type="button"
            className={activePage === "production" ? "active" : ""}
            aria-current={activePage === "production" ? "page" : undefined}
            onClick={() => setPage("production")}
          >
            Production
          </button>
        </nav> : null}
      </header>

      <div className="mobile-page mobile-asset-page" hidden={activePage !== "asset"}>
        <MobileAssetWorkspace />
      </div>
      <div className="mobile-page mobile-director-page" hidden={activePage !== "director"}>
        <DirectorPage mobile chatOnly requestedMessage={directorRequest} />
      </div>
      <div
        className={`mobile-page mobile-production-page${
          project?.mode === "json_production" ? " mobile-json-production-page" : ""
        }`}
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
    </div>
  );
}

function AppShell() {
  const [page, setPage] = useState<DesktopPage>("director");
  const [settingsVisited, setSettingsVisited] = useState(false);
  const settingsReturnPage = useRef<Exclude<DesktopPage, "settings">>("director");
  const [directorRequest, setDirectorRequest] = useState<DirectorChatRequest | null>(null);
  const [jsonProductionToolbarTarget, setJsonProductionToolbarTarget] = useState<HTMLDivElement | null>(null);
  const requestSequence = useRef(0);
  const [health, setHealth] = useState<{
    comfy_reachable: boolean;
    comfy_error: string | null;
  } | null>(null);
  const { project } = useProject();
  const jsonProductionMode = project?.mode === "json_production";
  const activePage = jsonProductionMode && page !== "settings" ? "production" : page;
  const openSettings = () => {
    if (activePage !== "settings") settingsReturnPage.current = activePage;
    setSettingsVisited(true);
    setPage("settings");
  };
  const closeSettings = () => setPage(settingsReturnPage.current);
  const reviewMaterials = (shot: Shot, shotNumber: number) => {
    requestSequence.current += 1;
    setDirectorRequest(materialReviewRequest(shot, shotNumber, requestSequence.current));
    setPage("director");
  };

  useEffect(() => {
    fetchHealth()
      .then((h) => setHealth(h))
      .catch(() => setHealth({ comfy_reachable: false, comfy_error: "unreachable" }));
  }, []);

  return (
    <div
      className={`app${activePage === "director" ? " director-page-active" : ""}${
        jsonProductionMode ? " json-production-app" : ""
      }`}
      data-theme="oat-walnut"
    >
      <header className="topbar" aria-label="Application header">
        <div className="topbar-row">
          <div className="brand">
            <span className="brand-mark" aria-label="Director Studio brand"><DirectorStudioMark /></span>
            <div className="brand-text">
              <div className="brand-title">Director Studio</div>
            </div>
          </div>

          <div className="topbar-project">
            <ProjectPicker />
          </div>

          {jsonProductionMode ? (
            <div
              className="json-production-topbar-tools"
              ref={setJsonProductionToolbarTarget}
              aria-label="JSON production controls"
            />
          ) : null}

          {!jsonProductionMode ? <nav className="workflow-nav" aria-label="Project workflow">
            {NAV_ITEMS.map((item) => (
              <button
                key={item.id}
                type="button"
                className={activePage === item.id ? "active" : ""}
                aria-label={item.label}
                aria-current={activePage === item.id ? "page" : undefined}
                onClick={() => setPage(item.id)}
              >
                {item.label}
              </button>
            ))}
          </nav> : null}

          <div
            className={`health ${health?.comfy_reachable ? "ok" : "bad"}`}
            aria-label={`ComfyUI ${health?.comfy_reachable ? "online" : "offline"}`}
            title={`ComfyUI ${health?.comfy_reachable ? "online" : "offline"}`}
          >
            <span className="dot" />
            <span className="health-label">ComfyUI</span>
          </div>
          <button type="button" className="btn secondary topbar-settings" aria-current={activePage === "settings" ? "page" : undefined} onClick={openSettings}>Settings</button>
        </div>
      </header>

      {/* Keep pages mounted so in-flight job UI/polling survives tab switches */}
      {settingsVisited ? <div className={activePage === "settings" ? "page-pane active" : "page-pane"} hidden={activePage !== "settings"}><WorkflowSettingsPage active={activePage === "settings"} onClose={closeSettings} /></div> : null}
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
        <DirectorPage requestedMessage={directorRequest} />
      </div>
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
    <ProjectProvider>
      {mobile ? <MobileAppShell /> : <AppShell />}
    </ProjectProvider>
  );
}
