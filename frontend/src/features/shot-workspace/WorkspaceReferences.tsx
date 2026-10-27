import { useEffect, useState } from "react";
import type { RefRole, Shot } from "../../shared/api/types";
import { listLibraryAssets, type LibraryAsset, type LibraryKind } from "../library/api";
import { replaceShotMaterials, type ShotMaterialSelection } from "./api";
import { StudioIcon } from "../../shared/components/StudioIcon";
import "./referenceDesign.css";

const KINDS: LibraryKind[] = ["actors", "costumes", "scenes", "props", "layouts"];
const ROLES: Record<string, RefRole> = { actors: "actor", costumes: "costume", scenes: "scene", props: "prop", layouts: "layout_ref_frame" };
const LABELS: Record<string, string> = { actors: "Actors", costumes: "Wardrobe", scenes: "Scenes", props: "Props", layouts: "Layouts" };
const identity = (value: ShotMaterialSelection) => `${value.asset_id}:${value.file_key || ""}`;

export function ShotMaterialEditor({ shot, onClose, onOpenImage, onSaved }: {
  shot: Shot; shots?: Shot[]; shotNumber?: number; manualReferences?: boolean;
  onClose: () => void; onOpenImage: (url: string) => void;
  onSaved?: (shot: Shot, note: string, write: boolean) => void;
}) {
  const [assets, setAssets] = useState<LibraryAsset[]>([]);
  const [materials, setMaterials] = useState<ShotMaterialSelection[]>(shot.refs.map((ref) => ({ ...ref })));
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let current = true;
    void Promise.all(KINDS.map((kind) => listLibraryAssets(kind, shot.project_id))).then((groups) => {
      if (current) setAssets(groups.flat());
    }).catch((cause) => { if (current) setError(String(cause)); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [shot.project_id]);
  async function save(write: boolean) {
    setBusy(true); setError("");
    try {
      const ordered = [...materials.filter((ref) => ref.role !== "layout_ref_frame"), ...materials.filter((ref) => ref.role === "layout_ref_frame")];
      const saved = await replaceShotMaterials(shot.id, ordered, { expectedRefs: shot.refs, revisionRequest: note });
      onSaved?.(saved, note, write); onClose();
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }
  function move(index: number, delta: number) {
    setMaterials((current) => { const values = [...current]; [values[index], values[index + delta]] = [values[index + delta], values[index]]; return values; });
  }
  return <div className="shot-material-editor-backdrop workspace-reference-backdrop"><section role="dialog" aria-modal="true" aria-label="Shot references" className="shot-material-editor workspace-reference-dialog">
    <header className="manual-description-label"><div className="ref-dialog-title"><span className="ref-dialog-mark"><StudioIcon name="library" size={22} /></span><div><h2>Link references</h2><p>Choose the images that define this shot.</p></div></div><button type="button" className="ref-close" aria-label="Close references" disabled={busy} onClick={onClose}><StudioIcon name="close" /></button></header>
    <div className="workspace-reference-body">
    <details className="ref-selected-panel" open={materials.length > 0}><summary>Selected references <span>{materials.length} / 9</span></summary>
    <section aria-label="Selected references" className="workspace-reference-selection">
      {materials.map((material, index) => { const asset = assets.find((value) => value.id === material.asset_id); const url = asset?.urls[material.file_key || "master"];
        return <article key={identity(material)} className="workspace-reference-row">
          <button type="button" className="ref-selected-image" disabled={!url} onClick={() => url && onOpenImage(url)}>{url ? <img src={url} alt={`Picture ${index + 1}`} /> : <StudioIcon name="scenes" />}</button>
          <div><strong>P{index + 1} · {asset?.name || "Selected reference"}</strong>
            <textarea aria-label={`Reference ${index + 1} note`} rows={2} value={material.notes || ""} disabled={busy}
              placeholder="Optional direction for this reference" onChange={(event) => setMaterials((current) => current.map((ref, position) => position === index ? { ...ref, notes: event.target.value } : ref))} /></div>
          <div><button className="mode-chip" disabled={busy || index === 0} onClick={() => move(index, -1)} aria-label={`Move reference ${index + 1} up`}>↑</button>
            <button className="mode-chip" disabled={busy || index === materials.length - 1} onClick={() => move(index, 1)} aria-label={`Move reference ${index + 1} down`}>↓</button>
            <button className="mode-chip" disabled={busy} aria-label={`Remove reference ${index + 1}`} title="Remove reference" onClick={() => setMaterials((current) => current.filter((_, position) => position !== index))}><StudioIcon name="close" size={14} /></button></div>
        </article>; })}
    </section></details>
    <div className="ref-library-toolbar"><label className="ref-search"><StudioIcon name="search" /><input type="search" aria-label="Search references" placeholder="Search references" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
    <select aria-label="Reference category" value={filter} onChange={(event) => setFilter(event.target.value)}><option value="all">All images</option>{KINDS.map((kind) => <option key={kind} value={kind}>{LABELS[kind]}</option>)}</select></div>
    {loading && <p role="status" className="ref-empty">Loading library…</p>}
    {!loading && !assets.some((asset) => (filter === "all" || asset.kind === filter) && `${asset.name} ${asset.notes}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()) && Object.keys(asset.files).some((key) => /\.(png|jpe?g|webp|gif)$/i.test(asset.files[key] || "") && asset.urls[key])) && <p className="ref-empty">No matching images. Import images in Assets, or try another search.</p>}
    <div className="workspace-reference-library">{assets.filter((asset) => (filter === "all" || asset.kind === filter) && `${asset.name} ${asset.notes}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())).flatMap((asset) => Object.keys(asset.files)
      .filter((key) => /\.(png|jpe?g|webp|gif)$/i.test(asset.files[key] || "") && asset.urls[key]).map((key) => {
        const selected = materials.some((ref) => identity(ref) === `${asset.id}:${key}`);
        return <article key={`${asset.id}:${key}`} className={selected ? "ref-library-card is-selected" : "ref-library-card"}><button type="button" className="ref-library-image" aria-label={`Preview ${asset.name} ${key}`} onClick={() => onOpenImage(asset.urls[key])}><img src={asset.urls[key]} alt={asset.name} loading="lazy" /></button>
          <div className="ref-library-meta"><strong>{asset.name}</strong><small>{key.replaceAll("_", " ")}</small>
          {asset.notes && <details className="ref-asset-description"><summary>Description</summary><p>{asset.notes}</p></details>}
          <button type="button" className="ref-add-button" aria-label={selected ? `${asset.name} ${key} selected` : `Add ${asset.name} ${key}`} disabled={busy || selected || materials.length >= 9}
            onClick={() => setMaterials((current) => current.some((ref) => identity(ref) === `${asset.id}:${key}`) || current.length >= 9 ? current : [...current, { role: ROLES[asset.kind], asset_id: asset.id, file_key: key, notes: "" }])}><StudioIcon name={selected ? "check" : "plus"} size={16} />{selected ? "Selected" : "Add reference"}</button></div></article>;
      }))}</div>
    <details className="ref-set-note"><summary>Notes for this reference set <span>Optional</span></summary><textarea aria-label="Reference-set note" placeholder="How should these references be used together?" value={note} rows={2} disabled={busy} onChange={(event) => setNote(event.target.value)} /></details>
    {error && <p role="alert" className="banner error">{error}</p>}
    </div>
    <footer className="manual-shot-actions"><button className="btn secondary" disabled={busy || loading} onClick={() => void save(false)}>{busy ? "Saving…" : "Save references"}</button>
      <button className="btn primary" disabled={busy || loading || !materials.length} onClick={() => void save(true)}>Save & write prompt</button></footer>
  </section></div>;
}
