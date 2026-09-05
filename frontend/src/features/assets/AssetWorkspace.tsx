import { useState } from "react";
import { useProject } from "../../shared/project/ProjectContext";
import { CastingPage } from "../casting/CastingPage";
import type { LibraryKind } from "../library/api";
import { PropsPage } from "../props/PropsPage";
import { SetDesignPage } from "../set/SetDesignPage";
import { LibraryOverview } from "./LibraryOverview";
import { AssetImportDialog } from "../library/AssetImportDialog";

type PreparedAssetCategory = Exclude<LibraryKind, "layouts" | "costumes">;
type AssetCategory = "library" | PreparedAssetCategory;

const CATEGORIES: { id: AssetCategory; label: string; eyebrow: string }[] = [
  { id: "library", label: "Library", eyebrow: "All reusable assets" },
  { id: "actors", label: "Actors", eyebrow: "Cast identity" },
  { id: "scenes", label: "Scenes", eyebrow: "World and locations" },
  { id: "props", label: "Props", eyebrow: "Story objects" },
  { id: "voices", label: "Voices", eyebrow: "Performance reference" },
];

const IMPORT_LABELS: Record<PreparedAssetCategory, string> = {
  actors: "actor",
  scenes: "scene",
  props: "prop",
  voices: "voice",
};

export function AssetWorkspace() {
  const { projectId } = useProject();
  const [category, setCategory] = useState<AssetCategory>("library");
  const [importKind, setImportKind] = useState<PreparedAssetCategory | null>(null);

  return (
    <main className="asset-workspace" aria-label="Asset preparation">
      <header className="asset-workspace-header">
        <div>
          <h1>Assets</h1>
          <p>
            Build the reusable cast, locations, objects, and voices the Director can
            assign across every Shot.
          </p>
        </div>
      </header>

      {!projectId ? <div className="banner">Select a project to prepare its assets.</div> : null}

      <div className="asset-workspace-body">
        <nav className="asset-category-rail" aria-label="Asset categories">
          {CATEGORIES.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`asset-category-main ${category === item.id ? "active" : ""}`}
              aria-label={item.label}
              aria-current={category === item.id ? "page" : undefined}
              onClick={() => setCategory(item.id)}
            >
              <strong>{item.label}</strong>
              <span>{item.eyebrow}</span>
            </button>
          ))}
        </nav>

        <section className="asset-category-content" aria-live="polite">
          <div className="asset-library-overview-panel" hidden={category !== "library"}>
            <LibraryOverview onSelectKind={setCategory} />
          </div>
          {CATEGORIES.filter((item) => item.id !== "library").map((item) => {
            const workflowCategory = item.id as PreparedAssetCategory;
            return (
              <div
                key={workflowCategory}
                className="asset-preparation-panel"
                hidden={category !== workflowCategory}
              >
              <div className="asset-workflow-toolbar">
                <span>Already have a reference?</span>
                <button
                  type="button"
                  className={`btn ${workflowCategory === "voices" ? "primary" : "secondary"}`}
                  disabled={!projectId}
                  onClick={() => setImportKind(workflowCategory)}
                >
                  Import {IMPORT_LABELS[workflowCategory]}
                </button>
              </div>
              {workflowCategory === "actors" ? <CastingPage onOpenLibrary={() => setCategory("library")} /> : null}
              {workflowCategory === "scenes" ? <SetDesignPage onOpenLibrary={() => setCategory("library")} /> : null}
              {workflowCategory === "props" ? <PropsPage onOpenLibrary={() => setCategory("library")} /> : null}
              {workflowCategory === "voices" ? (
                <div className="asset-guidance-card">
                  <div className="workspace-kicker">Voice preparation</div>
                  <h2>Import a clean performance sample</h2>
                  <p>Two to fifteen seconds with a clear speaker name gives the Director a reliable casting reference.</p>
                </div>
              ) : null}
            </div>
            );
          })}
        </section>
      </div>
      {importKind && projectId ? (
        <AssetImportDialog
          kind={importKind}
          projectId={projectId}
          onClose={() => setImportKind(null)}
          onImported={() => {
            setImportKind(null);
          }}
        />
      ) : null}
    </main>
  );
}
