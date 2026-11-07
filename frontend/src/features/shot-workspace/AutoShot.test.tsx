// @vitest-environment jsdom
import {useState} from "react";
import {cleanup,fireEvent,render,screen,waitFor} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import type {Shot} from "../../shared/api/types";
import {ShotEditor} from "./ShotWorkspacePage";
import {suggestShotMaterials,replaceShotMaterials} from "./api";
vi.mock("./ManualContinuity",()=>({ManualContinuity:()=>null}));
const generation=vi.hoisted(()=>({generate:vi.fn(),prepare:vi.fn(),clear:vi.fn()}));
vi.mock("./ManualShotOutput",()=>({ManualShotOutput:({generationRef}:{generationRef?:{current:unknown}})=>{
  if(generationRef)generationRef.current=generation;
  return <aside aria-label="Video output"/>;
}}));
vi.mock("./api",()=>({suggestShotMaterials:vi.fn(),replaceShotMaterials:vi.fn(),formatDialogue:()=>""}));
afterEach(()=>{cleanup();vi.clearAllMocks();vi.unstubAllGlobals();});
function deferred<T>(){let resolve!:(v:T)=>void;const promise=new Promise<T>(r=>{resolve=r;});return {promise,resolve};}
const initial={id:"s1",project_id:"p1",title:"A shot",scene_id:"scene",script_beat:"A quiet room.",duration_s:5,status:"draft",refs:[],voice_refs:[],dialogue:[],prompt_sections:{},meta:{}} as unknown as Shot;
const draft={description:"A slow approach into the quiet room.",title:"A shot",duration_s:6,shot_type:"wide",camera_angle:"eye level",camera_motion:"dolly",composition:"centered",dialogue:[],questions:[]};
const material={role:"scene" as const,asset_id:"scene1",file_key:"master",notes:""};
function setup(refine:()=>Promise<unknown>,write:(s:Shot)=>Promise<Shot|void>){
 let server=structuredClone(initial);const order:string[]=[];
 vi.stubGlobal("fetch",vi.fn(async(url:string,init?:RequestInit)=>({ok:true,json:async()=>{
  if(url.endsWith("/refine")){order.push("refine");expect(JSON.parse(init!.body as string).description_only).toBe(true);return refine();}
  if(init?.method==="PUT"){
   order.push("save");const b=JSON.parse(init.body as string);server={...server,script_beat:b.description,title:b.title,duration_s:b.duration_s};return server;
  }
  if(url.endsWith("prompt-check"))return {valid:false,stale:false,errors:[],warnings:[]};
  if(url.endsWith("/timings"))return {records:[]};
  if(url.endsWith("/conversation"))return {messages:[]};
  return {version:"current"};
 }})));
 vi.mocked(replaceShotMaterials).mockImplementation(async(_id,materials)=>{order.push("references");server={...server,refs:materials.map((r,i)=>({...r,picture_index:i+1}))};return server;});
 function Harness(){const [shot,setShot]=useState(server);return <ShotEditor shot={shot} shots={[shot]} index={0} busy={false} onSaved={setShot} onWrite={write} onDelete={vi.fn()} desktopOutput={false} writing={false} writeElapsed={0} onCancel={vi.fn()}/>;}
 render(<Harness/>);return order;
}
it("runs the stages in order, highlights each region and generates only the completed prompt",async()=>{
 const refine=deferred<typeof draft>();const select=deferred<Awaited<ReturnType<typeof suggestShotMaterials>>>();const writing=deferred<Shot>();
 vi.mocked(suggestShotMaterials).mockImplementation(()=>select.promise);generation.generate.mockResolvedValue(undefined);
 const writer=vi.fn(()=>writing.promise);const order=setup(()=>refine.promise,writer);
 const button=screen.getByRole("button",{name:"Run full shot"});await waitFor(()=>expect((button as HTMLButtonElement).disabled).toBe(false));
 fireEvent.click(button);fireEvent.click(button);
 await waitFor(()=>expect(document.querySelector('[data-workflow-stage="brief"]')?.getAttribute("data-active")).toBe("true"));
 refine.resolve(draft);await waitFor(()=>expect(suggestShotMaterials).toHaveBeenCalledOnce());
 expect(document.querySelector('[data-workflow-stage="references"]')?.getAttribute("data-active")).toBe("true");
 select.resolve({materials:[material],reasons:[],warnings:[],candidate_count:1,model_duration_s:1,operation_duration_s:1});
 await waitFor(()=>expect(writer).toHaveBeenCalledOnce());
 expect(document.querySelector('[data-workflow-stage="prompt"]')?.getAttribute("data-active")).toBe("true");
 expect(generation.generate).not.toHaveBeenCalled();
 const complete={...initial,script_beat:draft.description,refs:[{...material,picture_index:1}],prompt_sections:{summary:"Finished prompt"}} as Shot;
 writing.resolve(complete);await screen.findByText("Video queued. Progress is shown in Video output.");
 expect(generation.generate).toHaveBeenCalledExactlyOnceWith(complete);
 expect(order).toEqual(["save","refine","save","references"]);
 expect(suggestShotMaterials).toHaveBeenCalledWith("s1",[],draft.description);
});
it("stops at a failed writer without submitting video",async()=>{
 vi.mocked(suggestShotMaterials).mockResolvedValue({materials:[material],reasons:[],warnings:[],candidate_count:1,model_duration_s:1,operation_duration_s:1});
 setup(async()=>draft,vi.fn().mockRejectedValue(new Error("Writer failed")));
 const button=screen.getByRole("button",{name:"Run full shot"});await waitFor(()=>expect((button as HTMLButtonElement).disabled).toBe(false));fireEvent.click(button);
 await screen.findByText("Auto shot stopped at prompt: Writer failed");
 expect(generation.generate).not.toHaveBeenCalled();expect(generation.clear).toHaveBeenCalledOnce();
});
it("does not write or generate when selection finds no usable references",async()=>{
 vi.mocked(suggestShotMaterials).mockResolvedValue({materials:[],reasons:[],warnings:[],candidate_count:1,model_duration_s:1,operation_duration_s:1});
 const writer=vi.fn();setup(async()=>draft,writer);
 const button=screen.getByRole("button",{name:"Run full shot"});await waitFor(()=>expect((button as HTMLButtonElement).disabled).toBe(false));fireEvent.click(button);
 await screen.findByText("Auto shot stopped at references: No matching references found.");
 expect(replaceShotMaterials).not.toHaveBeenCalled();expect(writer).not.toHaveBeenCalled();expect(generation.generate).not.toHaveBeenCalled();
});
