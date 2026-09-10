// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { JsonProductionDocument, JsonProductionShot, JsonShotJobRecord } from "./types";
import { JsonProductionPage } from "./JsonProductionPage";
import { getStoryboard, listJsonShotJobs, putStoryboard, submitJsonShot } from "./api";
import { cancelH3Job } from "../production/api";

const projectState = vi.hoisted(() => ({ projectId: "prj_test" as string | null }));

vi.mock("../../shared/project/ProjectContext", () => ({
  useProject: () => ({ projectId: projectState.projectId }),
}));

vi.mock("./api", () => ({
  getStoryboard: vi.fn(),
  putStoryboard: vi.fn(),
  submitJsonShot: vi.fn(),
  listJsonShotJobs: vi.fn(),
}));

vi.mock("../production/api", () => ({
  cancelH3Job: vi.fn(),
}));

const SHARED_PROMPT = {
  subject_definitions: "<Picture 1> defines Lu's identity and wardrobe.",
  summary: "<Picture 2> establishes the corridor composition.",
  retention_analysis: "Hold attention through the doorway reveal.",
  detailed_description: "0–6 seconds: Lu enters and stops at the desk.",
  overall_soundscape: "Quiet rain and fluorescent hum.",
  non_diegetic_music: "No non-diegetic music.",
};

const EMPTY_DOCUMENT: JsonProductionDocument = {
  version: 1,
  revision: 0,
  aspect_ratio: "16:9",
  shots: [],
};

function makeShot(
  id: string,
  title: string,
  overrides: Partial<JsonProductionShot> = {},
): JsonProductionShot {
  return {
    id,
    title,
    script_beat: `${title} beat`,
    duration_s: 6,
    dialogue: ["Stay quiet."],
    pictures: [
      { index: 1, role: "actor", label: "Lu identity and navy wardrobe" },
      { index: 2, role: "layout", label: "Post-entry blocking and corridor geography" },
    ],
    audio: [],
    prompt: { ...SHARED_PROMPT },
    ...overrides,
  };
}

function twoShotDocument(): JsonProductionDocument {
  return {
    version: 1,
    revision: 1,
    aspect_ratio: "16:9",
    shots: [
      makeShot("shot_001", "Corridor entry", {
        prompt: {
          ...SHARED_PROMPT,
          summary: "Shot one summary uses <Picture 2>.",
        },
      }),
      makeShot("shot_002", "Desk stop", {
        audio: [{ index: 1, label: "Footsteps and rain" }],
        prompt: {
          ...SHARED_PROMPT,
          summary: "Shot two summary uses <Picture 2>.",
          overall_soundscape: "Quiet rain and <Audio 1> footsteps.",
        },
      }),
    ],
  };
}

function jobRecord(
  overrides: Partial<JsonShotJobRecord> = {},
): JsonShotJobRecord {
  return {
    id: "job_json_1",
    status: "running",
    name: "JSON shot",
    notes: "",
    prompt: "",
    dialogue: [],
    frames: 180,
    error: null,
    comfy_prompt_id: null,
    external_task_id: null,
    created_at: "2026-08-26T00:00:00Z",
    updated_at: "2026-08-26T00:00:00Z",
    outputs: {},
    input_previews: {},
    pipeline_id: "h3_ref2va",
    project_id: "prj_test",
    json_shot_id: "shot_002",
    json_storyboard_revision: 1,
    ...overrides,
  };
}

function jsonFile(document: unknown, name = "board.json"): File {
  return new File([JSON.stringify(document)], name, { type: "application/json" });
}

