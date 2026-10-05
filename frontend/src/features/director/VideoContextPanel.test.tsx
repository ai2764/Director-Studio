// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Shot, ShotVideoContext } from "../../shared/api/types";
import { fetchH3Profiles } from "../../shared/api/client";
import { getShot, getVideoJob, saveVideoContext, uploadVideoContext } from "./api";
import { continuationFocus, VideoContextPanel } from "./VideoContextPanel";

vi.mock("./api", () => ({
  getShot: vi.fn(),
  getVideoJob: vi.fn(),
  saveVideoContext: vi.fn(),
  uploadVideoContext: vi.fn(),
}));

vi.mock("../../shared/api/client", () => ({
  fetchH3Profiles: vi.fn(),
}));

const builtinProfiles = {
  active: {
    profile_id: "builtin-official-h3",
    display_name: "Built-in",
    source: "builtin" as const,
    workflow_sha256: "hash",
    contract_version: 2,
    warning: null,
  },
  profiles: [],
};

function makeShot(id: string, title: string, extra: Partial<Shot> = {}): Shot {
  return {
    id,
    title,
    project_id: "prj_1",
    scene_id: "sc_1",
    script_beat: `${title} beat`,
    duration_s: 5,
    status: "draft",
    refs: [],
    voice_refs: [],
    dialogue: [],
    prompt_sections: {
      subject_definitions: "",
      summary: "",
      retention_analysis: "",
      detailed_description: "",
      overall_soundscape: "",
      non_diegetic_music: "",
    },
    layout_asset_id: null,
    layout_review_status: null,
    ref_frame_job_id: null,
    layout_refs: [],
    h3_job_id: null,
    source_audio_path: null,
    feedback: "",
    blocked_reasons: [],
    meta: {},
    ...extra,
  };
}

function succeededJob(id: string, outputs: Record<string, { key: string; label: string; filename: string; url: string }>) {
  return { id, status: "succeeded", outputs };
}

function videoOutput(id: string, key = "video") {
  return {
    key,
    label: key,
    filename: `${id}-${key}.mp4`,
    url: `/api/files/jobs/${id}/outputs/${key}.mp4`,
  };
}

function PanelHarness({
  initial,
  shots,
  expanded = false,
}: {
  initial: Shot;
  shots: Shot[];
  expanded?: boolean;
}) {
  const [shot, setShot] = useState(initial);
  const view = shots.map((item) => (item.id === shot.id ? shot : item));
  return (
    <VideoContextPanel
      shot={shot}
      shots={view}
      expanded={expanded}
      onShotUpdated={setShot}
    />
  );
}

describe("continuationFocus", () => {
  it("selects only a shot whose saved continuation is active", () => {
    const saved = makeShot("s2", "Continue", {
      video_context: { mode: "previous_shot", source_job_id: "job_real", context_frames: 22 },
    });
    expect(continuationFocus(["configure_video_context:s2"], [saved])).toBe("s2");
    expect(continuationFocus(["configure_video_context:s2"], [makeShot("s2", "Continue")])).toBeNull();
    expect(continuationFocus(
      ["configure_video_context:s2"],
      [makeShot("s2", "Continue", { video_context: { mode: "off" } })],
    )).toBeNull();
    expect(continuationFocus(["configure_video_context:missing"], [saved])).toBeNull();
    expect(continuationFocus(["plan_shots"], [saved])).toBeNull();
  });
});

