// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen,waitFor} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import {VoiceReferences} from "./VoiceReferences";
import type {Shot} from "../../shared/api/types";
import {listLibraryAssets} from "../library/api";
vi.mock("../library/api",()=>({listLibraryAssets:vi.fn()}));
vi.mock("../library/AssetImportDialog",()=>({AssetImportDialog:()=>null}));
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.clearAllMocks();});
it("previews a library voice, requires a speaker, and saves the explicit binding",async()=>{
  const shot = {id:"shot_test",project_id:"project_test",voice_refs:[],source_audio_path:null} as unknown as Shot;
  vi.mocked(listLibraryAssets).mockResolvedValue([{id:"voice_one",name:"Guide",meta:{h3_ready:true},urls:{reference:"/test-reference.wav"}} as never]);
  const fetcher=vi.fn(async()=>({ok:true,json:async()=>({...shot,voice_refs:[{asset_id:"voice_one",audio_index:1,file_key:"reference",speaker:"Guide",notes:""}]})}));
  vi.stubGlobal("fetch",fetcher);const onSaved=vi.fn();
  render(<VoiceReferences shot={shot} disabled={false} onSaved={onSaved}/>);
  const details=screen.getByText("Voice references · 0 / 3").closest("details")!;
  details.open=true;fireEvent(details,new Event("toggle"));
  await screen.findByRole("option",{name:"Guide"});
  expect(listLibraryAssets).toHaveBeenCalledWith("voices","project_test");
  fireEvent.change(screen.getByLabelText("Add voice reference"),{target:{value:"voice_one"}});
  expect(screen.getByLabelText("Preview voice 1").getAttribute("src")).toBe("/test-reference.wav");
  expect((screen.getByRole("button",{name:"Save voices"}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Voice 1 speaker"),{target:{value:"Guide"}});
  fireEvent.click(screen.getByRole("button",{name:"Save voices"}));
  await waitFor(()=>expect(onSaved).toHaveBeenCalled());
  const options=fetcher.mock.calls[0] as unknown as [string,RequestInit];
  expect(options[0]).toBe("/api/shot-workspace/shots/shot_test/voice-references");
  expect(JSON.parse(options[1].body as string)).toEqual({voice_refs:[{asset_id:"voice_one",audio_index:1,file_key:"reference",speaker:"Guide",notes:""}],expected_refs:[]});
});
