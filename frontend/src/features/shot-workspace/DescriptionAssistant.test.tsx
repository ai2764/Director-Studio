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
