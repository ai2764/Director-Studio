// @vitest-environment jsdom

import { afterEach, expect, it, vi } from "vitest";
import { submitJsonShot } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

it("sends the selected H3 provider with the multipart shot submission", async () => {
  let submitted: FormData | null = null;
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
    submitted = init?.body as FormData;
    return new Response(JSON.stringify({ id: "job_1" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }));

  await submitJsonShot(
    "prj_1",
    "shot_1",
    3,
    { pictures: new Map(), audio: new Map() },
    "minimax",
  );

  expect(submitted).not.toBeNull();
  expect(submitted!.get("revision")).toBe("3");
  expect(submitted!.get("h3_provider")).toBe("minimax");
});
