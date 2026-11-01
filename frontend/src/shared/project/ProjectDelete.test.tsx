// @vitest-environment jsdom
import {render,screen,fireEvent,waitFor,cleanup} from "@testing-library/react";
import {it,expect,vi,afterEach} from "vitest";
import {ProjectPicker} from "./ProjectPicker";
const refresh = vi.hoisted(()=>vi.fn());
vi.mock("./ProjectContext",()=>({useProject:()=>({projects:[{id:"prj_fixture",name:"Fixture project"}],projectId:"prj_fixture",project:{id:"prj_fixture",name:"Fixture project"},loading:false,error:null,setProjectId:vi.fn(),createAndSelect:vi.fn(),refreshProjects:refresh})}));
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.clearAllMocks();});
it("omits project deletion unless the desktop enables it",()=>{
 render(<ProjectPicker/>);
 expect(screen.queryByRole("button",{name:"Delete project"})).toBeNull();
});
it("requires exact project name and deletes only on explicit confirmation",async()=>{
 const fetcher=vi.fn(async()=>({ok:true,json:async()=>({deleted:"prj_fixture"})}));
 vi.stubGlobal("fetch",fetcher);
 render(<ProjectPicker allowDelete/>);
 fireEvent.click(screen.getByRole("button",{name:"Delete project"}));
 expect(screen.getByRole("alertdialog")).toBeTruthy();
 expect(screen.getByText(/This cannot be undone/)).toBeTruthy();
 const confirm=screen.getByRole("button",{name:"Delete permanently"});
 expect((confirm as HTMLButtonElement).disabled).toBe(true);
 expect(fetcher).not.toHaveBeenCalled();
 fireEvent.change(screen.getByLabelText("Confirm project name"),{target:{value:"Fixture project"}});
 fireEvent.click(confirm);
 await waitFor(()=>expect(refresh).toHaveBeenCalledOnce());
 expect(fetcher).toHaveBeenCalledExactlyOnceWith("/api/projects/prj_fixture",expect.objectContaining({method:"DELETE",body:JSON.stringify({confirmation:"Fixture project"})}));
 expect(screen.queryByRole("alertdialog")).toBeNull();
});
it("cancel keeps project intact",()=>{
 const fetcher=vi.fn();vi.stubGlobal("fetch",fetcher);
 render(<ProjectPicker allowDelete/>);
 fireEvent.click(screen.getByRole("button",{name:"Delete project"}));
 fireEvent.click(screen.getByRole("button",{name:"Cancel"}));
 expect(fetcher).not.toHaveBeenCalled();
 expect(screen.queryByRole("alertdialog")).toBeNull();
});