describe("VideoContextPanel", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchH3Profiles).mockResolvedValue(builtinProfiles);
    vi.mocked(getVideoJob).mockImplementation(async (id: string) => succeededJob(id, {
      video: videoOutput(id),
    }));
  });

  it("shows an old shot as off", () => {
    render(
      <VideoContextPanel
        shot={makeShot("s1", "Arrival")}
        shots={[makeShot("s1", "Arrival")]}
        onShotUpdated={vi.fn()}
      />,
    );

    const panel = screen.getByRole("region", { name: "Video continuation" });
    expect(panel.textContent).toContain("Off");
    expect(panel.textContent).not.toContain("Configured");
    expect(screen.queryByRole("button", { name: /generate|submit/i })).toBeNull();
  });

  it.each(["job_real", null])("shows the builtin window and preview for source %s", async (sourceJobId) => {
    const previous = makeShot("s1", "Arrival", { h3_job_id: "job_real" });
    const current = makeShot("s2", "Continue", {
      video_context: {
        mode: "previous_shot",
        source_shot_id: "s1",
        source_job_id: sourceJobId,
        source_output_key: "video",
        context_frames: 22,
        carry_audio: false,
      },
    });
    render(
      <VideoContextPanel
        shot={current}
        shots={[previous, current]}
        expanded
        onShotUpdated={vi.fn()}
      />,
    );

    expect(screen.getByRole("region", { name: "Video continuation" }).textContent).toContain("Configured");
    expect(screen.getByLabelText("Resolved job").textContent).toBe("job_real");
    expect(screen.getByText("Arrival")).toBeTruthy();
    expect(await screen.findByText("22 frames · 0.92 s")).toBeTruthy();
    const window = screen.getByLabelText("Context window") as HTMLSelectElement;
    expect(window.value).toBe("22");
    expect(Array.from(window.options).map((option) => option.value)).toEqual(["5", "22", "39", "56"]);
    await waitFor(() => {
      expect(document.querySelector("video")?.getAttribute("src")).toBe(
        "/api/files/jobs/job_real/outputs/video.mp4",
      );
    });
    const video = document.querySelector("video") as HTMLVideoElement;
    expect(video.hasAttribute("controls")).toBe(true);
    expect(video.hasAttribute("playsinline")).toBe(true);
    expect(video.getAttribute("preload")).toBe("metadata");
    expect(video.hasAttribute("autoplay")).toBe(false);
    expect(video.paused).toBe(true);
    const settings = screen.getByText("Continuation settings").closest("details") as HTMLDetailsElement;
    expect(settings.open).toBe(true);
    expect(screen.queryByRole("button", { name: /generate|submit/i })).toBeNull();
  });

  it("keeps a custom workflow window read-only", async () => {
    vi.mocked(fetchH3Profiles).mockResolvedValue({
      ...builtinProfiles,
      active: { ...builtinProfiles.active, source: "custom", profile_id: "custom-h3" },
    });
    const previous = makeShot("s1", "Arrival", { h3_job_id: "job_real" });
    const current = makeShot("s2", "Continue", {
      video_context: {
        mode: "previous_shot",
        source_job_id: "job_real",
        source_output_key: "video",
        context_frames: 22,
      },
    });
    render(
      <VideoContextPanel shot={current} shots={[previous, current]} expanded onShotUpdated={vi.fn()} />,
    );

    const window = await screen.findByLabelText("Context window");
    expect(window.tagName).toBe("INPUT");
    expect((window as HTMLInputElement).readOnly).toBe(true);
    expect((window as HTMLInputElement).value).toBe("工作流配置");
    expect(screen.queryByLabelText("Carry source audio")).toBeNull();
  });

  it("saves a chosen previous video version from the backend response", async () => {
    const previous = makeShot("s1", "Arrival", {
      h3_job_id: "job_new",
      meta: { superseded_h3_job_ids: ["job_old"] },
    });
    const initialContext: ShotVideoContext = {
      mode: "previous_shot",
      source_shot_id: "s1",
      source_job_id: "job_new",
      source_output_key: "video",
      context_frames: 22,
      carry_audio: false,
    };
    const current = makeShot("s2", "Continue", { video_context: initialContext });
    vi.mocked(saveVideoContext).mockResolvedValue({
      shot_id: "s2",
      video_context: { ...initialContext, source_job_id: "job_old" },
    });
    vi.mocked(getShot).mockResolvedValue({
      ...current,
      video_context: { ...initialContext, source_job_id: "job_old" },
    });

    render(<PanelHarness initial={current} shots={[previous, current]} expanded />);

    await screen.findByRole("option", { name: "job_old · video" });
    fireEvent.change(screen.getByLabelText("Previous video version"), {
      target: { value: "job_old:video" },
    });

    await waitFor(() => expect(saveVideoContext).toHaveBeenCalledWith("s2", {
      mode: "previous_shot",
      source_job_id: "job_old",
      source_output_key: "video",
      context_frames: 22,
      carry_audio: false,
    }));
    await waitFor(() => expect(screen.getByLabelText("Resolved job").textContent).toBe("job_old"));
  });

  it("turns continuation off from the saved shot", async () => {
    const previous = makeShot("s1", "Arrival", { h3_job_id: "job_real" });
    const current = makeShot("s2", "Continue", {
      video_context: {
        mode: "previous_shot",
        source_job_id: "job_real",
        source_output_key: "video",
        context_frames: 22,
      },
    });
    vi.mocked(saveVideoContext).mockResolvedValue({ shot_id: "s2", video_context: { mode: "off" } });
    vi.mocked(getShot).mockResolvedValue({ ...current, video_context: { mode: "off" } });

    render(<PanelHarness initial={current} shots={[previous, current]} expanded />);
    fireEvent.click(screen.getByRole("button", { name: "Turn continuation off" }));

    await waitFor(() => expect(saveVideoContext).toHaveBeenCalledWith("s2", { mode: "off" }));
    await waitFor(() => {
      const panel = screen.getByRole("region", { name: "Video continuation" });
      expect(panel.textContent).toContain("Off");
      expect(panel.textContent).not.toContain("Configured");
    });
  });

  it.each([false, true])("persists external audio when its prior value is %s", async (carryAudio) => {
    const context: ShotVideoContext = { mode: "external_upload", upload_id: "vup_saved", context_frames: 22, carry_audio: carryAudio };
    const current = makeShot("s1", "External", { video_context: context });
    vi.mocked(saveVideoContext).mockResolvedValue({shot_id:current.id,video_context:{...context,carry_audio:!carryAudio}});
    vi.mocked(getShot).mockResolvedValue({...current,video_context:{...context,carry_audio:!carryAudio}});
    render(<PanelHarness initial={current} shots={[current]} expanded />);
    fireEvent.click(await screen.findByLabelText("Carry source audio"));
    await waitFor(() => expect(saveVideoContext).toHaveBeenCalledWith(current.id, {
      mode:"external_upload",upload_id:"vup_saved",context_frames:22,carry_audio:!carryAudio,
    }));
    await waitFor(() => expect((screen.getByLabelText("Carry source audio") as HTMLInputElement).checked).toBe(!carryAudio));
  });

  it("uploads an external video and shows the saved upload", async () => {
    const current = makeShot("s2", "Continue");
    const file = new File(["mp4"], "take.mp4", { type: "video/mp4" });
    vi.mocked(uploadVideoContext).mockResolvedValue({
      upload_id: "vup_1",
      filename: "vup_1.mp4",
      sha256: "abc",
      media: { width: 864, height: 480, fps: 24, duration_s: 2, has_audio: false, has_video: true },
      size_bytes: 3,
    });
    vi.mocked(saveVideoContext).mockResolvedValue({
      shot_id: "s2",
      video_context: { mode: "external_upload", upload_id: "vup_1" },
    });
    vi.mocked(getShot).mockResolvedValue({
      ...current,
      video_context: { mode: "external_upload", upload_id: "vup_1", context_frames: 22, carry_audio: false },
    });

    render(<PanelHarness initial={current} shots={[makeShot("s1", "Arrival"), current]} expanded />);
    fireEvent.change(screen.getByLabelText("Upload continuation video"), {
      target: { files: [file] },
    });

    await waitFor(() => expect(uploadVideoContext).toHaveBeenCalledWith("prj_1", file));
    await waitFor(() => expect(saveVideoContext).toHaveBeenCalledWith("s2", expect.objectContaining({
      mode: "external_upload",
      upload_id: "vup_1",
    })));
    await waitFor(() => expect(screen.getByLabelText("Resolved upload").textContent).toBe("vup_1"));
    const video = document.querySelector("video") as HTMLVideoElement;
    expect(video.getAttribute("src")).toBe("/api/files/projects/prj_1/video_context_uploads/vup_1.mp4");
    expect(screen.getByRole("region", { name: "Video continuation" }).textContent).toContain("Configured");
  });

  it("keeps the shot off when saving a version fails", async () => {
    const previous = makeShot("s1", "Arrival", { h3_job_id: "job_real" });
    const current = makeShot("s2", "Continue");
    vi.mocked(saveVideoContext).mockRejectedValue(new Error("Previous shot job is running"));

    render(<PanelHarness initial={current} shots={[previous, current]} expanded />);
    await screen.findByRole("option", { name: "job_real · video" });
    fireEvent.change(screen.getByLabelText("Previous video version"), {
      target: { value: "job_real:video" },
    });

    expect(await screen.findByText("Previous shot job is running")).toBeTruthy();
    expect(screen.getByRole("region", { name: "Video continuation" }).textContent).not.toContain("Configured");
    expect(getShot).not.toHaveBeenCalled();
  });

  it("asks for one artifact when the previous job has several videos", async () => {
    vi.mocked(getVideoJob).mockResolvedValue(succeededJob("job_real", {
      video: videoOutput("job_real", "video"),
      video_raw: videoOutput("job_real", "video_raw"),
    }));
    const previous = makeShot("s1", "Arrival", { h3_job_id: "job_real" });
    const current = makeShot("s2", "Continue", {
      video_context: { mode: "previous_shot", source_job_id: "job_real" },
    });
    render(
      <VideoContextPanel shot={current} shots={[previous, current]} expanded onShotUpdated={vi.fn()} />,
    );

    expect(await screen.findByText("Select one video artifact from the previous shot")).toBeTruthy();
  });
});
