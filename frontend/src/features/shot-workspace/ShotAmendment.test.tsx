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
it("applies an amendment to an editable draft and supports undo without writing a prompt", async () => {
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/assist")) return { ok:true, json:async () => ({ kind:"draft", reply:"Draft updated.", draft:{ description:"The object remains still on the table.",title:shot.title,duration_s:5,
      shot_type:"close-up",camera_angle:"eye level",camera_motion:"static",composition:"centered",dialogue:[],questions:[] } }) };
    return { ok:true, json:async () => init?.method === "PUT" ? shot : url.endsWith("prompt-check") ? {valid:false,stale:false,errors:[],warnings:[]} : {version:"v1"} };
  });
  vi.stubGlobal("fetch", fetcher);
  const write = vi.fn(); const save = vi.fn();
  render(<ShotEditor shot={shot} shots={[shot]} index={0} busy={false} onSaved={save} onWrite={write} onDelete={vi.fn()}
    desktopOutput={false} writing={false} writeElapsed={0} onCancel={vi.fn()} />);
  fireEvent.click(screen.getByRole("button",{name:"✦ Description assistant"}));
  fireEvent.change(screen.getByLabelText("Description request"),{target:{value:"Keep the object stationary."}});
  await waitFor(() => expect((screen.getByRole("button",{name:"Send"}) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button",{name:"Send"}));
  await waitFor(() => expect((screen.getByLabelText("Shot brief") as HTMLTextAreaElement).value).toBe("The object remains still on the table."));
  expect(write).not.toHaveBeenCalled();
  expect(save).not.toHaveBeenCalled();
  const request = fetcher.mock.calls.find(([url])=>url.endsWith("/assist"));
  expect(JSON.parse(request![1]!.body as string).amendment).toBe("Keep the object stationary.");
  fireEvent.click(screen.getByRole("button",{name:"Undo description change"}));
  expect((screen.getByLabelText("Shot brief") as HTMLTextAreaElement).value).toBe(shot.script_beat);
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
