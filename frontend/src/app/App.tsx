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
import { NAV_ITEMS, type NavId } from "./navigation";
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
  const reviewMaterials = (shot: Shot, shotNumber: number) => {
    requestSequence.current += 1;
    setDirectorRequest(materialReviewRequest(shot, shotNumber, requestSequence.current));
    setPage("director");
  };

  return (
    <div className="mobile-app" data-theme="oat-walnut">
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

        <nav className="mobile-topbar-row mobile-workspace-nav" aria-label="Mobile workspace">
          <button
            type="button"
            className={page === "asset" ? "active" : ""}
            aria-current={page === "asset" ? "page" : undefined}
            onClick={() => setPage("asset")}
          >
            Asset
          </button>
          <button
            type="button"
            className={page === "director" ? "active" : ""}
            aria-current={page === "director" ? "page" : undefined}
            onClick={() => setPage("director")}
          >
            Director
          </button>
          <button
            type="button"
            className={page === "production" ? "active" : ""}
            aria-current={page === "production" ? "page" : undefined}
            onClick={() => setPage("production")}
          >
            Production
          </button>
        </nav>
      </header>

      <div className="mobile-page mobile-asset-page" hidden={page !== "asset"}>
        <MobileAssetWorkspace />
      </div>
      <div className="mobile-page mobile-director-page" hidden={page !== "director"}>
        <DirectorPage mobile chatOnly requestedMessage={directorRequest} />
      </div>
      <div className="mobile-page mobile-production-page" hidden={page !== "production"}>
        <ProductionPage
          active={page === "production"}
          mobile
          onReviewMaterials={reviewMaterials}
        />
      </div>
    </div>
  );
}

function AppShell() {
  const [page, setPage] = useState<NavId>("director");
  const [directorRequest, setDirectorRequest] = useState<DirectorChatRequest | null>(null);
  const requestSequence = useRef(0);
  const [health, setHealth] = useState<{
    comfy_reachable: boolean;
    comfy_error: string | null;
  } | null>(null);
  const { project } = useProject();
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
    <div className={`app${page === "director" ? " director-page-active" : ""}`} data-theme="oat-walnut">
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

          <nav className="workflow-nav" aria-label="Project workflow">
            {NAV_ITEMS.map((item) => (
              <button
                key={item.id}
                type="button"
                className={page === item.id ? "active" : ""}
                aria-label={item.label}
                aria-current={page === item.id ? "page" : undefined}
                onClick={() => setPage(item.id)}
              >
                {item.label}
              </button>
            ))}
          </nav>

          <div
            className={`health ${health?.comfy_reachable ? "ok" : "bad"}`}
            aria-label={`ComfyUI ${health?.comfy_reachable ? "online" : "offline"}`}
            title={`ComfyUI ${health?.comfy_reachable ? "online" : "offline"}`}
          >
            <span className="dot" />
            <span className="health-label">ComfyUI</span>
          </div>
        </div>
      </header>

      {/* Keep pages mounted so in-flight job UI/polling survives tab switches */}
      <div
        className={page === "assets" ? "page-pane active" : "page-pane"}
        hidden={page !== "assets"}
      >
        <AssetWorkspace />
      </div>
      <div
        className={page === "director" ? "page-pane active" : "page-pane"}
        hidden={page !== "director"}
      >
        <DirectorPage requestedMessage={directorRequest} />
      </div>
      <div
        className={page === "production" ? "page-pane active" : "page-pane"}
        hidden={page !== "production"}
      >
        {project?.mode === "json_production" ? (
          <JsonProductionPage active={page === "production"} />
        ) : (
          <ProductionPage
            active={page === "production"}
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
