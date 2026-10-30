import { LibraryPage } from "../library/LibraryPage";

export function AssetWorkspace() {
  return <main className="asset-workspace" aria-label="Asset Library">
    <header className="asset-workspace-header"><div><h1>Library</h1>
      <p>Import, organize and describe reusable reference images and audio.</p></div></header>
    <LibraryPage />
  </main>;
}
