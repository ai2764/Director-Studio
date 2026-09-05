import { useState } from "react";
import type { LibraryKind } from "../library/api";
import { CastingPage } from "../casting/CastingPage";
import { PropsPage } from "../props/PropsPage";
import { SetDesignPage } from "../set/SetDesignPage";
import { MobileLibraryOverview } from "./MobileLibraryOverview";
import { AssetImportDialog } from "../library/AssetImportDialog";
import { useProject } from "../../shared/project/ProjectContext";

type MobileAssetCategory = "library" | Exclude<LibraryKind, "layouts" | "costumes">;

const CATEGORIES: { id: MobileAssetCategory; label: string }[] = [
  { id: "library", label: "Library" },
  { id: "actors", label: "Actors" },
  { id: "scenes", label: "Scenes" },
  { id: "props", label: "Props" },
  { id: "voices", label: "Voices" },
];

const WORKFLOW_COPY: Record<Exclude<MobileAssetCategory, "library">, { singular: string; description: string }> = {
  actors: { singular: "Actor", description: "Build a reusable character identity and reference sheet." },
  scenes: { singular: "Scene", description: "Build a reusable location with consistent viewing angles." },
  props: { singular: "Prop", description: "Turn a story object into a clean reusable reference." },
  voices: { singular: "Voice", description: "Prepare a clean performance sample for casting and H3." },
};

export function MobileAssetWorkspace() {
  const { projectId } = useProject();
  const [category, setCategory] = useState<MobileAssetCategory>("library");
  const [importKind, setImportKind] = useState<Exclude<MobileAssetCategory, "library"> | null>(null);

  return (
    <main className="mobile-asset-workspace">
      <header className="mobile-section-header">
        <div>
          <span className="mobile-eyebrow">Asset intake</span>
          <h1>Assets</h1>
        </div>
      </header>

      <nav className="mobile-category-strip" aria-label="Asset categories">
        {CATEGORIES.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`mobile-category-main ${category === item.id ? "active" : ""}`}
            aria-label={item.label}
            aria-current={category === item.id ? "page" : undefined}
            onClick={() => setCategory(item.id)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      <div hidden={category !== "library"}>
        <MobileLibraryOverview onSelectKind={setCategory} />
      </div>
      {CATEGORIES.filter((item) => item.id !== "library").map((item) => {
        const workflowCategory = item.id as Exclude<MobileAssetCategory, "library">;
        return (
        <div
          key={workflowCategory}
          className="mobile-asset-category-page"
          hidden={category !== workflowCategory}
        >
          <section className="mobile-workflow-callout">
            <div>
              <span className="mobile-eyebrow">Prepare {WORKFLOW_COPY[workflowCategory].singular}</span>
              <h2>{WORKFLOW_COPY[workflowCategory].singular} workflow</h2>
              <p>{WORKFLOW_COPY[workflowCategory].description}</p>
            </div>
            <button
              type="button"
              className={`btn ${workflowCategory === "voices" ? "primary" : "secondary"}`}
              disabled={!projectId}
              onClick={() => setImportKind(workflowCategory)}
            >
              Import {WORKFLOW_COPY[workflowCategory].singular.toLowerCase()}
            </button>
          </section>

          {workflowCategory !== "voices" ? (
            <section className="mobile-workflow-surface">
              {workflowCategory === "actors" ? <CastingPage onOpenLibrary={() => setCategory("library")} /> : null}
              {workflowCategory === "scenes" ? <SetDesignPage onOpenLibrary={() => setCategory("library")} /> : null}
              {workflowCategory === "props" ? <PropsPage onOpenLibrary={() => setCategory("library")} /> : null}
            </section>
          ) : (
            <p className="mobile-workflow-guidance">Import a clean source; the Director will assign it to Shots.</p>
          )}
        </div>
        );
      })}
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
