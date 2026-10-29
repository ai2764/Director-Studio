// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Shot } from "../../shared/api/types";
import { ShotMaterialEditor } from "./WorkspaceReferences";
import { listLibraryAssets } from "../library/api";
import { replaceShotMaterials } from "./api";
import { LIBRARY_CHANGED } from "../library/libraryChanges";

vi.mock("../library/api", () => ({ listLibraryAssets: vi.fn() }));
vi.mock("./api", () => ({ replaceShotMaterials: vi.fn() }));
const shot = { id: "shot_test", project_id: "project_test", refs: [] } as unknown as Shot;
const image = { id: "asset_test", kind: "props", name: "Blue mug", notes: "A square handle", files: { master: "mug.png" }, urls: { master: "/mug.png" } };
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listLibraryAssets).mockImplementation(async (kind) => kind === "props" ? [image] as never : []);
  vi.mocked(replaceShotMaterials).mockResolvedValue(shot);
});
afterEach(cleanup);

it("refreshes an open picker after a library change without dropping reference notes", async () => {
  render(<ShotMaterialEditor shot={shot} onClose={vi.fn()} onOpenImage={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button",{name:"Add Blue mug master"}));
  fireEvent.change(screen.getByRole("textbox",{name:"Reference 1 note"}),{target:{value:"Keep the handle"}});
  const added={...image,id:"asset_new",name:"New cup"};
  vi.mocked(listLibraryAssets).mockImplementation(async(kind)=>kind === "props" ? [image,added] as never : []);
  fireEvent(window,new Event(LIBRARY_CHANGED));
  await screen.findByRole("button",{name:"Add New cup master"});
  expect((screen.getByRole("textbox",{name:"Reference 1 note"}) as HTMLTextAreaElement).value).toBe("Keep the handle");
  expect(screen.getByText("P1 · Blue mug")).toBeTruthy();
});

it("keeps selected references and notes when the library search changes", async () => {
  const close = vi.fn(); const saved = vi.fn();
  render(<ShotMaterialEditor shot={shot} onClose={close} onOpenImage={vi.fn()} onSaved={saved} />);
  fireEvent.click(await screen.findByRole("button", { name: "Add Blue mug master" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Reference 1 note" }), { target: { value: "Use the handle shape" } });
  fireEvent.change(screen.getByRole("searchbox", { name: "Search references" }), { target: { value: "nothing matches" } });
  expect(screen.getByText("P1 · Blue mug")).toBeTruthy();
  expect(screen.getByText(/No matching images/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Save references" }));
  await waitFor(() => expect(replaceShotMaterials).toHaveBeenCalledWith("shot_test", [{ role: "prop", asset_id: "asset_test", file_key: "master", notes: "Use the handle shape" }], { expectedRefs: [], revisionRequest: "" }));
  expect(saved).toHaveBeenCalledWith(shot, "", false);
  expect(close).toHaveBeenCalled();
});

it("allows removing a selected image then adding it again, without duplicates", async () => {
  render(<ShotMaterialEditor shot={shot} onClose={vi.fn()} onOpenImage={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Add Blue mug master" }));
  expect((screen.getByRole("button", { name: "Blue mug master selected" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Remove reference 1" }));
  expect(screen.queryByText("P1 · Blue mug")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Add Blue mug master" }));
  expect(screen.getAllByText("P1 · Blue mug")).toHaveLength(1);
});
