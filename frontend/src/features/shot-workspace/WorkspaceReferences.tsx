import { useEffect, useState } from "react";
import type { RefRole, Shot } from "../../shared/api/types";
import { listLibraryAssets, type LibraryAsset, type LibraryKind } from "../library/api";
import { replaceShotMaterials, type ShotMaterialSelection } from "./api";

const KINDS: LibraryKind[] = ["actors", "costumes", "scenes", "props", "layouts"];
const ROLES: Record<string, RefRole> = { actors: "actor", costumes: "costume", scenes: "scene", props: "prop", layouts: "layout_ref_frame" };
const identity = (value: ShotMaterialSelection) => `${value.asset_id}:${value.file_key || ""}`;

export function ShotMaterialEditor({ shot, onClose, onOpenImage, onSaved }: {
  shot: Shot; shots?: Shot[]; shotNumber?: number; manualReferences?: boolean;
  onClose: () => void; onOpenImage: (url: string) => void;
  onSaved?: (shot: Shot, note: string, write: boolean) => void;
}) {
  const [assets, setAssets] = useState<LibraryAsset[]>([]);
  const [materials, setMaterials] = useState<ShotMaterialSelection[]>(shot.refs.map((ref) => ({ ...ref })));
  const [filter, setFilter] = useState("all");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let current = true;
    void Promise.all(KINDS.map((kind) => listLibraryAssets(kind, shot.project_id))).then((groups) => {
      if (current) setAssets(groups.flat());
    }).catch((cause) => { if (current) setError(String(cause)); });
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
    <header className="manual-description-label"><h2>References</h2><button type="button" className="mode-chip" disabled={busy} onClick={onClose}>Close</button></header>
    <div className="workspace-reference-body">
    <section aria-label="Selected references" className="workspace-reference-selection">
      {materials.map((material, index) => { const asset = assets.find((value) => value.id === material.asset_id); const url = asset?.urls[material.file_key || "master"];
        return <article key={identity(material)} className="workspace-reference-row">
          {url && <button type="button" onClick={() => onOpenImage(url)}><img src={url} alt={`Picture ${index + 1}`} /></button>}
          <div><strong>P{index + 1} · {asset?.name || "Selected reference"}</strong>
            <textarea aria-label={`Reference ${index + 1} note`} rows={2} value={material.notes || ""} disabled={busy}
              placeholder="Optional direction for this reference" onChange={(event) => setMaterials((current) => current.map((ref, position) => position === index ? { ...ref, notes: event.target.value } : ref))} /></div>
          <div><button className="mode-chip" disabled={busy || index === 0} onClick={() => move(index, -1)} aria-label={`Move reference ${index + 1} up`}>↑</button>
            <button className="mode-chip" disabled={busy || index === materials.length - 1} onClick={() => move(index, 1)} aria-label={`Move reference ${index + 1} down`}>↓</button>
            <button className="mode-chip" disabled={busy} onClick={() => setMaterials((current) => current.filter((_, position) => position !== index))}>Remove</button></div>
        </article>; })}
    </section>
    <label>Category<select aria-label="Reference category" value={filter} onChange={(event) => setFilter(event.target.value)}><option value="all">All images</option>{KINDS.map((kind) => <option key={kind}>{kind}</option>)}</select></label>
    <div className="workspace-reference-library">{assets.filter((asset) => filter === "all" || asset.kind === filter).flatMap((asset) => Object.keys(asset.files)
      .filter((key) => /\.(png|jpe?g|webp|gif)$/i.test(asset.files[key] || "") && asset.urls[key]).map((key) => {
        const selected = materials.some((ref) => identity(ref) === `${asset.id}:${key}`);
        return <article key={`${asset.id}:${key}`}><button type="button" onClick={() => onOpenImage(asset.urls[key])}><img src={asset.urls[key]} alt={asset.name} /></button>
          <strong>{asset.name}</strong><small>{key}</small><button type="button" className="mode-chip" disabled={busy || selected || materials.length >= 9}
            onClick={() => setMaterials((current) => [...current, { role: ROLES[asset.kind], asset_id: asset.id, file_key: key, notes: "" }])}>{selected ? "Selected" : "Add"}</button></article>;
      }))}</div>
    <label>Reference-set note<textarea aria-label="Reference-set note" value={note} rows={2} onChange={(event) => setNote(event.target.value)} /></label>
    {error && <p role="alert" className="banner error">{error}</p>}
    </div>
    <footer className="manual-shot-actions"><button className="btn secondary" disabled={busy} onClick={() => void save(false)}>Save references</button>
      <button className="btn primary" disabled={busy || !materials.length} onClick={() => void save(true)}>Save & write prompt</button></footer>
  </section></div>;
}
