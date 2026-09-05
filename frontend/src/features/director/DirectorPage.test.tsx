// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Project, ProjectDetail, ProjectMode, Shot } from "../../shared/api/types";
import { DirectorPage } from "./DirectorPage";
import {
  cancelDirectorChatSession,
  DirectorChatError,
  chatWithDirectorStream,
  getDirectorChatSession,
  getDirectorModel,
  getDirectorVramStatus,
  getProject,
  queueRefFrame,
} from "./api";

const projectState = vi.hoisted(() => ({
  projectId: "prj_test" as string | null,
  project: {
    id: "prj_test",
    name: "Test project",
    script_text: "INT. HALLWAY - DAY",
    mode: "director" as ProjectMode,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    shot_ids: ["sht_1"],
  } as Project | null,
}));

const getDirectorChatHistoryMock = vi.hoisted(() => vi.fn());

vi.mock("../../shared/project/ProjectContext", () => ({
  useProject: () => ({
    projectId: projectState.projectId,
    project: projectState.project,
    refreshProjects: vi.fn(),
    createAndSelect: vi.fn(),
  }),
}));

vi.mock("./api", () => ({
  DirectorChatError: class DirectorChatError extends Error {
    code: string;
    generationCount: number;

    constructor(message: string, code: string, generationCount = 0) {
      super(message);
      this.name = "DirectorChatError";
      this.code = code;
      this.generationCount = generationCount;
    }
  },
  chatWithDirectorStream: vi.fn(),
  cancelDirectorChatSession: vi.fn(),
  getDirectorChatHistory: getDirectorChatHistoryMock,
  getDirectorChatSession: vi.fn(),
  getDirectorModel: vi.fn().mockResolvedValue({
    model: "qwen3.6:27b",
    available: ["qwen3.6:27b"],
  }),
  getDirectorVramStatus: vi.fn(),
  getProject: vi.fn(),
  queueRefFrame: vi.fn(),
  replaceShotMaterials: vi.fn(),
  setDirectorModel: vi.fn(),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
}

const testShot: Shot = {
  id: "sht_1",
  project_id: "prj_test",
  scene_id: "sc01",
  title: "Corridor walk-in",
  script_beat: "The actor enters.",
  duration_s: 6,
  status: "needs_review",
      refs: [],
      voice_refs: [],
  prompt_sections: {
    subject_definitions: "",
    summary: "",
    retention_analysis: "",
    detailed_description: "",
    overall_soundscape: "",
    non_diegetic_music: "",
  },
  dialogue: [],
  layout_asset_id: "lay_1",
  layout_review_status: "pending_review",
  ref_frame_job_id: "job_1",
      h3_job_id: null,
      source_audio_path: null,
  feedback: "",
  blocked_reasons: [],
  meta: {},
  layout_refs: [],
};

describe("Director shot actions", () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(chatWithDirectorStream).mockReset();
    Element.prototype.scrollIntoView = vi.fn();
    projectState.projectId = "prj_test";
    projectState.project = {
      id: "prj_test",
      name: "Test project",
      script_text: "INT. HALLWAY - DAY",
      mode: "director",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      shot_ids: [testShot.id],
    };
    getDirectorChatHistoryMock.mockResolvedValue([]);
    vi.mocked(getDirectorChatSession).mockResolvedValue({
      active: false,
      session_id: null,
      started_at: null,
    });
    vi.mocked(cancelDirectorChatSession).mockResolvedValue({
      active: false,
      session_id: null,
      started_at: null,
    });
    vi.mocked(getDirectorVramStatus).mockResolvedValue({
      chat_locked: false,
      generation_count: 0,
      generation_jobs: [],
    });
    vi.mocked(getProject).mockResolvedValue({
      project: {
        id: "prj_test",
        name: "Test project",
        script_text: "INT. HALLWAY - DAY",
        mode: "director",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
        shot_ids: [testShot.id],
      },
      shots: [testShot],
    });
    vi.mocked(queueRefFrame).mockResolvedValue([
      { ...testShot, status: "ref_frame_pending" },
    ]);
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:director-chat-preview"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
  });

  it("shows a read-only bypass notice for JSON Production projects and calls no Agent APIs", async () => {
    projectState.project = {
      id: "prj_json",
      name: "JSON board",
      script_text: "",
      mode: "json_production",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      shot_ids: [],
    };
    projectState.projectId = "prj_json";

    render(<DirectorPage />);

    expect(
      await screen.findByText("This project bypasses the local Director Agent"),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Plan shots" })).toBeNull();
    expect(getProject).not.toHaveBeenCalled();
    expect(getDirectorModel).not.toHaveBeenCalled();
    expect(chatWithDirectorStream).not.toHaveBeenCalled();
    expect(queueRefFrame).not.toHaveBeenCalled();
  });

  it("hides shot controls while keeping chat in chat-only mode", async () => {
    render(<DirectorPage chatOnly />);

    expect(await screen.findByRole("button", { name: "Send" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Shots" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Plan shots" })).toBeNull();
    await waitFor(() =>
      expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled(),
    );
  });

  it("shows the Director Agent mascot holding a shot board in the desktop header", async () => {
    const { container } = render(<DirectorPage />);

    await screen.findByRole("heading", { name: "Director" });
    const mascot = screen.getByRole("img", { name: "Director Agent holding a shot board" });
    expect(mascot.getAttribute("src")).toBe("/director-agent-shot-board.png");
    expect(mascot.closest(".director-identity")).toBeTruthy();
    expect(container.querySelector(".director-chat-header-row > .director-identity")).toBeTruthy();
    expect(container.querySelector(".director-chat-header.workspace-panel-header")).toBeTruthy();
    expect(container.querySelector(".shot-workspace-header.workspace-panel-header")).toBeTruthy();
    expect(container.querySelector(".director-model-picker.inline-model-picker")).toBeTruthy();
  });

  it("disables Director chat when Ollama has no installed models", async () => {
    vi.mocked(getDirectorModel).mockResolvedValueOnce({
      model: "",
      provider: "ollama",
      reachable: true,
      available: [],
    });

    render(<DirectorPage />);

    expect(await screen.findByRole("option", { name: "No models available" })).toBeTruthy();
    expect(
      (screen.getByPlaceholderText(/Talk to the Director/) as HTMLTextAreaElement).disabled,
    ).toBe(true);
    expect((screen.getByRole("button", { name: "Send" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("restores the complete saved Director conversation when a project opens", async () => {
    getDirectorChatHistoryMock.mockResolvedValue([
      {
        id: "msg_1",
        role: "user",
        content: "Keep the lighting warm.",
        created_at: "2026-08-29T10:00:00Z",
        images: [],
      },
      {
        id: "msg_2",
        role: "assistant",
        content: "I will preserve the warm lighting across the shots.",
        created_at: "2026-08-29T10:00:01Z",
        images: [],
      },
    ]);

    render(<DirectorPage />);

    expect(await screen.findByText("Keep the lighting warm.")).toBeTruthy();
    expect(
      screen.getByText("I will preserve the warm lighting across the shots."),
    ).toBeTruthy();
    expect(screen.queryByText(/Working on \*\*Test project\*\*/)).toBeNull();
  });

  it("replaces Send with Cancel and disables the composer during a local response", async () => {
    const action = deferred<Awaited<ReturnType<typeof chatWithDirectorStream>>>();
    vi.mocked(chatWithDirectorStream).mockReturnValueOnce(action.promise);
    render(<DirectorPage />);
    await screen.findByRole("heading", { name: "1. Corridor walk-in" });

    fireEvent.change(screen.getByPlaceholderText(/Talk to the Director/), {
      target: { value: "Plan it" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("button", { name: "Cancel" })).toBeTruthy();
    expect((screen.getByPlaceholderText(/Talk to the Director/) as HTMLTextAreaElement).disabled).toBe(true);
    expect((screen.getByLabelText("Add images") as HTMLInputElement).disabled).toBe(true);
  });

  it("cancels the backend session without adding an error bubble", async () => {
    vi.mocked(chatWithDirectorStream).mockImplementationOnce(
      (_projectId, _message, _history, _handlers, _images, signal) =>
        new Promise((_resolve, reject) => {
          signal?.addEventListener("abort", () => {
            reject(new DOMException("Aborted", "AbortError"));
          });
        }),
    );
    render(<DirectorPage />);
    await screen.findByRole("heading", { name: "1. Corridor walk-in" });

    fireEvent.change(screen.getByPlaceholderText(/Talk to the Director/), {
      target: { value: "Plan it" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(cancelDirectorChatSession).toHaveBeenCalledWith("prj_test"));
    expect(await screen.findByRole("button", { name: "Send" })).toBeTruthy();
    expect(screen.queryByText(/Something went wrong/)).toBeNull();
  });

  it("shows a refreshed active session and reloads history when it finishes", async () => {
    vi.useFakeTimers();
    vi.mocked(getDirectorChatSession)
      .mockResolvedValueOnce({
        active: true,
        session_id: "chat_1",
        started_at: "2026-09-02T00:00:00Z",
      })
      .mockResolvedValueOnce({ active: false, session_id: null, started_at: null });
    getDirectorChatHistoryMock
      .mockResolvedValueOnce([{ role: "user", content: "Plan it" }])
      .mockResolvedValueOnce([
        { role: "user", content: "Plan it" },
        { role: "assistant", content: "The completed reply" },
      ]);

    render(<DirectorPage />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByText("LLM busy — response is still running")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
    expect((screen.getByPlaceholderText(/Talk to the Director/) as HTMLTextAreaElement).disabled).toBe(true);

    await act(async () => {
      vi.advanceTimersByTime(1500);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(getDirectorChatHistoryMock).toHaveBeenCalledTimes(2);
    expect(screen.getByText("The completed reply")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
  });

  it("keeps only the conversation surface in mobile chat-only mode", async () => {
    render(<DirectorPage mobile chatOnly />);

    expect(await screen.findByRole("button", { name: "Send" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Shots, 1 planned" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Project status" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Write H3 prompt/ })).toBeNull();
    expect(screen.queryByRole("separator", { name: "Resize Director chat and Shots" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Shot workspace" })).toBeNull();
  });

  it("keeps chat project-wide without showing a redundant scope subtitle", async () => {
    vi.mocked(chatWithDirectorStream).mockResolvedValue({
      reply: "Project-wide answer", actions: [], project: projectState.project!,
      shots: [testShot], images: [], thinking: "", steps: [],
    });
    render(<DirectorPage />);
    await screen.findByRole("heading", { name: "1. Corridor walk-in" });

    expect(screen.queryByText("Project-wide Director")).toBeNull();
    fireEvent.change(screen.getByPlaceholderText(/Talk to the Director/), {
      target: { value: "What should we improve next?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText("Project-wide answer");
    expect(chatWithDirectorStream).toHaveBeenCalledWith(
      "prj_test", "What should we improve next?", expect.any(Array), expect.any(Object), [], expect.any(AbortSignal),
    );
  });

  it("streams one queued material review request with live process and reasoning", async () => {
    const action = deferred<Awaited<ReturnType<typeof chatWithDirectorStream>>>();
    vi.mocked(chatWithDirectorStream).mockImplementationOnce((_projectId, _message, _history, handlers) => {
      handlers?.onStatus?.("Reviewing changed Picture references");
      handlers?.onThink?.("Checking whether the actor and scene still support the shot.");
      return action.promise;
    });
    const requestedMessage = {
      id: "material-review-sht_1-1",
      projectId: "prj_test",
      message:
        "Shot 01 references changed. Review the current materials and rewrite its H3 prompt. If a critical reference is missing or conflicting, ask one concrete question instead.",
    };

    const { rerender } = render(<DirectorPage requestedMessage={requestedMessage} />);

    expect(await screen.findByText(requestedMessage.message)).toBeTruthy();
    expect(await screen.findByText("Reviewing changed Picture references")).toBeTruthy();
    expect(screen.getByText("Checking whether the actor and scene still support the shot.")).toBeTruthy();
    expect(chatWithDirectorStream).toHaveBeenCalledTimes(1);

    rerender(<DirectorPage requestedMessage={requestedMessage} />);
    expect(chatWithDirectorStream).toHaveBeenCalledTimes(1);

    action.resolve({
      reply: "The references are coherent and the H3 prompt is updated.",
      actions: ["write_prompt"],
      project: projectState.project!,
      shots: [testShot],
      images: [],
      thinking: "",
      steps: ["Reviewing changed Picture references"],
    });
    expect(await screen.findByText("The references are coherent and the H3 prompt is updated.")).toBeTruthy();
  });

  it("previews uploaded images and sends them to the Director Agent", async () => {
    vi.mocked(chatWithDirectorStream).mockResolvedValue({
      reply: "I can see the blocking.", actions: [], project: projectState.project!,
      shots: [testShot], images: [], thinking: "", steps: [],
    });
    render(<DirectorPage />);
    await screen.findByRole("heading", { name: "1. Corridor walk-in" });
    const file = new File(["image-data"], "blocking.png", { type: "image/png" });

    fireEvent.change(screen.getByLabelText("Add images"), {
      target: { files: [file] },
    });

    expect(screen.getByRole("img", { name: "blocking.png" })).toBeTruthy();
    expect(screen.getByText("blocking.png")).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText(/Talk to the Director/), {
      target: { value: "Check this composition." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText("I can see the blocking.");
    expect(chatWithDirectorStream).toHaveBeenCalledWith(
      "prj_test",
      "Check this composition.",
      expect.any(Array),
      expect.any(Object),
      [file],
      expect.any(AbortSignal),
    );
  });

  it("locks the composer and advances generation time locally", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-31T10:02:37Z"));
    vi.mocked(getDirectorVramStatus).mockResolvedValue({
      chat_locked: true,
      generation_count: 3,
      generation_jobs: [
        {
          job_id: "job_video",
          pipeline_id: "h3_ref2va",
          kind: "video",
          status: "running",
          phase: "generating",
          queued_at: "2026-08-31T10:00:00Z",
        },
        {
          job_id: "job_image_1",
          pipeline_id: "ref_frame",
          kind: "image",
          status: "queued",
          phase: "queued",
          queued_at: "2026-08-31T10:01:00Z",
        },
        {
          job_id: "job_image_2",
          pipeline_id: "actor",
          kind: "image",
          status: "queued",
          phase: "queued",
          queued_at: "2026-08-31T10:02:00Z",
        },
      ],
    });

    render(<DirectorPage />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByText("Generating video · 02:37 · 2 jobs waiting")).toBeTruthy();
    expect((screen.getByPlaceholderText(/Talk to the Director/) as HTMLTextAreaElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Send" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText("Add images") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Project status" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("combobox") as HTMLSelectElement).disabled).toBe(true);
    expect(getDirectorVramStatus).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(1000);
      await Promise.resolve();
    });
    expect(screen.getByText("Generating video · 02:38 · 2 jobs waiting")).toBeTruthy();
    expect(getDirectorVramStatus).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(1500);
      await Promise.resolve();
    });
    expect(getDirectorVramStatus).toHaveBeenCalledTimes(2);
  });

  it("restores draft and image after a generation race", async () => {
    vi.mocked(chatWithDirectorStream).mockRejectedValueOnce(
      new DirectorChatError("GPU busy", "GPU_GENERATION_ACTIVE", 1),
    );
    const { container } = render(<DirectorPage />);
    await screen.findByRole("heading", { name: "1. Corridor walk-in" });
    const file = new File(["image-data"], "blocking.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("Add images"), {
      target: { files: [file] },
    });
    fireEvent.change(screen.getByPlaceholderText(/Talk to the Director/), {
      target: { value: "Keep this draft" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(chatWithDirectorStream).toHaveBeenCalled());

    expect(screen.getByDisplayValue("Keep this draft")).toBeTruthy();
    expect(screen.getByRole("img", { name: "blocking.png" })).toBeTruthy();
    expect(container.querySelectorAll(".chat-bubble.user")).toHaveLength(0);
    expect(screen.queryByText(/Something went wrong/)).toBeNull();
  });

  it("marks the desktop workspace to fill the available page height", async () => {
    const { container } = render(<DirectorPage />);
    await screen.findByRole("heading", { name: "1. Corridor walk-in" });

    expect(container.querySelector("main.director-fill-viewport")).toBeTruthy();
    expect(container.querySelector(".director-resizable-workspace")).toBeTruthy();
  });

  it("shows provider runtime separately from durable Process steps", async () => {
    const action = deferred<Awaited<ReturnType<typeof chatWithDirectorStream>>>();
    vi.mocked(chatWithDirectorStream).mockImplementationOnce((_projectId, _message, _history, handlers) => {
      handlers?.onRuntime?.("qwen3.6:27b ready on GPU");
      handlers?.onStatus?.("Executing storyboard tool");
      return action.promise;
    });
    render(<DirectorPage />);
    await screen.findByRole("heading", { name: "1. Corridor walk-in" });

    fireEvent.change(screen.getByPlaceholderText(/Talk to the Director/), {
      target: { value: "Plan this scene" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("qwen3.6:27b ready on GPU")).toBeTruthy();
    expect(screen.getByText("Runtime")).toBeTruthy();
    expect(screen.getByText("Executing storyboard tool")).toBeTruthy();
    expect(screen.getByText("Process")).toBeTruthy();

    action.resolve({
      reply: "Planned", actions: [], project: projectState.project!, shots: [testShot],
      images: [], thinking: "", steps: ["Executing storyboard tool"],
    });
  });

  it("does not expose the retired Shot reference action", async () => {
    render(<DirectorPage />);
    await screen.findByRole("heading", { name: "1. Corridor walk-in" });

    expect(screen.queryByRole("button", { name: "Reference in chat" })).toBeNull();
  });

  it("keeps the selected Shot prompt action in the main shortcut row", async () => {
    const secondShot = { ...testShot, id: "sht_2", title: "Doorway reveal" };
    vi.mocked(getProject).mockResolvedValue({
      project: { ...projectState.project!, shot_ids: [testShot.id, secondShot.id] },
      shots: [testShot, secondShot],
    });
    vi.mocked(chatWithDirectorStream).mockResolvedValue({
      reply: "Prompt drafted", actions: [], project: projectState.project!,
      shots: [testShot, secondShot], images: [], thinking: "", steps: [],
    });
    render(<DirectorPage />);
    await screen.findByRole("heading", { name: "1. Corridor walk-in" });

    fireEvent.click(screen.getByRole("button", { name: "Shot 2 · Doorway reveal" }));

    expect(screen.getByText("Needs prompt", { selector: ".status-chip" })).toBeTruthy();
    expect(screen.queryByText("needs_review")).toBeNull();
    expect(screen.getByRole("button", { name: "Generate reference frame" })).toBeTruthy();
    const promptAction = screen.getByRole("button", { name: "Write H3 prompt · Shot 02" });
    const shortcutRow = promptAction.closest(".chat-chips");
    expect(shortcutRow).toBeTruthy();
    expect(shortcutRow?.querySelectorAll("button")).toHaveLength(4);
    expect(screen.queryByRole("button", { name: "Review images" })).toBeNull();
    expect(screen.queryByText("Selected Shot")).toBeNull();

    fireEvent.click(promptAction);
    await screen.findByText("Prompt drafted");
    expect(chatWithDirectorStream).toHaveBeenCalledWith(
      "prj_test", "Write the H3 prompt for shot 2", expect.any(Array), expect.any(Object), [], expect.any(AbortSignal),
    );
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject" })).toBeNull();
  });

  it("uses Add reference frame to start a shot-specific Director discussion", async () => {
    vi.mocked(getProject).mockResolvedValue({
      project: {
        id: "prj_test",
        name: "Test project",
        script_text: "INT. HALLWAY - DAY",
        mode: "director",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
        shot_ids: [testShot.id],
      },
      shots: [
        {
          ...testShot,
          layout_refs: [
            {
              id: "lr_1",
              asset_id: "lay_1",
              job_id: "job_1",
              purpose: "entry frame",
              state_description: "The actor reaches the doorway.",
              time_hint: "entry",
              source_refs: [],
              review_status: "pending_review",
              review_feedback: "",
              selected_for_h3: false,
              created_at: "2026-08-25T10:00:00Z",
            },
          ],
        },
      ],
    });
    vi.mocked(chatWithDirectorStream).mockResolvedValue({
      reply: "A second Layout may help establish the post-entry blocking.",
      actions: [],
      project: {
        id: "prj_test",
        name: "Test project",
        script_text: "INT. HALLWAY - DAY",
        mode: "director",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
        shot_ids: [testShot.id],
      },
      shots: [testShot],
      images: [],
      thinking: "",
      steps: [],
    });
    render(<DirectorPage />);
    await screen.findByText("entry frame");

    fireEvent.click(screen.getByRole("button", { name: "Add reference frame" }));
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Show the blocking after the actor crosses the doorway." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Discuss with Director" }));

    await screen.findByText("A second Layout may help establish the post-entry blocking.");
    expect(chatWithDirectorStream).toHaveBeenCalledWith(
      "prj_test",
      expect.stringMatching(
        new RegExp(
          `(?=.*shot "Corridor walk-in" \\(${testShot.id}\\))(?=.*lr_1)(?=.*Show the blocking after the actor crosses the doorway)(?=.*do not queue)`,
          "is",
        ),
      ),
      expect.any(Array),
      expect.any(Object),
      [],
      expect.any(AbortSignal),
    );
    expect(screen.queryByLabelText("Purpose")).toBeNull();
  });

  it("shows only the current Layout in chat while keeping history in the Shot document", async () => {
    const layoutBase = {
      job_status: "succeeded" as const,
      job_error: "",
      state_description: "The same shot composition.",
      time_hint: "entry",
      source_refs: [],
      review_feedback: "",
      created_at: "2026-08-26T10:00:00Z",
    };
    vi.mocked(getProject).mockResolvedValue({
      project: {
        id: "prj_test",
        name: "Test project",
        script_text: "INT. HALLWAY - DAY",
        mode: "director",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
        shot_ids: [testShot.id],
      },
      shots: [
        {
          ...testShot,
          layout_asset_id: "lay_selected",
          refs: [
            {
              role: "layout_ref_frame",
              asset_id: "lay_selected",
              picture_index: 1,
              file_key: "layout",
            },
          ],
          layout_refs: [
            {
              ...layoutBase,
              id: "lr_rejected",
              asset_id: "lay_rejected",
              job_id: "job_rejected",
              purpose: "rejected old angle",
              review_status: "reject",
              selected_for_h3: false,
            },
            {
              ...layoutBase,
              id: "lr_superseded",
              asset_id: "lay_superseded",
              job_id: "job_superseded",
              purpose: "superseded repair",
              review_status: "usable_with_repair",
              selected_for_h3: false,
            },
            {
              ...layoutBase,
              id: "lr_selected",
              asset_id: "lay_selected",
              job_id: "job_selected",
              purpose: "selected composition",
              review_status: "usable",
              selected_for_h3: true,
            },
            {
              ...layoutBase,
              id: "lr_pending",
              asset_id: "lay_pending",
              job_id: "job_pending",
              purpose: "new angle awaiting review",
              review_status: "pending_review",
              selected_for_h3: false,
            },
          ],
        },
      ],
    });

    const { container } = render(<DirectorPage />);
    await screen.findByRole("heading", { name: "1. Corridor walk-in" });

    const chatCaptions = Array.from(
      container.querySelectorAll(".chat-images .chat-image-cap"),
      (node) => node.textContent,
    );
    expect(chatCaptions).toEqual(["Corridor walk-in · selected composition"]);
    expect(screen.getByText("rejected old angle")).toBeTruthy();
    expect(screen.getByText("superseded repair")).toBeTruthy();
    expect(screen.queryByText("Review new angle awaiting review")).toBeNull();
    expect(screen.queryByRole("button", { name: /Reject new angle/ })).toBeNull();
  });

  it("removes a chat reference card when its image payload cannot be decoded", async () => {
    const { container } = render(<DirectorPage />);
    await screen.findByRole("heading", { name: "1. Corridor walk-in" });

    const image = screen.getByAltText("Corridor walk-in · reference frame");
    fireEvent.error(image);

    expect(container.querySelector(".chat-image-btn")).toBeNull();
    expect(screen.queryByText("Corridor walk-in · reference frame")).toBeNull();
  });

  it("shows a no-preview placeholder after every Agent ref image candidate fails", async () => {
    vi.mocked(getProject).mockResolvedValue({
      project: {
        id: "prj_test",
        name: "Test project",
        script_text: "INT. HALLWAY - DAY",
        mode: "director",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
        shot_ids: [testShot.id],
      },
      shots: [
        {
          ...testShot,
          refs: [
            {
              role: "layout_ref_frame",
              asset_id: "lay_invalid",
              picture_index: 3,
              file_key: "layout",
            },
          ],
        },
      ],
    });

    render(<DirectorPage />);
    await screen.findByRole("heading", { name: "1. Corridor walk-in" });

    for (let attempt = 0; attempt < 8; attempt += 1) {
      const image = screen.queryByAltText("layout ref frame");
      if (!image) break;
      fireEvent.error(image);
    }

    expect(screen.queryByAltText("layout ref frame")).toBeNull();
    expect(screen.getByText("P3 · layout ref frame")).toBeTruthy();
    expect(screen.getByText("no preview")).toBeTruthy();
  });

  it("regenerates a reference frame directly from the shot action", async () => {
    render(<DirectorPage />);
    await screen.findByRole("heading", { name: "1. Corridor walk-in" });

    fireEvent.click(screen.getByRole("button", { name: "Generate reference frame" }));

    await waitFor(() => expect(queueRefFrame).toHaveBeenCalledWith(testShot.id));
    expect(await screen.findByText(/Queued.*reference frame/i)).toBeTruthy();
  });

  it("renders the Director interface in English", async () => {
    const { container } = render(<DirectorPage />);
    await screen.findByRole("heading", { name: "1. Corridor walk-in" });

    expect(screen.getByRole("button", { name: "Project status" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Plan shots" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Review images" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Review references" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Help" })).toBeNull();
    expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
    expect(container.textContent || "").not.toMatch(/[\u3400-\u9fff]/);
  });

  it("stops polling a terminal failed Layout without rendering a failed card", async () => {
    vi.useFakeTimers();
    const failed = {
      ...testShot,
      status: "ref_frame_pending" as const,
      layout_refs: [
        {
          id: "lr_failed",
          asset_id: null,
          job_id: "job_failed",
          job_status: "failed" as const,
          job_error: "GPU worker stopped",
          purpose: "doorway angle",
          state_description: "The worker could not render this composition.",
          time_hint: "entry",
          source_refs: [],
          review_status: null,
          review_feedback: "",
          selected_for_h3: false,
          created_at: "2026-08-26T10:00:00Z",
        },
      ],
    };
    vi.mocked(getProject).mockResolvedValueOnce({
      project: {
        id: "prj_test", name: "Test project", script_text: "INT. HALLWAY - DAY", mode: "director",
        created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", shot_ids: [testShot.id],
      },
      shots: [failed],
    });

    render(<DirectorPage />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.queryByText("GPU worker stopped")).toBeNull();
    expect(screen.queryByRole("article", { name: "doorway angle" })).toBeNull();

    await act(async () => { vi.advanceTimersByTime(2500); await Promise.resolve(); await Promise.resolve(); });
    expect(getProject).toHaveBeenCalledTimes(1);
  });

  it("keeps polling until every queued Layout sibling has an asset", async () => {
    vi.useFakeTimers();
    const initial = {
      ...testShot,
      status: "needs_review" as const,
      layout_asset_id: "lay_before",
      refs: [
        {
          role: "layout_ref_frame" as const,
          asset_id: "lay_before",
          picture_index: 1,
          file_key: "layout",
        },
      ],
      layout_refs: [
        {
          id: "lr_before",
          asset_id: "lay_before",
          job_id: "job_before",
          job_status: "succeeded" as const,
          job_error: "",
          purpose: "before entry",
          state_description: "Before the actor enters.",
          time_hint: "before entry",
          source_refs: [],
          review_status: "usable" as const,
          review_feedback: "",
          selected_for_h3: true,
          created_at: "2026-08-26T10:00:00Z",
        },
        {
          id: "lr_after",
          asset_id: null,
          job_id: "job_after",
          job_status: "running" as const,
          job_error: "",
          purpose: "after entry",
          state_description: "Awaiting the blocking frame.",
          time_hint: "after entry",
          source_refs: [],
          review_status: null,
          review_feedback: "",
          selected_for_h3: false,
          created_at: "2026-08-26T10:01:00Z",
        },
      ],
    };
    const completed = {
      ...initial,
      layout_asset_id: "lay_after",
      refs: [
        {
          role: "layout_ref_frame" as const,
          asset_id: "lay_after",
          picture_index: 1,
          file_key: "layout",
        },
      ],
      layout_refs: initial.layout_refs.map((layout) =>
        layout.id === "lr_after"
          ? { ...layout, asset_id: "lay_after", job_status: "succeeded" as const, review_status: "pending_review" as const, selected_for_h3: true }
          : { ...layout, selected_for_h3: false },
      ),
    };
    vi.mocked(getProject)
      .mockResolvedValueOnce({
        project: {
          id: "prj_test", name: "Test project", script_text: "INT. HALLWAY - DAY", mode: "director",
          created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", shot_ids: [testShot.id],
        },
        shots: [initial],
      })
      .mockResolvedValueOnce({
        project: {
          id: "prj_test", name: "Test project", script_text: "INT. HALLWAY - DAY", mode: "director",
          created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", shot_ids: [testShot.id],
        },
        shots: [completed],
      });

    render(<DirectorPage />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getAllByText("before entry").length).toBeGreaterThan(0);
    await act(async () => { vi.advanceTimersByTime(2500); await Promise.resolve(); await Promise.resolve(); });

    expect(screen.getAllByText("after entry").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Reference frame.*ready for review/i)).toHaveLength(1);
  });

  it("does not let a stale polling response overwrite a Layout added through Director chat", async () => {
    vi.useFakeTimers();
    const initial = {
      ...testShot,
      status: "needs_review" as const,
      layout_refs: [
        {
          id: "lr_review",
          asset_id: "lay_review",
          job_id: "job_review",
          job_status: "succeeded" as const,
          job_error: "",
          purpose: "before entry",
          state_description: "The actor prepares to enter.",
          time_hint: "before entry",
          source_refs: [],
          review_status: "pending_review" as const,
          review_feedback: "",
          selected_for_h3: false,
          created_at: "2026-08-26T10:00:00Z",
        },
        {
          id: "lr_still_running",
          asset_id: null,
          job_id: "job_still_running",
          job_status: "running" as const,
          job_error: "",
          purpose: "after entry",
          state_description: "A sibling Layout is still rendering.",
          time_hint: "after entry",
          source_refs: [],
          review_status: null,
          review_feedback: "",
          selected_for_h3: false,
          created_at: "2026-08-26T10:01:00Z",
        },
      ],
    };
    const detail: ProjectDetail = {
      project: {
        id: "prj_test", name: "Test project", script_text: "INT. HALLWAY - DAY", mode: "director",
        created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", shot_ids: [testShot.id],
      },
      shots: [initial],
    };
    const stalePoll = deferred<ProjectDetail>();
    const updatedShot: Shot = {
      ...initial,
      layout_refs: [
        ...initial.layout_refs,
        {
          id: "lr_dialogue_revision",
          asset_id: null,
          job_id: "job_dialogue_revision",
          job_status: "queued" as const,
          job_error: "",
          purpose: "dialogue revision",
          state_description: "The second actor has entered the corridor.",
          time_hint: "after entry",
          source_refs: [],
          review_status: null,
          review_feedback: "",
          selected_for_h3: false,
          created_at: "2026-08-26T10:02:00Z",
        },
      ],
    };
    const action = deferred<Awaited<ReturnType<typeof chatWithDirectorStream>>>();
    vi.mocked(getProject)
      .mockResolvedValueOnce(detail)
      .mockImplementationOnce(() => stalePoll.promise);
    vi.mocked(chatWithDirectorStream).mockImplementationOnce(() => action.promise);

    render(<DirectorPage />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { vi.advanceTimersByTime(2500); await Promise.resolve(); });
    expect(getProject).toHaveBeenCalledTimes(2);

    fireEvent.click(screen.getByRole("button", { name: "Add reference frame" }));
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Show the dialogue revision after the second actor enters." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Discuss with Director" }));
    await act(async () => {
      action.resolve({
        reply: "The additional Layout has been queued after our discussion.",
        actions: [],
        project: detail.project,
        shots: [updatedShot],
        images: [],
        thinking: "",
        steps: [],
      });
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText("dialogue revision")).toBeTruthy();

    await act(async () => {
      stalePoll.resolve(detail);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText("dialogue revision")).toBeTruthy();
  });

  it("shows a polling error without losing the page and clears it after the next refresh", async () => {
    vi.useFakeTimers();
    const pending = { ...testShot, status: "ref_frame_pending" as const };
    const detail: ProjectDetail = {
      project: {
        id: "prj_test", name: "Test project", script_text: "INT. HALLWAY - DAY", mode: "director",
        created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", shot_ids: [testShot.id],
      },
      shots: [pending],
    };
    vi.mocked(getProject)
      .mockResolvedValueOnce(detail)
      .mockRejectedValueOnce(new Error("refresh offline"))
      .mockResolvedValueOnce(detail);

    render(<DirectorPage />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByRole("heading", { name: "1. Corridor walk-in" })).toBeTruthy();
    await act(async () => { vi.advanceTimersByTime(2500); await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByRole("alert").textContent).toContain("Could not refresh Layout status: refresh offline");

    await act(async () => { vi.advanceTimersByTime(2500); await Promise.resolve(); await Promise.resolve(); });
    expect(screen.queryByText("Could not refresh Layout status: refresh offline")).toBeNull();
  });
});
