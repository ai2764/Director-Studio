// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ContextCompaction } from "./ContextCompaction";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("runs only manual compaction and shows measured reduction without sending chat", async () => {
  const urls: string[] = [];
  const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
    urls.push(url);
    if (url === "/api/director/runtime") return new Response(JSON.stringify({ runtime: "harness" }));
    expect(url).toBe("/api/projects/prj_test/chat/compact");
    expect(options?.method).toBe("POST");
    return new Response(JSON.stringify({ compacted: true, before_tokens: 12000, after_tokens: 3000, session_id: "native" }));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<ContextCompaction projectId="prj_test" disabled={false} onBusyChange={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "Compact context" }));
  await screen.findByText(/12,000.*3,000/);
  expect(screen.getByText(/Send your next message/)).toBeTruthy();
  expect(urls).toEqual(["/api/director/runtime", "/api/projects/prj_test/chat/compact"]);
});

it("shows compaction errors and allows another explicit attempt", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url.endsWith("runtime")
    ? new Response(JSON.stringify({ runtime: "harness" }))
    : new Response(JSON.stringify({ detail: "Summary truncated; history unchanged" }), { status: 503 })));
  render(<ContextCompaction projectId="p" disabled={false} onBusyChange={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "Compact context" }));
  await screen.findByRole("alert");
  expect(screen.getByText(/Summary truncated/)).toBeTruthy();
  expect((screen.getByRole("button", { name: "Compact context" }) as HTMLButtonElement).disabled).toBe(false);
});

it("hides the control in legacy and respects active chat admission", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ runtime: "legacy" }))));
  const view = render(<ContextCompaction projectId="p" disabled={false} onBusyChange={() => {}} />);
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  expect(screen.queryByRole("button", { name: "Compact context" })).toBeNull();
  view.unmount();
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ runtime: "harness" }))));
  render(<ContextCompaction projectId="p" disabled onBusyChange={() => {}} />);
  expect((await screen.findByRole("button", { name: "Compact context" }) as HTMLButtonElement).disabled).toBe(true);
});
