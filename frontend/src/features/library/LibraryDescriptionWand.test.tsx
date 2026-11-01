// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LibraryDescriptionWand } from "./LibraryDescriptionWand";
import { describeUploadImage, type LibraryAsset } from "./api";
vi.mock("./api", () => ({ describeUploadImage: vi.fn() }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const asset = { id: "prop_test", kind: "props", files: { master: "object.png" }, urls: { master: "/test/object.png" } , name: "Object", notes: "", pipeline_id: "external", job_id: "", seed: 0, created_at: "2026-01-01T00:00:00Z", meta: {}, project_id: "prj_test" } as LibraryAsset;
it("passes the current name and description as guidance without saving the asset", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(["fixture"]) }));
  vi.mocked(describeUploadImage).mockResolvedValue({ description: "A blue object with a round handle.", cached: false, language: "en" });
  const apply = vi.fn();
  render(<LibraryDescriptionWand asset={asset} name="Blue object" description="Keep handle detail" projectId="prj_test" disabled={false} onDescription={apply} />);
  fireEvent.click(screen.getByRole("button", { name: /Refine description/ }));
  await waitFor(() => expect(apply).toHaveBeenCalledWith("A blue object with a round handle."));
  expect(describeUploadImage).toHaveBeenCalledWith(expect.objectContaining({ name: "Blue object", existingDescription: "Keep handle detail", projectId: "prj_test" }));
});
it("keeps edits made while a description request is running", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(["fixture"]) }));
  let finish!: (value: { description: string; cached: boolean; language: "en" | "zh" }) => void;
  vi.mocked(describeUploadImage).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const apply = vi.fn();
  const view = render(<LibraryDescriptionWand asset={asset} name="Object" description="Original" projectId="prj_test" disabled={false} onDescription={apply} />);
  fireEvent.click(screen.getByRole("button", { name: /Refine description/ }));
  await waitFor(() => expect(finish).toBeTypeOf("function"));
  view.rerender(<LibraryDescriptionWand asset={asset} name="Object" description="New user edit" projectId="prj_test" disabled={false} onDescription={apply} />);
  finish({ description: "Older suggestion", cached: false, language: "en" });
  await screen.findByText("Your edits were kept. Retry to refine the current text.");
  expect(apply).not.toHaveBeenCalled();
});
