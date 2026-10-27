// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ShotEditor } from "./ShotWorkspacePage";
import type { Shot } from "../../shared/api/types";
vi.mock("./ManualContinuity", () => ({ ManualContinuity: () => null }));
vi.mock("./ManualShotOutput", () => ({ ManualShotOutput: () => null }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const shot: Shot = { id:"sht_test", project_id:"prj_test", scene_id:"scene", title:"Object view", script_beat:"The object rotates on the table.", duration_s:5,
  status:"draft", refs:[], voice_refs:[], dialogue:[], layout_refs:[], layout_asset_id:null, layout_review_status:null, ref_frame_job_id:null,
  h3_job_id:null, source_audio_path:null, feedback:"", blocked_reasons:[],
  prompt_sections:{subject_definitions:"",summary:"",retention_analysis:"",detailed_description:"",overall_soundscape:"",non_diegetic_music:""} };
it("applies an amendment to an editable draft and supports undo without writing a prompt", async () => {
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/refine")) return { ok:true, json:async () => ({ description:"The object remains still on the table.",title:shot.title,duration_s:5,
      shot_type:"close-up",camera_angle:"eye level",camera_motion:"static",composition:"centered",dialogue:[],questions:[] }) };
    return { ok:true, json:async () => init?.method === "PUT" ? shot : url.endsWith("prompt-check") ? {valid:false,stale:false,errors:[],warnings:[]} : {version:"v1"} };
  });
  vi.stubGlobal("fetch", fetcher);
  const write = vi.fn(); const save = vi.fn();
  render(<ShotEditor shot={shot} shots={[shot]} index={0} busy={false} onSaved={save} onWrite={write} onDelete={vi.fn()}
    desktopOutput={false} writing={false} writeElapsed={0} onCancel={vi.fn()} />);
  fireEvent.click(screen.getByRole("button",{name:"Amend shot"}));
  fireEvent.change(screen.getByLabelText("Amendment instructions"),{target:{value:"Keep the object stationary."}});
  await waitFor(() => expect((screen.getByRole("button",{name:"Apply amendment"}) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button",{name:"Apply amendment"}));
  await waitFor(() => expect((screen.getByLabelText("Shot brief") as HTMLTextAreaElement).value).toBe("The object remains still on the table."));
  expect(write).not.toHaveBeenCalled();
  const request = fetcher.mock.calls.find(([url])=>url.endsWith("/refine"));
  expect(JSON.parse(request![1]!.body as string).amendment).toBe("Keep the object stationary.");
  fireEvent.click(screen.getByRole("button",{name:"Undo amendment"}));
  expect((screen.getByLabelText("Shot brief") as HTMLTextAreaElement).value).toBe(shot.script_beat);
});