async function importPastedJson(text: string) {
  fireEvent.change(screen.getByLabelText("Paste JSON"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Import JSON" }));
}

describe("JsonProductionPage import", () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    projectState.projectId = "prj_test";
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:json-preview"),
      revokeObjectURL: vi.fn(),
    });
    vi.mocked(getStoryboard).mockResolvedValue(EMPTY_DOCUMENT);
    vi.mocked(putStoryboard).mockResolvedValue(EMPTY_DOCUMENT);
    vi.mocked(listJsonShotJobs).mockResolvedValue([]);
    vi.mocked(submitJsonShot).mockResolvedValue(jobRecord({ status: "queued" }));
    vi.mocked(cancelH3Job).mockResolvedValue(jobRecord({ status: "cancelled" }));
  });

  it("shows empty-state JSON file and paste JSON controls", async () => {
    render(<JsonProductionPage active />);

    const file = await screen.findByLabelText("JSON file");
    expect((file as HTMLInputElement).accept).toMatch(/json/i);
    expect(screen.getByLabelText("Paste JSON")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Import JSON" })).toBeTruthy();
  });

  it("captures a large clipboard payload without truncating it", async () => {
    const document = {
      ...twoShotDocument(),
      shots: [
        makeShot("shot_large", "Large paste", {
          prompt: {
            ...SHARED_PROMPT,
            detailed_description: `0–6 seconds: ${"continuous motion detail ".repeat(1200)}`,
          },
        }),
      ],
    };
    const json = JSON.stringify(document);
    render(<JsonProductionPage active />);
    const textarea = await screen.findByLabelText("Paste JSON") as HTMLTextAreaElement;

    fireEvent.paste(textarea, {
      clipboardData: {
        getData: (type: string) => (type === "text/plain" ? json : ""),
      },
    });

    expect(textarea.value).toBe(json);
    expect(screen.getByText(`${json.length} characters`)).toBeTruthy();
  });

  it("never calls PUT when pasted JSON is invalid", async () => {
    render(<JsonProductionPage active />);
    await screen.findByLabelText("Paste JSON");

    await importPastedJson("{not-json");

    await screen.findByText(/malformed JSON/i);
    expect(putStoryboard).not.toHaveBeenCalled();
  });

  it("renders shots in the order returned by a successful PUT", async () => {
    const imported = twoShotDocument();
    vi.mocked(putStoryboard).mockResolvedValue({
      ...imported,
      revision: 1,
      shots: [
        makeShot("shot_b", "Returned second"),
        makeShot("shot_a", "Returned first"),
      ],
    });

    render(<JsonProductionPage active />);
    await screen.findByLabelText("Paste JSON");
    await importPastedJson(JSON.stringify(imported));

    await waitFor(() => expect(putStoryboard).toHaveBeenCalledTimes(1));
    const titles = screen.getAllByRole("button", { name: /Returned/ }).map(
      (el) => el.textContent,
    );
    expect(titles[0]).toMatch(/Returned second/);
    expect(titles[1]).toMatch(/Returned first/);
  });

  it("requires confirmation before reimport replaces an existing shot list", async () => {
    vi.mocked(getStoryboard).mockResolvedValue(twoShotDocument());
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<JsonProductionPage active />);
    await screen.findByRole("button", { name: /shot_001/ });

    const replacement = {
      ...twoShotDocument(),
      shots: [makeShot("shot_new", "Replacement shot")],
    };
    const input = screen.getByLabelText("JSON file") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [jsonFile(replacement)] } });

    await waitFor(() => expect(confirm).toHaveBeenCalled());
    expect(putStoryboard).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /shot_001/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /shot_002/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /shot_new/ })).toBeNull();

    confirm.mockReturnValue(true);
    vi.mocked(putStoryboard).mockResolvedValue({
      version: 1,
      revision: 2,
      aspect_ratio: "16:9",
      shots: [makeShot("shot_new", "Replacement shot")],
    });
    fireEvent.change(input, { target: { files: [jsonFile(replacement)] } });

    await waitFor(() => expect(putStoryboard).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("button", { name: /shot_new/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /shot_001/ })).toBeNull();
    confirm.mockRestore();
  });

  it("does not let a stale GET overwrite a successful import", async () => {
    const pending: Array<(doc: JsonProductionDocument) => void> = [];
    vi.mocked(getStoryboard).mockImplementation(
      () =>
        new Promise((resolve) => {
          pending.push(resolve);
        }),
    );
    const imported = { ...twoShotDocument(), revision: 1 };
    vi.mocked(putStoryboard).mockResolvedValue(imported);

    render(<JsonProductionPage active />);
    await screen.findByLabelText("Paste JSON");
    await importPastedJson(JSON.stringify(imported));
    await waitFor(() => expect(putStoryboard).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("button", { name: /shot_001/ })).toBeTruthy();

    expect(pending.length).toBeGreaterThan(0);
    await act(async () => {
      for (const resolve of pending) resolve(EMPTY_DOCUMENT);
    });

    expect(screen.getByRole("button", { name: /shot_001/ })).toBeTruthy();
    expect(screen.queryByLabelText("Paste JSON")).toBeNull();
  });
});

