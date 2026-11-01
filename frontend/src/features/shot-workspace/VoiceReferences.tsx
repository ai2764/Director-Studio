import { useEffect, useRef, useState } from "react";
import type { Shot, ShotVoiceRef } from "../../shared/api/types";
import { parseError } from "../../shared/api/client";
import { listLibraryAssets, type LibraryAsset } from "../library/api";
import { AssetImportDialog } from "../library/AssetImportDialog";
import { WorkspaceMascot } from "./WorkspaceMascot";
import { useLibraryRevision } from "../library/libraryChanges";
import { StudioIcon } from "../../shared/components/StudioIcon";

export function VoiceReferences({shot, disabled, onSaved}: {shot:Shot; disabled:boolean; onSaved:(shot:Shot)=>void}) {
  const [open, setOpen] = useState(false);
  const [assets, setAssets] = useState<LibraryAsset[]>([]);
  const [refs, setRefs] = useState<ShotVoiceRef[]>(shot.voice_refs);
  const confirmed = useRef(shot.voice_refs);
  const saving = useRef(false);
  const [importing, setImporting] = useState(false);
  const [revision, setRevision] = useState(0);
  const {revision: libraryRevision, refresh} = useLibraryRevision(open);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { confirmed.current = shot.voice_refs; setRefs(shot.voice_refs); }, [shot.voice_refs]);
  useEffect(() => {
    if (!open) return;
    let current = true; setLoading(true); setError("");
    void listLibraryAssets("voices", shot.project_id).then((items) => { if (current) setAssets(items); })
      .catch((cause) => { if (current) setError(String(cause)); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [open, shot.project_id, revision, libraryRevision]);
  async function save(next: ShotVoiceRef[]) {
    if (saving.current || disabled || JSON.stringify(next) === JSON.stringify(confirmed.current)) return;
    saving.current = true; setRefs(next);
    setBusy(true); setError("");
    try {
      const response = await fetch(`/api/shot-workspace/shots/${shot.id}/voice-references`, {
        method:"PUT", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({voice_refs:next,expected_refs:confirmed.current}),
      });
      if (!response.ok) throw new Error(await parseError(response));
      const saved = await response.json() as Shot;
      confirmed.current = saved.voice_refs; setRefs(saved.voice_refs); onSaved(saved);
    } catch (cause) { setRefs(confirmed.current); setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { saving.current = false; setBusy(false); }
  }
  return <details className="manual-voice-references" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary><span className="workspace-summary-content"><WorkspaceMascot role="voice" /><span>Voice references · {shot.voice_refs.length} / 3</span></span></summary>
    {open && <div className="manual-voice-body">
      <p>Select a voice to link it immediately. Speakers are matched from sample names or filenames when writing the prompt. Up to 3 voices, 15 seconds total.</p>
      {shot.source_audio_path && <p>This shot uses native source audio. Remove it before linking voices.</p>}
      {refs.map((ref,index) => {
        const asset = assets.find((item) => item.id === ref.asset_id);
        return <div className="manual-voice-row" key={ref.asset_id}>
          <strong>Audio {index+1} · {asset?.name || ref.asset_id}</strong>
          {asset?.urls[ref.file_key] && <audio controls preload="none" src={asset.urls[ref.file_key]} aria-label={`Preview voice ${index+1}`} />}
          <details><summary>Optional voice settings</summary>
          <label>Speaker override (optional)<input aria-label={`Voice ${index+1} speaker`} value={ref.speaker} placeholder="Automatic from sample name or filename" disabled={disabled || busy}
            onBlur={() => void save(refs)}
            onChange={(event) => setRefs((current) => current.map((value,i) => i === index ? {...value,speaker:event.target.value} : value))} /></label>
          <label>Voice direction<input aria-label={`Voice ${index+1} direction`} value={ref.notes || ""} disabled={disabled || busy}
            onBlur={() => void save(refs)}
            onChange={(event) => setRefs((current) => current.map((value,i) => i === index ? {...value,notes:event.target.value} : value))} /></label></details>
          <button type="button" className="mode-chip" disabled={disabled || busy} onClick={() => void save(refs.filter((_,i) => i !== index).map((value,i) => ({...value,audio_index:i+1})))}>Remove voice {index+1}</button>
        </div>;
      })}
      <label>Add voice<select aria-label="Add voice reference" value="" disabled={disabled || busy || loading || refs.length >= 3 || !!shot.source_audio_path}
        onChange={(event) => { const asset = assets.find((item) => item.id === event.target.value); if (asset) void save([...refs,{asset_id:asset.id,audio_index:refs.length+1,file_key:"reference",speaker:"",notes:""}]); }}>
        <option value="">{loading ? "Loading voices…" : "Choose a voice"}</option>
        {assets.filter((asset) => asset.meta.h3_ready && asset.urls.reference && !refs.some((ref) => ref.asset_id === asset.id)).map((asset) => <option key={asset.id} value={asset.id}>{asset.name}</option>)}
      </select></label>
      {!loading && !assets.length && <small>No voice samples yet. Import one below.</small>}
      <div className="manual-shot-actions">
        <button type="button" className="btn secondary" disabled={disabled || busy} onClick={() => setImporting(true)}>Import voice</button>
        <button type="button" className="mode-chip" aria-label="Refresh voice library" title="Refresh voices" disabled={busy || loading} onClick={refresh}><StudioIcon name="refresh" /></button>
        {busy && <small role="status">Saving voice changes…</small>}
      </div>
      {error && <p role="alert">{error}</p>}
    </div>}
    {importing && <AssetImportDialog kind="voices" projectId={shot.project_id} onClose={() => setImporting(false)} onImported={() => {setImporting(false);setRevision((value) => value+1);}} />}
  </details>;
}
