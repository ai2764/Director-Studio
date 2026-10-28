// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ShotEditor } from "./ShotWorkspacePage";
import type { Shot } from "../../shared/api/types";
vi.mock("./ManualContinuity", () => ({ ManualContinuity: () => null }));
vi.mock("./ManualShotOutput", () => ({ ManualShotOutput: () => <aside aria-label="Video output" /> }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const shot: Shot = { id:"sht_test", project_id:"prj_test", scene_id:"scene", title:"Object view", script_beat:"The object rotates on the table.", duration_s:5,
  status:"draft", refs:[], voice_refs:[], dialogue:[], layout_refs:[], layout_asset_id:null, layout_review_status:null, ref_frame_job_id:null,
  h3_job_id:null, source_audio_path:null, feedback:"", blocked_reasons:[],
  prompt_sections:{subject_definitions:"",summary:"",retention_analysis:"",detailed_description:"",overall_soundscape:"",non_diegetic_music:""} };

it("saves an assistant prompt amendment and its matching brief before invoking the writer", async () => {
  const draft = {description:"A static close-up shows the object.",title:shot.title,duration_s:9,
    shot_type:"close-up",camera_angle:"eye level",camera_motion:"static",composition:"centered",dialogue:[],questions:[]};
  const saved = {...shot,script_beat:draft.description,shot_type:draft.shot_type,duration_s:9};
  const order:string[] = [];
  const fetcher = vi.fn(async (url:string, init?:RequestInit) => {
    if (url.endsWith("/assist")) return {ok:true,json:async()=>({kind:"prompt",reply:"Prompt request prepared.",update_brief:true,prompt_request:"Rewrite as a close-up."})};
    if (url.endsWith("/refine")) {order.push("refine");return {ok:true,json:async()=>draft};}
    if (init?.method === "PUT") {order.push("save");return {ok:true,json:async()=>saved};}
    return {ok:true,json:async()=>url.endsWith("prompt-check") ? {valid:false,stale:false,errors:[],warnings:[]} : url.endsWith("/conversation") ? {messages:[]} : {version:"v1"}};
  });
  vi.stubGlobal("fetch",fetcher);
  const write = vi.fn(async () => {order.push("write");});
  render(<ShotEditor shot={shot} shots={[shot]} index={0} busy={false} onSaved={vi.fn()} onWrite={write} onDelete={vi.fn()} desktopOutput={false} writing={false} writeElapsed={0} onCancel={vi.fn()} />);
  fireEvent.click(screen.getByRole("button",{name:"✦ Description assistant"}));
  fireEvent.change(screen.getByLabelText("Description request"),{target:{value:"Rewrite as a close-up."}});
  await waitFor(()=>expect((screen.getByRole("button",{name:"Send"}) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button",{name:"Send"}));
  await screen.findByText("Prompt saved. Review the six sections before generating.");
  expect(order).toEqual(["save","refine","save","write"]);
  expect(write).toHaveBeenCalledWith(saved,"Rewrite as a close-up.");
  const saveCall = fetcher.mock.calls.filter(([,init])=>init?.method === "PUT").at(-1);
  expect(JSON.parse(saveCall![1]!.body as string).description).toBe(draft.description);
  expect(JSON.parse(saveCall![1]!.body as string).duration_s).toBe(9);
});

it("does not report prompt success when the writer fails", async () => {
  vi.stubGlobal("fetch",vi.fn(async (url:string,init?:RequestInit)=>({ok:true,json:async()=>url.endsWith("/assist") ? {kind:"prompt",reply:"Prepared.",prompt_request:"Write prompt."} : init?.method === "PUT" ? shot : url.endsWith("prompt-check") ? {valid:false,stale:false,errors:[],warnings:[]} : url.endsWith("/conversation") ? {messages:[]} : {version:"v1"}})));
  render(<ShotEditor shot={shot} shots={[shot]} index={0} busy={false} onSaved={vi.fn()} onWrite={vi.fn().mockRejectedValue(new Error("Prompt validation failed."))} onDelete={vi.fn()} desktopOutput={false} writing={false} writeElapsed={0} onCancel={vi.fn()} />);
  fireEvent.click(screen.getByRole("button",{name:"✦ Description assistant"}));
  fireEvent.change(screen.getByLabelText("Description request"),{target:{value:"Write prompt."}});
  await waitFor(()=>expect((screen.getByRole("button",{name:"Send"}) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button",{name:"Send"}));
  await screen.findByText("Prompt validation failed.");
  expect(screen.queryByText("Prompt saved. Review the six sections before generating.")).toBeNull();
});
it("applies an amendment to an editable draft and supports undo without writing a prompt", async () => {
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/assist")) return { ok:true, json:async () => ({ kind:"draft", reply:"Draft updated.", draft:{ description:"The object remains still on the table.",title:shot.title,duration_s:8,
      shot_type:"close-up",camera_angle:"eye level",camera_motion:"static",composition:"centered",dialogue:[],questions:[] } }) };
    return { ok:true, json:async () => init?.method === "PUT" ? shot : url.endsWith("prompt-check") ? {valid:false,stale:false,errors:[],warnings:[]} : {version:"v1"} };
  });
  vi.stubGlobal("fetch", fetcher);
  const write = vi.fn(); const save = vi.fn();
  render(<ShotEditor shot={shot} shots={[shot]} index={0} busy={false} onSaved={save} onWrite={write} onDelete={vi.fn()}
    desktopOutput={false} writing={false} writeElapsed={0} onCancel={vi.fn()} />);
  fireEvent.click(screen.getByRole("button",{name:"✦ Description assistant"}));
  fireEvent.change(screen.getByLabelText("Duration (seconds)"),{target:{value:"7"}});
  fireEvent.change(screen.getByLabelText("Description request"),{target:{value:"Keep the object stationary."}});
  await waitFor(() => expect((screen.getByRole("button",{name:"Send"}) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button",{name:"Send"}));
  await waitFor(() => expect((screen.getByLabelText("Shot brief") as HTMLTextAreaElement).value).toBe("The object remains still on the table."));
  expect((screen.getByLabelText("Duration (seconds)") as HTMLInputElement).value).toBe("8");
  expect(write).not.toHaveBeenCalled();
  expect(save).not.toHaveBeenCalled();
  const request = fetcher.mock.calls.find(([url])=>url.endsWith("/assist"));
  expect(JSON.parse(request![1]!.body as string).amendment).toBe("Keep the object stationary.");
  expect(JSON.parse(request![1]!.body as string).duration_s).toBe(7);
  fireEvent.change(screen.getByLabelText("Duration (seconds)"),{target:{value:"10"}});
  expect((screen.getByLabelText("Duration (seconds)") as HTMLInputElement).value).toBe("10");
  fireEvent.click(screen.getByRole("button",{name:"Undo description change"}));
  expect((screen.getByLabelText("Shot brief") as HTMLTextAreaElement).value).toBe(shot.script_beat);
  expect((screen.getByLabelText("Duration (seconds)") as HTMLInputElement).value).toBe("7");
});
it("creates a description from an empty draft without saving an empty brief", async () => {
  const fetcher = vi.fn(async (url: string, _init?: RequestInit) => ({ ok:true, json:async () => url.endsWith("/assist") ? { kind:"draft", reply:"Draft updated.", draft:{
    description:"A blue box sits on a table.", title:"Box view", duration_s:5, shot_type:"medium", camera_angle:"eye level", camera_motion:"static", composition:"centered", dialogue:[], questions:[] }
  } : url.endsWith("prompt-check") ? {valid:false,stale:false,errors:[],warnings:[]} : {version:"v1"} }));
  vi.stubGlobal("fetch", fetcher);
  const save=vi.fn(); const write=vi.fn();
  render(<ShotEditor shot={{...shot,script_beat:""}} shots={[shot]} index={0} busy={false} onSaved={save} onWrite={write} onDelete={vi.fn()} desktopOutput={false} writing={false} writeElapsed={0} onCancel={vi.fn()} />);
  fireEvent.click(screen.getByRole("button",{name:"✦ Description assistant"}));
  fireEvent.change(screen.getByLabelText("Description request"),{target:{value:"A blue box sits on a table."}});
  await waitFor(() => expect((screen.getByRole("button",{name:"Send"}) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button",{name:"Send"}));
  await waitFor(() => expect((screen.getByLabelText("Shot brief") as HTMLTextAreaElement).value).toBe("A blue box sits on a table."));
  const request=fetcher.mock.calls.find(([url])=>url.endsWith("/assist"));
  expect(JSON.parse(request![1]!.body as string).description).toBe("");
  expect(save).not.toHaveBeenCalled(); expect(write).not.toHaveBeenCalled();
});
it("discusses without editing and applies a suggestion only when selected", async () => {
  let turns=0;
  const fetcher=vi.fn(async (url:string, _init?:RequestInit) => {
    if (url.endsWith("/assist")) {
      turns++;
      return {ok:true,json:async()=>turns===1 ? {kind:"reply",reply:"A close-up emphasizes the shape.",proposed_change:"Use a static close-up.",context_scope:"Current description"} : {
        kind:"draft",reply:"Draft updated.",draft:{description:"A static close-up shows the object.",title:shot.title,duration_s:5,shot_type:"close-up",camera_angle:"eye level",camera_motion:"static",composition:"centered",dialogue:[],questions:[]}}};
    }
    return {ok:true,json:async()=>url.endsWith("prompt-check") ? {valid:false,stale:false,errors:[],warnings:[]} : {version:"v1"}};
  });
  vi.stubGlobal("fetch",fetcher); const save=vi.fn(); const write=vi.fn();
  render(<ShotEditor shot={shot} shots={[shot]} index={0} busy={false} onSaved={save} onWrite={write} onDelete={vi.fn()} desktopOutput={false} writing={false} writeElapsed={0} onCancel={vi.fn()} />);
  fireEvent.click(screen.getByRole("button",{name:"✦ Description assistant"}));
  fireEvent.change(screen.getByLabelText("Description request"),{target:{value:"Would a close-up work better?"}});
  await waitFor(()=>expect((screen.getByRole("button",{name:"Send"}) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button",{name:"Send"}));
  await screen.findByText("A close-up emphasizes the shape.");
  expect((screen.getByLabelText("Shot brief") as HTMLTextAreaElement).value).toBe(shot.script_beat);
  fireEvent.click(screen.getByRole("button",{name:"Apply suggestion"}));
  await waitFor(()=>expect((screen.getByLabelText("Shot brief") as HTMLTextAreaElement).value).toBe("A static close-up shows the object."));
  const requests=fetcher.mock.calls.filter(([url])=>url.endsWith("/assist"));
  const second=JSON.parse(requests[1][1]!.body as string);
  expect(second.recent_turns).toHaveLength(2);
  expect(second.amendment).toBe("Update this shot as follows: Use a static close-up.");
  expect(save).not.toHaveBeenCalled(); expect(write).not.toHaveBeenCalled();
});

it("marks a prompt stale immediately for draft edits and rechecks reference changes without writing", async () => {
  let stale = false;
  let resolveCheck: ((value: unknown) => void) | undefined;
  const fetcher = vi.fn(async (url: string) => ({ok:true, json:async () => {
    if (!url.endsWith("prompt-check")) return {version:"v1"};
    if (stale) return new Promise((resolve) => { resolveCheck = resolve; });
    return {valid:true,stale:false,errors:[],warnings:[]};
  }}));
  vi.stubGlobal("fetch", fetcher);
  const saved = {...shot, prompt_sections:{...shot.prompt_sections,summary:"A saved prompt."}};
  const props = {shot:saved,shots:[saved],index:0,busy:false,onSaved:vi.fn(),onWrite:vi.fn(),onDelete:vi.fn(),
    desktopOutput:false,writing:false,writeElapsed:0,onCancel:vi.fn()};
  const view = render(<ShotEditor {...props} />);
  await waitFor(() => expect(screen.getByLabelText("Prompt status").textContent).toBe("Prompt up to date"));
  fireEvent.change(screen.getByLabelText("Shot brief"), {target:{value:"A changed action."}});
  expect(screen.getByLabelText("Prompt status").textContent).toBe("Prompt out of date");
  fireEvent.change(screen.getByLabelText("Shot brief"), {target:{value:shot.script_beat}});
  expect(screen.getByLabelText("Prompt status").textContent).toBe("Prompt up to date");
  stale = true;
  const updated = {...saved,refs:[{role:"prop",asset_id:"asset_test",picture_index:1,file_key:"master"}] as Shot["refs"]};
  view.rerender(<ShotEditor {...props} shot={updated} />);
  expect(screen.getByLabelText("Prompt status").textContent).toBe("Checking prompt…");
  await waitFor(() => expect(resolveCheck).toBeDefined());
  resolveCheck!({valid:false,stale:true,errors:[],warnings:[]});
  await waitFor(() => expect(screen.getByLabelText("Prompt status").textContent).toBe("Prompt out of date"));
  expect(props.onWrite).not.toHaveBeenCalled();
});

it("allows saving a cleared brief without writing a prompt", async () => {
  const saved = {...shot,dialogue:["Welcome."]};
  const onSaved = vi.fn(); const onWrite = vi.fn();
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => ({ok:true,json:async () => {
    if (init?.method === "PUT") return {...saved,script_beat:"",dialogue:[]};
    return url.endsWith("prompt-check") ? {valid:false,stale:false,errors:[],warnings:[]} : {version:"v1"};
  }}));
  vi.stubGlobal("fetch", fetcher);
  render(<ShotEditor shot={saved} shots={[saved]} index={0} busy={false} onSaved={onSaved} onWrite={onWrite}
    onDelete={vi.fn()} desktopOutput={false} writing={false} writeElapsed={0} onCancel={vi.fn()} />);
  await waitFor(() => expect((screen.getByRole("button",{name:"Save"}) as HTMLButtonElement).disabled).toBe(false));
  expect(screen.getByRole("complementary",{name:"Video output"})).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"Clear shot brief"}));
  expect((screen.getByLabelText("Shot brief") as HTMLTextAreaElement).value).toBe("");
  expect((screen.getByLabelText("Dialogue (optional, one cue per line)") as HTMLTextAreaElement).value).toBe("");
  expect((screen.getByRole("button",{name:"Write prompt"}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button",{name:"Save"}));
  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  const request = fetcher.mock.calls.find(([,init]) => init?.method === "PUT");
  const body = JSON.parse(request![1]!.body as string);
  expect(body.description).toBe(""); expect(body.dialogue).toEqual([]);
  expect(body.camera_motion).toBe(""); expect(onWrite).not.toHaveBeenCalled();
});

it("replaces the stale notice while writing instead of stacking stale banners", async () => {
  let valid = false;
  const warning = "Prompt out of date: the brief, dialogue, references or continuity changed. Write the prompt again.";
  vi.stubGlobal("fetch",vi.fn(async (url:string) => ({ok:true,json:async () => url.endsWith("prompt-check")
    ? {valid,stale:!valid,errors:valid ? [] : [{code:"prompt_stale",message:warning},{code:"missing_sections",message:"The saved prompt is incomplete."}],warnings:[]}
    : {version:"v1"}})));
  const saved = {...shot,prompt_sections:{...shot.prompt_sections,summary:"An old prompt."}};
  const props = {shot:saved,shots:[saved],index:0,busy:false,onSaved:vi.fn(),onWrite:vi.fn(),onDelete:vi.fn(),
    desktopOutput:false,writing:false,writeElapsed:0,onCancel:vi.fn()};
  const view=render(<ShotEditor {...props} />);
  await waitFor(() => expect(screen.getByLabelText("Prompt status").textContent).toBe("Prompt out of date"));
  expect(screen.queryByText(warning)).toBeNull();
  expect(screen.getByText("The saved prompt is incomplete.")).toBeTruthy();
  view.rerender(<ShotEditor {...props} writing busy writeElapsed={7} />);
  expect(screen.getByLabelText("Prompt status").textContent).toBe("Writing prompt…");
  expect(screen.queryByText("Prompt out of date")).toBeNull();
  expect(screen.queryByText("The saved prompt is incomplete.")).toBeNull();
  expect(screen.queryByText(warning)).toBeNull();
  expect(screen.getByRole("button",{name:"Cancel prompt writing"})).toBeTruthy();
  view.rerender(<ShotEditor {...props} />);
  expect(screen.getByLabelText("Prompt status").textContent).toBe("Prompt out of date");
  expect(screen.getByText("The saved prompt is incomplete.")).toBeTruthy();
  valid = true;
  const updated = {...saved,prompt_sections:{...saved.prompt_sections,summary:"A newly written prompt."}};
  view.rerender(<ShotEditor {...props} shot={updated} />);
  await waitFor(() => expect(screen.getByLabelText("Prompt status").textContent).toBe("Prompt up to date"));
  expect(screen.queryByText("The saved prompt is incomplete.")).toBeNull();
});