describe("JsonProductionPage three-column workspace", () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    projectState.projectId = "prj_test";
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:json-preview"),
      revokeObjectURL: vi.fn(),
    });
    vi.mocked(getStoryboard).mockResolvedValue(twoShotDocument());
    vi.mocked(putStoryboard).mockImplementation(async (_id, document) => ({
      ...document,
      revision: document.revision + 1,
    }));
    vi.mocked(listJsonShotJobs).mockResolvedValue([]);
    vi.mocked(submitJsonShot).mockResolvedValue(jobRecord({ status: "queued" }));
    vi.mocked(cancelH3Job).mockResolvedValue(jobRecord({ status: "cancelled" }));
  });

  it("selecting shot_002 updates the list, six prompt fields, and asset slots", async () => {
    render(<JsonProductionPage active />);
    fireEvent.click(await screen.findByRole("button", { name: /shot_002/ }));

    expect(screen.getByRole("button", { name: /shot_002/ }).getAttribute("aria-selected")).toBe(
      "true",
    );
    expect(screen.getByRole("button", { name: /shot_001/ }).getAttribute("aria-selected")).toBe(
      "false",
    );

    expect(
      (screen.getByLabelText("Subject definitions") as HTMLTextAreaElement).value,
    ).toContain("<Picture 1>");
    expect((screen.getByLabelText("Summary") as HTMLTextAreaElement).value).toBe(
      "Shot two summary uses <Picture 2>.",
    );
    expect(screen.getByLabelText("Retention analysis")).toBeTruthy();
    expect(screen.getByLabelText("Detailed description")).toBeTruthy();
    expect(screen.getByLabelText("Overall soundscape")).toBeTruthy();
    expect(screen.getByLabelText("Non-diegetic music")).toBeTruthy();

    const assets = screen.getByRole("region", { name: "Shot assets" });
    expect(within(assets).getByText("Picture 1 · Actor")).toBeTruthy();
    expect(within(assets).getByText("Picture 2 · Layout")).toBeTruthy();
    expect(within(assets).getByText(/Audio 1 ·/)).toBeTruthy();
  });

  it("groups each picture preview with only its own file actions", async () => {
    render(<JsonProductionPage active />);
    await screen.findByRole("button", { name: /shot_001/ });

    fireEvent.change(screen.getByLabelText("Picture 1 · Actor"), {
      target: { files: [new File([new Uint8Array([1])], "actor.png", { type: "image/png" })] },
    });
    fireEvent.change(screen.getByLabelText("Picture 2 · Layout"), {
      target: { files: [new File([new Uint8Array([2])], "layout.png", { type: "image/png" })] },
    });

    const actor = screen.getByRole("group", { name: "Picture 1 · Actor reference" });
    const layout = screen.getByRole("group", { name: "Picture 2 · Layout reference" });
    const actorPreview = within(actor).getByRole("img", { name: "Picture 1 · Actor preview" });
    const actorMaterialRow = actorPreview.closest(".json-asset-slot-body") as HTMLElement | null;
    expect(actorMaterialRow).toBeTruthy();
    expect(within(actorMaterialRow!).getByText("actor.png")).toBeTruthy();
    expect(within(actorMaterialRow!).getByText("Replace file")).toBeTruthy();
    expect(within(actorMaterialRow!).getByRole("button", { name: "Clear Picture 1 · Actor" })).toBeTruthy();
    expect(within(actor).queryByRole("button", { name: "Clear Picture 2 · Layout" })).toBeNull();
    expect(within(layout).getByRole("img", { name: "Picture 2 · Layout preview" })).toBeTruthy();
    expect(within(layout).getByRole("button", { name: "Clear Picture 2 · Layout" })).toBeTruthy();
  });

  it("keeps the prompt visible while the desktop inspector switches between references and output", async () => {
    render(<JsonProductionPage active />);
    await screen.findByRole("button", { name: /shot_001/ });

    const timeline = screen.getByRole("complementary", { name: "Shot timeline" });
    const workspace = timeline.closest(".json-desktop-workspace");
    expect(workspace?.firstElementChild).toBe(timeline);

    const inspector = screen.getByRole("navigation", { name: "Shot inspector" });
    const separator = screen.getByRole("separator", { name: "Resize prompt and References or Output" });
    expect(separator.getAttribute("aria-valuenow")).toBe("65");
    expect(within(inspector).getByRole("button", { name: "References" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByLabelText("Subject definitions")).toBeTruthy();
    expect(screen.getByText("Picture 1 · Actor")).toBeTruthy();

    fireEvent.click(within(inspector).getByRole("button", { name: "Output" }));

    expect(within(inspector).getByRole("button", { name: "Output" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByLabelText("Subject definitions")).toBeTruthy();
    expect(screen.queryByText("Picture 1 · Actor")).toBeNull();
    expect(screen.getByText("No H3 job yet for this shot.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Generate" })).toBeTruthy();
  });

  it("renders the desktop shot timeline as compact numbered bookmarks", async () => {
    render(<JsonProductionPage active />);

    const timeline = await screen.findByRole("complementary", { name: "Shot timeline" });
    const bookmarks = within(timeline).getAllByRole("button");

    expect(bookmarks).toHaveLength(2);
    expect(bookmarks[0].classList.contains("json-shot-bookmark")).toBe(true);
    expect(within(bookmarks[0]).getByText("01")).toBeTruthy();
    expect(within(bookmarks[1]).getByText("02")).toBeTruthy();
    expect(bookmarks[0].querySelector(".json-shot-bookmark-status")).toBeTruthy();
  });

  it("uses a compact three-step workflow on mobile while keeping Generate available", async () => {
    render(<JsonProductionPage active mobile />);

    const shotPicker = (await screen.findByLabelText("Current shot")) as HTMLSelectElement;
    expect(shotPicker.value).toBe("shot_001");
    expect(screen.queryByRole("button", { name: /shot_001/ })).toBeNull();

    const workflow = screen.getByRole("navigation", { name: "Shot workflow" });
    expect(screen.queryByRole("separator", { name: "Resize prompt and References or Output" })).toBeNull();
    expect(within(workflow).getByRole("button", { name: "Prompt" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByLabelText("Subject definitions")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Generate" })).toBeTruthy();

    fireEvent.click(within(workflow).getByRole("button", { name: "References" }));
    expect(screen.getByText("Picture 1 · Actor")).toBeTruthy();
    const pictureInput = screen.getByLabelText("Picture 1 · Actor");
    expect(pictureInput.closest(".json-upload-control")?.textContent).toBe("Choose file");
    expect(screen.queryByLabelText("Subject definitions")).toBeNull();
    expect(screen.getByRole("button", { name: "Generate" })).toBeTruthy();

    fireEvent.click(within(workflow).getByRole("button", { name: "Output" }));
    expect(screen.getByText("No H3 job yet for this shot.")).toBeTruthy();
    expect(screen.queryByText("Picture 1 · Actor")).toBeNull();
    expect(screen.getByRole("button", { name: "Generate" })).toBeTruthy();

    fireEvent.change(shotPicker, { target: { value: "shot_002" } });
    expect(await screen.findByText("No H3 job yet for this shot.")).toBeTruthy();
    expect(shotPicker.value).toBe("shot_002");
  });

  it("has no Library picker, Layout generator, QC, or Director action", async () => {
    render(<JsonProductionPage active />);
    await screen.findByRole("button", { name: /shot_001/ });
    fireEvent.click(screen.getByRole("button", { name: /shot_002/ }));

    expect(screen.queryByText(/Voice Library/i)).toBeNull();
    expect(screen.queryByLabelText(/Add Voice reference/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /skip layout/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /insert image/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /layout generator|generate layout/i })).toBeNull();
    expect(screen.queryByText("Reference QC")).toBeNull();
    expect(screen.queryByRole("button", { name: /^Director$/i })).toBeNull();
    expect(screen.queryByText(/bypasses the local Director Agent/i)).toBeNull();
  });

  it("keeps prompt edits local until Save prompt changes and disables Generate while dirty", async () => {
    render(<JsonProductionPage active />);
    fireEvent.click(await screen.findByRole("button", { name: /shot_002/ }));

    const summary = screen.getByLabelText("Summary") as HTMLTextAreaElement;
    fireEvent.change(summary, {
      target: { value: "Local unsaved summary with <Picture 2>." },
    });
    expect(putStoryboard).not.toHaveBeenCalled();
    expect((screen.getByRole("button", { name: "Generate" }) as HTMLButtonElement).disabled).toBe(
      true,
    );

    fireEvent.click(screen.getByRole("button", { name: "Save prompt changes" }));
    await waitFor(() => expect(putStoryboard).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(putStoryboard).mock.calls[0][1];
    expect(payload.shots[1].prompt.summary).toBe(
      "Local unsaved summary with <Picture 2>.",
    );
    expect(payload.shots).toHaveLength(2);
  });

  it("parses a complete selected-shot JSON and adds reference slots without replacing other shots", async () => {
    render(<JsonProductionPage active />);
    fireEvent.click(await screen.findByRole("button", { name: /shot_002/ }));

    fireEvent.change(screen.getByLabelText("Picture 1 · Actor"), {
      target: { files: [new File([new Uint8Array([1])], "actor.png", { type: "image/png" })] },
    });

    fireEvent.click(screen.getByRole("button", { name: "Edit shot JSON" }));
    const editor = screen.getByLabelText("Shot JSON") as HTMLTextAreaElement;
    const edited = JSON.parse(editor.value) as JsonProductionShot;
    edited.duration_s = 8;
    edited.pictures.push({ index: 3, role: "prop", label: "Evidence envelope" });
    edited.prompt.detailed_description += " <Picture 3> is placed on the desk.";
    fireEvent.change(editor, { target: { value: JSON.stringify(edited, null, 2) } });
    fireEvent.click(screen.getByRole("button", { name: "Parse & save" }));

    await waitFor(() => expect(putStoryboard).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(putStoryboard).mock.calls[0][1];
    expect(payload.shots).toHaveLength(2);
    expect(payload.shots[0].id).toBe("shot_001");
    expect(payload.shots[1].id).toBe("shot_002");
    expect(payload.shots[1].duration_s).toBe(8);
    expect(payload.shots[1].pictures[2]).toEqual({
      index: 3,
      role: "prop",
      label: "Evidence envelope",
    });

    const assets = screen.getByRole("region", { name: "Shot assets" });
    expect(within(assets).getByText("Picture 3 · Prop")).toBeTruthy();
    expect(screen.getByText("actor.png")).toBeTruthy();
  });

  it("shows a single-shot JSON parse error without saving", async () => {
    render(<JsonProductionPage active />);
    fireEvent.click(await screen.findByRole("button", { name: /shot_002/ }));
    fireEvent.click(screen.getByRole("button", { name: "Edit shot JSON" }));
    fireEvent.change(screen.getByLabelText("Shot JSON"), {
      target: { value: "{not-json" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Parse & save" }));

    expect(await screen.findByText(/malformed JSON/i)).toBeTruthy();
    expect(putStoryboard).not.toHaveBeenCalled();
  });

  it("submits selected files in slot order, polls by json_shot_id, and keeps files after completion", async () => {
    const queued = jobRecord({ status: "queued", json_shot_id: "shot_002" });
    const succeeded = jobRecord({
      status: "succeeded",
      json_shot_id: "shot_002",
      outputs: {
        video: {
          key: "video",
          label: "Enhanced",
          url: "/api/files/jobs/job_json_1/enhanced.mp4",
        },
        video_raw: {
          key: "video_raw",
          label: "Raw",
          url: "/api/files/jobs/job_json_1/raw.mp4",
        },
      },
    });
    vi.mocked(submitJsonShot).mockResolvedValue(queued);
    vi.mocked(listJsonShotJobs).mockResolvedValue([]);

    render(<JsonProductionPage active />);
    fireEvent.click(await screen.findByRole("button", { name: /shot_002/ }));

    expect((screen.getByRole("button", { name: "Generate" }) as HTMLButtonElement).disabled).toBe(
      true,
    );

    fireEvent.change(screen.getByLabelText("Picture 1 · Actor"), {
      target: { files: [new File([new Uint8Array([1])], "actor.png", { type: "image/png" })] },
    });
    fireEvent.change(screen.getByLabelText("Picture 2 · Layout"), {
      target: { files: [new File([new Uint8Array([2])], "layout.png", { type: "image/png" })] },
    });
    fireEvent.change(screen.getByLabelText(/Audio 1/), {
      target: {
        files: [new File([new Uint8Array([3])], "steps.wav", { type: "audio/wav" })],
      },
    });

    expect(screen.getByText("actor.png")).toBeTruthy();
    expect(screen.getByText("steps.wav")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Generate" }) as HTMLButtonElement).disabled).toBe(
      false,
    );

    fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    await waitFor(() => expect(submitJsonShot).toHaveBeenCalledTimes(1));
    const submitted = vi.mocked(submitJsonShot).mock.calls[0];
    expect(submitted[0]).toBe("prj_test");
    expect(submitted[1]).toBe("shot_002");
    expect(submitted[2]).toBe(1);
    expect([...submitted[3].pictures.keys()]).toEqual([1, 2]);
    expect(submitted[3].pictures.get(1)?.name).toBe("actor.png");
    expect(submitted[3].pictures.get(2)?.name).toBe("layout.png");
    expect([...submitted[3].audio.keys()]).toEqual([1]);

    vi.mocked(listJsonShotJobs).mockClear();
    vi.mocked(listJsonShotJobs).mockResolvedValue([succeeded]);
    await waitFor(
      () => expect(listJsonShotJobs).toHaveBeenCalledWith("prj_test", "shot_002", 1),
      { timeout: 3000 },
    );
    expect(await screen.findByRole("heading", { name: "Output v1" })).toBeTruthy();
    const player = document.querySelector("video.h3-preview") as HTMLVideoElement | null;
    expect(player?.getAttribute("src")).toBe("/api/files/jobs/job_json_1/enhanced.mp4");
    expect((await screen.findByRole("link", { name: /enhanced/i })).getAttribute("href")).toBe(
      "/api/files/jobs/job_json_1/enhanced.mp4",
    );
    expect(screen.queryByRole("link", { name: /raw/i })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "References" }));
    expect(screen.getByText("actor.png")).toBeTruthy();
    expect(screen.getByText("steps.wav")).toBeTruthy();
  });

  it("ignores older storyboard revisions and versions current generations only", async () => {
    const stale = jobRecord({
      id: "job_stale",
      created_at: "2026-08-26T00:00:00Z",
      updated_at: "2026-08-27T12:00:00Z",
      json_storyboard_revision: 0,
      status: "succeeded",
      outputs: {
        video: {
          key: "video",
          label: "Enhanced",
          url: "/api/files/jobs/job_stale/enhanced.mp4",
        },
      },
    });
    const currentFirst = jobRecord({
      id: "job_current_1",
      created_at: "2026-08-27T00:00:00Z",
      updated_at: "2026-08-27T00:05:00Z",
      status: "succeeded",
    });
    const newer = jobRecord({
      id: "job_new",
      created_at: "2026-08-27T01:00:00Z",
      updated_at: "2026-08-27T01:05:00Z",
      status: "succeeded",
      outputs: {
        video: {
          key: "video",
          label: "Enhanced",
          url: "/api/files/jobs/job_new/enhanced.mp4",
        },
        video_raw: {
          key: "video_raw",
          label: "Raw",
          url: "/api/files/jobs/job_new/raw.mp4",
        },
      },
    });
    vi.mocked(listJsonShotJobs).mockImplementation(async (_projectId, shotId) =>
      shotId === "shot_002" ? [stale, currentFirst, newer] : [],
    );

    render(<JsonProductionPage active />);
    fireEvent.click(await screen.findByRole("button", { name: /shot_002/ }));
    fireEvent.click(screen.getByRole("button", { name: "Output" }));

    expect(await screen.findByRole("heading", { name: "Output v2" })).toBeTruthy();
    const player = document.querySelector("video.h3-preview") as HTMLVideoElement | null;
    expect(player?.getAttribute("src")).toBe("/api/files/jobs/job_new/enhanced.mp4");
    expect(screen.getByRole("link", { name: /enhanced/i }).getAttribute("href")).toBe(
      "/api/files/jobs/job_new/enhanced.mp4",
    );
    expect(screen.queryByRole("heading", { name: "Output v1" })).toBeNull();
    expect(screen.queryByRole("link", { name: /job_stale/i })).toBeNull();
    expect(
      document.querySelector('a[href="/api/files/jobs/job_stale/enhanced.mp4"]'),
    ).toBeNull();
  });

  it("keeps selected Picture and Audio files when the file dialog is cancelled", async () => {
    render(<JsonProductionPage active />);
    fireEvent.click(await screen.findByRole("button", { name: /shot_002/ }));

    fireEvent.change(screen.getByLabelText("Picture 1 · Actor"), {
      target: { files: [new File([new Uint8Array([1])], "actor.png", { type: "image/png" })] },
    });
    fireEvent.change(screen.getByLabelText(/Audio 1/), {
      target: {
        files: [new File([new Uint8Array([3])], "steps.wav", { type: "audio/wav" })],
      },
    });
    expect(screen.getByText("actor.png")).toBeTruthy();
    expect(screen.getByText("steps.wav")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Picture 1 · Actor"), {
      target: { files: [] },
    });
    fireEvent.change(screen.getByLabelText(/Audio 1/), {
      target: { files: [] },
    });

    expect(screen.getByText("actor.png")).toBeTruthy();
    expect(screen.getByText("steps.wav")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Clear Picture 1 · Actor" }));
    fireEvent.click(screen.getByRole("button", { name: /Clear Audio 1/ }));
    expect(screen.queryByText("actor.png")).toBeNull();
    expect(screen.queryByText("steps.wav")).toBeNull();
  });

  it("clears file Maps, preview URLs, and jobs when the project changes", async () => {
    projectState.projectId = "prj_a";
    vi.mocked(getStoryboard).mockImplementation(async () => twoShotDocument());

    const { rerender } = render(<JsonProductionPage active />);
    fireEvent.click(await screen.findByRole("button", { name: /shot_002/ }));
    fireEvent.change(screen.getByLabelText("Picture 1 · Actor"), {
      target: { files: [new File([new Uint8Array([1])], "actor.png", { type: "image/png" })] },
    });
    expect(screen.getByText("actor.png")).toBeTruthy();
    expect(URL.createObjectURL).toHaveBeenCalled();

    projectState.projectId = "prj_b";
    rerender(<JsonProductionPage active />);
    await waitFor(() => expect(screen.queryByText("actor.png")).toBeNull());
    fireEvent.click(await screen.findByRole("button", { name: /shot_002/ }));

    expect(screen.queryByText("actor.png")).toBeNull();
    expect(URL.revokeObjectURL).toHaveBeenCalled();
    expect(vi.mocked(getStoryboard).mock.calls.some((call) => call[0] === "prj_b")).toBe(true);
  });

  it("cancels the active H3 job with the existing cancel endpoint", async () => {
    vi.mocked(listJsonShotJobs).mockResolvedValue([
      jobRecord({ status: "running", json_shot_id: "shot_002" }),
    ]);

    render(<JsonProductionPage active />);
    fireEvent.click(await screen.findByRole("button", { name: /shot_002/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(cancelH3Job).toHaveBeenCalledWith("job_json_1"));
  });
});
