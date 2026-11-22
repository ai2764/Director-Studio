import { LibraryPage } from "../library/LibraryPage";

export function AssetWorkspace() {
  return (
    <main
      className="asset-workspace flex min-h-0 flex-1 flex-col bg-canvas text-ink"
      aria-label="Asset Library"
    >
      <header className="asset-workspace-header shrink-0 border-b border-border-subtle bg-panel/60 px-5 py-3.5">
        <div className="mx-auto flex w-full max-w-[1440px] flex-col gap-0.5">
          <h1 className="text-sm font-semibold tracking-[-0.015em] text-ink">Library</h1>
          <p className="text-xs text-ink-secondary">
            Import, organize and describe reusable reference images and audio.
          </p>
        </div>
      </header>
      <LibraryPage />
    </main>
  );
}
