// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen,waitFor} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import {ShotWorkspacePage} from "./ShotWorkspacePage";
import {getProject,suggestShotMaterials,replaceShotMaterials} from "./api";
const context = vi.hoisted(()=>({projectId:"p1",createAndSelect:vi.fn()}));
vi.mock("../../shared/project/ProjectContext",()=>({useProject:()=>context}));
vi.mock("./ManualShotOutput",()=>({ManualShotOutput:()=>null}));
vi.mock("./ManualContinuity",()=>({ManualContinuity:()=>null}));
vi.mock("./api",()=>({getProject:vi.fn(),getDirectorChatSession:vi.fn(async()=>({active:false})),formatDialogue:()=>"",suggestShotMaterials:vi.fn(),replaceShotMaterials:vi.fn()}));
afterEach(()=>{cleanup();localStorage.clear();vi.unstubAllGlobals();vi.clearAllMocks();context.projectId="p1";});
function setup(){
  vi.mocked(getProject).mockImplementation(async(id)=>({shots:[1,2].map(n=>({id:`${id}-${n}`,project_id:id,title:`${id} shot ${n}`,script_beat:"A quiet room.",duration_s:5,refs:[],voice_refs:[],dialogue:[],prompt_sections:{}}))}) as never);
  vi.stubGlobal("fetch",vi.fn(async(url:string)=>({ok:true,json:async()=>url.endsWith("prompt-check") ? {valid:false,stale:false,errors:[],warnings:[]} : {version:"v1"}})));
}
it("restores the selected shot after remount and remembers each project independently",async()=>{
  setup();let view=render(<ShotWorkspacePage/>);
  await screen.findByDisplayValue("p1 shot 1");
  fireEvent.click(screen.getByRole("button",{name:"Shot 2"}));
  await screen.findByDisplayValue("p1 shot 2");
  view.unmount();view=render(<ShotWorkspacePage/>);
  await screen.findByDisplayValue("p1 shot 2");
  context.projectId="p2";view.rerender(<ShotWorkspacePage/>);
  await screen.findByDisplayValue("p2 shot 1");
  fireEvent.click(screen.getByRole("button",{name:"Shot 2"}));
  await screen.findByDisplayValue("p2 shot 2");
  context.projectId="p1";view.rerender(<ShotWorkspacePage/>);
  await screen.findByDisplayValue("p1 shot 2");
  expect(localStorage.getItem("ds.shotWorkspace.selectedShot.p2")).toBe("p2-2");
});
it("falls back to an existing shot when the remembered shot was deleted",async()=>{
  setup();localStorage.setItem("ds.shotWorkspace.selectedShot.p1","deleted-shot");
  render(<ShotWorkspacePage/>);
  await screen.findByDisplayValue("p1 shot 1");
  await waitFor(()=>expect(localStorage.getItem("ds.shotWorkspace.selectedShot.p1")).toBe("p1-1"));
});

it("selects and saves references directly beside Link ref without opening a dialog",async()=>{
  setup();
  const material={role:"scene" as const,asset_id:"scene_a",file_key:"master",notes:""};
  vi.mocked(suggestShotMaterials).mockResolvedValue({materials:[material],reasons:[],warnings:[],candidate_count:2,model_duration_s:2,operation_duration_s:2.4});
  vi.mocked(replaceShotMaterials).mockImplementation(async()=>({id:"p1-1",project_id:"p1",title:"p1 shot 1",script_beat:"A quiet room.",duration_s:5,refs:[{...material,picture_index:1}],voice_refs:[],dialogue:[],prompt_sections:{}}) as never);
  render(<ShotWorkspacePage/>);await screen.findByDisplayValue("p1 shot 1");
  fireEvent.change(screen.getByRole("textbox",{name:"Shot brief"}),{target:{value:"An outdoor street."}});
  fireEvent.click(screen.getByRole("button",{name:"Choose references with AI"}));
  expect(await screen.findByText("Selected 1 references · 2.4s.")).toBeTruthy();
  expect(suggestShotMaterials).toHaveBeenCalledWith("p1-1",[],"An outdoor street.");
  expect(replaceShotMaterials).toHaveBeenCalledWith("p1-1",[material],{expectedRefs:[]});
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole("button",{name:"Undo selection"})).toBeTruthy();
});

it("keeps existing references when automatic selection finds no matches",async()=>{
  setup();
  vi.mocked(suggestShotMaterials).mockResolvedValue({materials:[],reasons:[],warnings:[],candidate_count:1,model_duration_s:1,operation_duration_s:1});
  render(<ShotWorkspacePage/>);await screen.findByDisplayValue("p1 shot 1");
  fireEvent.click(screen.getByRole("button",{name:"Choose references with AI"}));
  expect(await screen.findByText(/No matching references found/)).toBeTruthy();
  expect(replaceShotMaterials).not.toHaveBeenCalled();
});
