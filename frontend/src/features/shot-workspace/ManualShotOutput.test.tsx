// @vitest-environment jsdom
import {render,waitFor,cleanup,screen,fireEvent} from "@testing-library/react";
import {afterEach,it,expect,vi} from "vitest";
import {createRef} from "react";
import {ManualShotOutput} from "./ManualShotOutput";
import type {Shot} from "../../shared/api/types";
import {getDirectorChatSession} from "./api";
import {getShot,submitShot,getLocalH3Resolutions,getH3ProviderStatus} from "../production/api";
vi.mock("./api",()=>({getDirectorChatSession:vi.fn()}));
vi.mock("../production/api",()=>({getShot:vi.fn(),submitShot:vi.fn(),getLocalH3Resolutions:vi.fn(),getH3ProviderStatus:vi.fn(),getH3Job:vi.fn(),cancelH3Job:vi.fn()}));
afterEach(()=>{cleanup();sessionStorage.clear();vi.resetAllMocks();vi.unstubAllGlobals();});
it.each(["completed","failed"])("restores a pending chain after reload only for completed prompts (%s)",async(status)=>{
 const shot={id:"s1",project_id:"p1",title:"Test",status:"draft",refs:[{}],prompt_sections:{subject_definitions:"Subject",summary:"Action",retention_analysis:"Keep",detailed_description:"Motion",overall_soundscape:"Quiet",non_diegetic_music:"None"}} as Shot;
 vi.stubGlobal("fetch",vi.fn(async()=>({ok:true,json:async()=>({preset:"landscape-720"})})));
 const started=Date.now();
 sessionStorage.setItem("ds.writeGenerate.s1",JSON.stringify({started,provider:"local",resolution:"auto"}));
 vi.mocked(getDirectorChatSession).mockResolvedValue({active:false,error:status==="failed"?"Writer failed":"",progress:{shot_id:"s1",started_at:new Date(started).toISOString(),status} } as never);
 vi.mocked(getH3ProviderStatus).mockResolvedValue({default_provider:"local",minimax_configured:false} as never);
 vi.mocked(getLocalH3Resolutions).mockResolvedValue({presets:[]} as never);
 vi.mocked(getShot).mockResolvedValue(shot);
 vi.mocked(submitShot).mockResolvedValue(shot);
 render(<ManualShotOutput shot={shot} disabled={false} onSaved={vi.fn()}/>);
 if(status==="completed")await waitFor(()=>expect(submitShot).toHaveBeenCalledExactlyOnceWith("s1","local",undefined));
 else {await screen.findByText("Writer failed");expect(submitShot).not.toHaveBeenCalled();}
 expect(sessionStorage.getItem("ds.writeGenerate.s1")).toBeNull();
});

it("loads and saves a project-wide size selection",async()=>{
 const shot={id:"s1",project_id:"p1",title:"Test",status:"draft",refs:[],prompt_sections:{}} as unknown as Shot;
 const fetcher=vi.fn(async(_url:string,init?:RequestInit)=>({ok:true,json:async()=>({preset:init?.method==="PUT"?JSON.parse(init.body as string).preset:"landscape-720"})}));
 vi.stubGlobal("fetch",fetcher);
 vi.mocked(getH3ProviderStatus).mockResolvedValue({default_provider:"local",minimax_configured:false} as never);
 vi.mocked(getLocalH3Resolutions).mockResolvedValue({presets:[{id:"landscape-720",width:1280,height:704,label:"Landscape"},{id:"portrait-720",width:704,height:1280,label:"Portrait"}]} as never);
 render(<ManualShotOutput shot={shot} disabled={false} onSaved={vi.fn()}/>);
 const size=screen.getByLabelText("Video size") as HTMLSelectElement;
 await waitFor(()=>expect(size.value).toBe("landscape-720"));
 fireEvent.change(size,{target:{value:"portrait-720"}});
 await waitFor(()=>expect(size.value).toBe("portrait-720"));
 expect(fetcher).toHaveBeenCalledWith("/api/shot-workspace/projects/p1/resolution",expect.objectContaining({method:"PUT",body:JSON.stringify({preset:"portrait-720"})}));
});

it("shows source matching and omits project dimensions in chained continuation generation",async()=>{
 const shot={id:"s2",project_id:"p1",title:"Continuation",status:"draft",refs:[{}],video_context:{mode:"previous_shot",source_shot_id:"s1"},prompt_sections:{subject_definitions:"A",summary:"B",retention_analysis:"C",detailed_description:"D",overall_soundscape:"E",non_diegetic_music:"F"}} as Shot;
 const fetcher=vi.fn(async()=>({ok:true,json:async()=>({preset:"landscape-1080"})}));
 vi.stubGlobal("fetch",fetcher);
 vi.mocked(getH3ProviderStatus).mockResolvedValue({default_provider:"local",minimax_configured:false} as never);
 vi.mocked(getLocalH3Resolutions).mockResolvedValue({presets:[{id:"landscape-1080",label:"Landscape 1080",width:1920,height:1088}]} as never);
 vi.mocked(submitShot).mockResolvedValue(shot);
 const ref=createRef<{generate:(shot:Shot)=>Promise<void>}>();
 render(<ManualShotOutput shot={shot} disabled={false} onSaved={vi.fn()} generationRef={ref}/>);
 await waitFor(()=>expect(fetcher).toHaveBeenCalledOnce());
 const size=screen.getByLabelText("Video size") as HTMLSelectElement;
 expect(size.value).toBe("auto");
 expect(size.selectedOptions[0].textContent).toBe("Match source video");
 expect(size.disabled).toBe(true);
 fetcher.mockClear();
 await ref.current!.generate(shot);
 expect(fetcher).not.toHaveBeenCalled();
 expect(submitShot).toHaveBeenCalledExactlyOnceWith("s2","local",undefined);
});
