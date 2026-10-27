import { LibraryPage } from "../library/LibraryPage";

export function MobileAssetWorkspace() {
  return <main className="mobile-asset-workspace" aria-label="Asset Library">
    <header className="asset-workspace-header"><div><h1>Library</h1>
      <p>Import, organize and describe reusable reference images and audio.</p></div></header>
    <LibraryPage mobile />
  </main>;
}
