// @vitest-environment jsdom

import { afterEach, expect, it, vi } from "vitest";
import {
  clearJsonShotAsset,
  listJsonShotAssets,
  putJsonShotAsset,
  submitJsonShot,
} from "./api";

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
    "minimax",
  );

  expect(submitted).not.toBeNull();
  expect(submitted!.get("revision")).toBe("3");
  expect(submitted!.get("h3_provider")).toBe("minimax");
});

it("uploads one JSON Production slot as multipart data", async () => {
  let requestUrl = "";
  let requestInit: RequestInit | undefined;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    requestUrl = url;
    requestInit = init;
    return new Response(JSON.stringify({ filename: "mia.png" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }));
  const file = new File([new Uint8Array([1, 2, 3])], "mia.png", { type: "image/png" });

  await putJsonShotAsset("prj_1", "shot_1", "picture", 2, file);

  expect(requestUrl).toBe(
    "/api/projects/prj_1/production-storyboard/shots/shot_1/assets/picture/2",
  );
  expect(requestInit?.method).toBe("PUT");
  expect((requestInit?.body as FormData).get("file")).toBe(file);
});

it("lists and clears persisted JSON Production assets", async () => {
  const requests: Array<[string, string | undefined]> = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    requests.push([url, init?.method]);
    if (init?.method === "DELETE") return new Response(null, { status: 204 });
    return new Response("[]", {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }));

  await listJsonShotAssets("prj_1");
  await clearJsonShotAsset("prj_1", "shot_1", "audio", 1);

  expect(requests).toEqual([
    ["/api/projects/prj_1/production-storyboard/assets", undefined],
    ["/api/projects/prj_1/production-storyboard/shots/shot_1/assets/audio/1", "DELETE"],
  ]);
});
