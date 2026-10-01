// @vitest-environment jsdom

import { afterEach, expect, it, vi } from "vitest";
import {
  cancelDirectorChatSession,
  chatWithDirectorStream,
  getDirectorChatSession,
  getDirectorVramStatus,
  uploadMusicMaster,
} from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

it("delivers usage before a terminal SSE error", async () => {
  const usage = { call_id: "call-1", status: "output_truncated", input_tokens: 28939, output_tokens: 4096 };
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(
        `data: ${JSON.stringify({ type: "context_usage", data: usage })}\n\n` +
        'data: {"type":"error","message":"Harness INCOMPLETE_TURN"}\n\n',
      ));
      controller.close();
    },
  });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, body: stream }));
  const observed: unknown[] = [];
  await expect(chatWithDirectorStream("prj_test", "hello", [], {
    onContextUsage: (value) => observed.push(value),
  })).rejects.toThrow("INCOMPLETE_TURN");
  expect(observed).toEqual([usage]);
});

it("uses multipart transport when Director chat includes images", async () => {
  const payload = {
    type: "result",
    data: {
      reply: "Visible",
      actions: [],
      project: { id: "prj_test" },
      shots: [],
      images: [],
      thinking: "",
      steps: [],
    },
  };
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(payload)}\n\n`));
      controller.close();
    },
  });
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, body: stream });
  vi.stubGlobal("fetch", fetchMock);
  const file = new File(["pixels"], "frame.png", { type: "image/png" });

  const result = await chatWithDirectorStream(
    "prj_test",
    "Inspect this",
    [],
    {},
    [file],
  );

  expect(result.reply).toBe("Visible");
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(url).toBe("/api/projects/prj_test/chat/stream/images");
  expect(init.headers).toEqual({ Accept: "text/event-stream" });
  expect(init.body).toBeInstanceOf(FormData);
  const form = init.body as FormData;
  expect(form.get("message")).toBe("Inspect this");
  expect(form.getAll("images")).toEqual([file]);
});

it("carries retry authority as typed data instead of chat text", async () => {
  const receipt = { retry_id: "r1", shot_id: "s1", source_version: "v1" };
  const stream = new ReadableStream({ start(controller) {
    controller.enqueue(new TextEncoder().encode('data: {"type":"result","data":{"reply":"saved"}}\n\n'));
    controller.close();
  } });
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, body: stream });
  vi.stubGlobal("fetch", fetchMock);
  await chatWithDirectorStream("prj_test", "Retry prompt", [], {}, [], undefined, receipt);
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
    message: "Retry prompt", history: [], prompt_retry: receipt,
  });
});

it("sends saved segment IDs and revision as typed chat context", async () => {
  const stream = new ReadableStream({ start(controller) {
    controller.enqueue(new TextEncoder().encode('data: {"type":"result","data":{"reply":"ok"}}\n\n'));
    controller.close();
  } });
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, body: stream });
  vi.stubGlobal("fetch", fetchMock);
  const selection = { revision: 2, ids: ["seg_a", "seg_b"] };
  await chatWithDirectorStream("prj_mv", "Discuss these", [], {}, [], undefined, undefined, selection);
  expect(JSON.parse(fetchMock.mock.calls[0][1].body).segment_selection).toEqual(selection);
});

it("uploads the selected song as the MV project master", async () => {
  const updated = {
    id: "prj_mv",
    name: "Music video",
    script_text: "",
    mode: "mv",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:01Z",
    shot_ids: [],
    music_master: {
      filename: "song.wav",
      relative_path: "music/master.wav",
      duration_s: 12.5,
      content_sha256: "a".repeat(64),
      source_format: "wav",
    },
  };
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => updated,
  });
  vi.stubGlobal("fetch", fetchMock);
  const file = new File(["audio"], "song.wav", { type: "audio/wav" });

  await expect(uploadMusicMaster("prj_mv", file)).resolves.toEqual(updated);

  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(url).toBe("/api/projects/prj_mv/music-master");
  expect(init.method).toBe("POST");
  expect(init.body).toBeInstanceOf(FormData);
  expect((init.body as FormData).get("file")).toBe(file);
});

it("fetches the global Director VRAM status", async () => {
  const payload = {
    chat_locked: false,
    generation_count: 0,
    generation_jobs: [],
  };
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => payload,
  });
  vi.stubGlobal("fetch", fetchMock);

  await expect(getDirectorVramStatus()).resolves.toEqual(payload);
  expect(fetchMock).toHaveBeenCalledWith("/api/director/vram");
});

it("preserves the generation error code from an HTTP rejection", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: false,
    status: 409,
    statusText: "Conflict",
    json: async () => ({
      detail: {
        code: "GPU_GENERATION_ACTIVE",
        message: "Local image or video generation is using the GPU.",
        generation_count: 2,
      },
    }),
  }));

  await expect(
    chatWithDirectorStream("prj_test", "Change the shot"),
  ).rejects.toMatchObject({
    name: "DirectorChatError",
    code: "GPU_GENERATION_ACTIVE",
    generationCount: 2,
  });
});

it("preserves the generation error code from an SSE race", async () => {
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(
        'data: {"type":"error","code":"GPU_GENERATION_ACTIVE","message":"GPU busy","generation_count":1}\n\n',
      ));
      controller.close();
    },
  });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, body: stream }));

  await expect(
    chatWithDirectorStream("prj_test", "Change the shot"),
  ).rejects.toMatchObject({
    name: "DirectorChatError",
    code: "GPU_GENERATION_ACTIVE",
    generationCount: 1,
  });
});

it("passes the cancellation signal to Director chat fetch", async () => {
  const controller = new AbortController();
  const fetchMock = vi.fn().mockRejectedValue(new DOMException("Aborted", "AbortError"));
  vi.stubGlobal("fetch", fetchMock);

  await expect(
    chatWithDirectorStream("prj_test", "Plan", [], {}, [], controller.signal),
  ).rejects.toMatchObject({ name: "AbortError" });

  expect((fetchMock.mock.calls[0][1] as RequestInit).signal).toBe(controller.signal);
});

it("reads and cancels the active Director chat session", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        active: true,
        session_id: "chat_1",
        started_at: "2026-09-02T00:00:00Z",
      }),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({ active: false, session_id: null, started_at: null }),
    });
  vi.stubGlobal("fetch", fetchMock);

  await expect(getDirectorChatSession("prj_test")).resolves.toMatchObject({ active: true });
  await expect(cancelDirectorChatSession("prj_test")).resolves.toMatchObject({ active: false });
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    "/api/projects/prj_test/chat/session",
    "/api/projects/prj_test/chat/session/cancel",
  ]);
  expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: "POST" });
});

it("does not start a second chat when a stream ends without a result", async () => {
  const stream = new ReadableStream({
    start(controller) {
      controller.close();
    },
  });
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, body: stream });
  vi.stubGlobal("fetch", fetchMock);

  await expect(chatWithDirectorStream("prj_test", "Plan")).rejects.toThrow(
    "Director chat stream ended without a result",
  );
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
