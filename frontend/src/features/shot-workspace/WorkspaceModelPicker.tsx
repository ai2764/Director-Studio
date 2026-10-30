import { useEffect, useRef, useState } from "react";
import { getDirectorModel, setDirectorModel } from "../director/api";

export function WorkspaceModelPicker({disabled, onSwitching}: {disabled:boolean; onSwitching:(busy:boolean)=>void}) {
  const [open,setOpen] = useState(false);
  const [model,setModel] = useState("");
  const [models,setModels] = useState<string[]>([]);
  const [loading,setLoading] = useState(false);
  const [switching,setSwitching] = useState(false);
  const [error,setError] = useState("");
  const root = useRef<HTMLDetailsElement>(null);
  useEffect(()=>{
    if(!open)return;
    const close=(event:PointerEvent)=>{if(root.current&&event.target instanceof Node&&!root.current.contains(event.target))root.current.open=false;};
    document.addEventListener("pointerdown",close);
    return ()=>document.removeEventListener("pointerdown",close);
  },[open]);
  useEffect(()=>{
    if (!open) return;
    let current=true;setLoading(true);setError("");
    void getDirectorModel().then(status=>{if(current){setModel(status.model||"");setModels(status.available||[]);}})
      .catch(cause=>{if(current)setError(cause instanceof Error?cause.message:String(cause));})
      .finally(()=>{if(current)setLoading(false);});
    return ()=>{current=false;};
  },[open]);
  async function change(value:string){
    if(!value||value===model||disabled||switching)return;
    setSwitching(true);onSwitching(true);setError("");
    try{const status=await setDirectorModel(value,true);setModel(status.model||value);if(status.available)setModels(status.available);}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause));}
    finally{setSwitching(false);onSwitching(false);}
  }
  return <details ref={root} className="workspace-model-picker" onToggle={event=>setOpen(event.currentTarget.open)}
    onKeyDown={event=>{if(event.key==="Escape"){event.currentTarget.open=false;event.currentTarget.querySelector("summary")?.focus();}}}>
    <summary>AI model</summary>
    {open&&<div className="workspace-model-options">
      <label>AI model<select aria-label="Workspace AI model" value={model} disabled={disabled||loading||switching||!models.length}
        onChange={event=>void change(event.target.value)}>
        {!models.length&&<option value={model}>{loading?"Loading models…":model||"No models available"}</option>}
        {!!model&&!!models.length&&!models.includes(model)&&<option value={model} disabled>{model} (not available)</option>}
        {models.map(name=><option key={name} value={name}>{name}</option>)}
      </select></label>
      <small>Shared across projects for chat, description refinement and prompt writing.</small>
      {disabled&&<small>Finish the current action before switching.</small>}
      {switching&&<small role="status">Switching model…</small>}
      {error&&<p role="alert">Model selection failed: {error}</p>}
    </div>}
  </details>;
}
