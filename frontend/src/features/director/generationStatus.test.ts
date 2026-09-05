import { describe, expect, it } from "vitest";
import {
  formatGenerationElapsed,
  generationStatusText,
  type DirectorVramStatus,
} from "./generationStatus";

const status: DirectorVramStatus = {
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
};

describe("generation status formatting", () => {
  it("formats stage, elapsed time, and waiting count", () => {
    expect(
      generationStatusText(status, new Date("2026-08-31T10:02:37Z")),
    ).toBe("Generating video · 02:37 · 2 jobs waiting");
  });

  it("formats every runtime phase", () => {
    const labels = (["queued", "uploading", "saving"] as const).map((phase) =>
      generationStatusText(
        {
          ...status,
          generation_count: 1,
          generation_jobs: [{ ...status.generation_jobs[1], phase }],
        },
        new Date("2026-08-31T10:02:00Z"),
      ),
    );
    expect(labels).toEqual([
      "Queued · 01:00",
      "Uploading assets · 01:00",
      "Saving result · 01:00",
    ]);
  });

  it("labels image generation in English", () => {
    expect(
      generationStatusText(
        {
          ...status,
          generation_count: 1,
          generation_jobs: [{ ...status.generation_jobs[1], status: "running", phase: "generating" }],
        },
        new Date("2026-08-31T10:02:00Z"),
      ),
    ).toBe("Generating image · 01:00");
  });

  it("uses hours after sixty minutes and clamps future timestamps", () => {
    expect(
      formatGenerationElapsed(
        "2026-08-31T10:00:00Z",
        new Date("2026-08-31T11:02:03Z"),
      ),
    ).toBe("1:02:03");
    expect(
      formatGenerationElapsed(
        "2026-08-31T12:00:00Z",
        new Date("2026-08-31T11:02:03Z"),
      ),
    ).toBe("00:00");
  });
});
