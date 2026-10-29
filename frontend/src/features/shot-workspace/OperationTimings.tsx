import { useEffect, useState } from "react";

export type OperationTiming = {id:string;stage:string;duration_s:number;status:string;finished_at:string};
export const timingLabel:Record<string,string> = {refinement:"Refine",assistant:"Assistant",prompt_planning:"Prompt planning",prompt:"Write prompt"};
export function useOperationTimings(shotId:string,active:boolean){
  const [records,setRecords]=useState<OperationTiming[]>([]);
  useEffect(()=>{
    let current=true;
    async function refresh(){
      try{
        const response=await fetch(`/api/shot-workspace/shots/${shotId}/timings`,{cache:"no-store"});
        if(!response.ok)return;
        const data=await response.json() as {records?:OperationTiming[]};
        if(current)setRecords((data.records||[]).filter(item=>Number.isFinite(item.duration_s)));
      }catch{/* Timing display must not interrupt editing. */}
    }
    void refresh();
    const timer=setInterval(refresh,active?1500:15000);
    window.addEventListener("focus",refresh);
    return ()=>{current=false;clearInterval(timer);window.removeEventListener("focus",refresh);};
  },[shotId,active]);
  return records;
}
export function TimingNote({entry}:{entry?:OperationTiming}){
  if(!entry)return null;
  return <small className="workspace-timing" title={`${timingLabel[entry.stage]||entry.stage}: ${entry.status}`}>
    {timingLabel[entry.stage]||entry.stage} · {entry.duration_s.toFixed(1)}s{entry.status!=="completed"?` · ${entry.status}`:""}
  </small>;
}
