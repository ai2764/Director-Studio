// @vitest-environment jsdom
import {cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, expect, it, vi} from "vitest";
import {DescriptionAssistant} from "./DescriptionAssistant";
vi.mock("../director/api", () => ({getDirectorModel:vi.fn().mockResolvedValue({model:"test",available:["test"]}),setDirectorModel:vi.fn()}));
afterEach(() => {cleanup();vi.unstubAllGlobals();});
it("restores per-shot conversations after remount and keeps other shots separate", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url:string) => ({ok:true,json:async () => ({messages:url.includes("shot_one/") ? [
    {role:"user",text:"Keep the object still."}, {role:"assistant",text:"A static close-up will work.",proposal:"Use a close-up."}
  ] : []})})));
  const props = {description:"An object on a table.",disabled:false,ready:true,onRequest:vi.fn(),onUndo:vi.fn(),canUndo:false};
  const first = render(<DescriptionAssistant {...props} shotId="shot_one" />);
  fireEvent.click(screen.getByRole("button",{name:"✦ Description assistant"}));
  await screen.findByText("A static close-up will work.");
  expect(screen.getByRole("button",{name:"Apply suggestion"})).toBeTruthy();
  first.unmount();
  const restored = render(<DescriptionAssistant {...props} shotId="shot_one" />);
  fireEvent.click(screen.getByRole("button",{name:"✦ Description assistant"}));
  await screen.findByText("Keep the object still.");
  restored.rerender(<DescriptionAssistant {...props} shotId="shot_two" />);
  await waitFor(() => expect(screen.queryByText("Keep the object still.")).toBeNull());
  expect(props.onRequest).not.toHaveBeenCalled();
});

it("keeps Cancel actionable while thinking and aborts the request",async()=>{
 vi.stubGlobal("fetch",vi.fn(async()=>({ok:true,json:async()=>({messages:[]})})));
 let received: AbortSignal | undefined;
 const onRequest=vi.fn((_text:string,_turns:unknown,signal?:AbortSignal)=>new Promise<never>((_resolve,reject)=>{
   received=signal;signal?.addEventListener("abort",()=>reject(new DOMException("Aborted","AbortError")));
 }));
 render(<DescriptionAssistant shotId="cancel_test" description="A shot" disabled={false} ready onRequest={onRequest} onUndo={vi.fn()} canUndo={false}/>);
 fireEvent.click(screen.getByRole("button",{name:"✦ Description assistant"}));
 fireEvent.change(screen.getByLabelText("Description request"),{target:{value:"Change the shot"}});
 await waitFor(()=>expect((screen.getByRole("button",{name:"Send"}) as HTMLButtonElement).disabled).toBe(false));
 fireEvent.click(screen.getByRole("button",{name:"Send"}));
 const cancel=await screen.findByRole("button",{name:"Cancel"});
 expect((cancel as HTMLButtonElement).disabled).toBe(false);
 expect(screen.getByText("Thinking · 0s")).toBeTruthy();
 fireEvent.click(cancel);
 await waitFor(()=>expect(received?.aborted).toBe(true));
 await screen.findByText("Request cancelled. Any changes already saved are kept.");
 expect(screen.getByRole("button",{name:"Send"})).toBeTruthy();
});
