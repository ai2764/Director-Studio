// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen,waitFor} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import {WorkspaceModelPicker} from "./WorkspaceModelPicker";
import {getDirectorModel,setDirectorModel} from "../director/api";
vi.mock("../director/api",()=>({getDirectorModel:vi.fn(),setDirectorModel:vi.fn()}));
afterEach(()=>{cleanup();vi.clearAllMocks();});
it("loads only on expansion and switches the shared model with persistence",async()=>{
  vi.mocked(getDirectorModel).mockResolvedValue({model:"one",available:["one","two"]});
  vi.mocked(setDirectorModel).mockResolvedValue({model:"two",available:["one","two"]});
  const activity=vi.fn();render(<WorkspaceModelPicker disabled={false} onSwitching={activity}/>);
  expect(getDirectorModel).not.toHaveBeenCalled();
  const details=screen.getByText("AI model").closest("details")!;
  details.open=true;fireEvent(details,new Event("toggle"));
  await screen.findByRole("option",{name:"two"});
  fireEvent.change(screen.getByLabelText("Workspace AI model"),{target:{value:"two"}});
  await waitFor(()=>expect(setDirectorModel).toHaveBeenCalledWith("two",true));
  await waitFor(()=>expect(activity).toHaveBeenLastCalledWith(false));
  expect(activity).toHaveBeenCalledWith(true);
  expect((screen.getByLabelText("Workspace AI model") as HTMLSelectElement).value).toBe("two");
});
