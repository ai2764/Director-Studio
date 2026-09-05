// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { H3WorkflowSetup } from "./H3WorkflowSetup";

vi.mock("../../shared/project/ProjectContext", () => ({
  useProject: () => ({ projectId: "project-1" }),
}));
const mapping = {
  h3_node_id: "1",
  prompt_input: "prompt",
  width_input: "width",
  height_input: "height",
  frames_input: "length",
  picture_input_pattern: "ref_images.ref_image_{index}",
  audio_input_pattern: "ref_audios.ref_audio_{index}",
  seed_node_id: "2",
  seed_input: "noise_seed",
  saver_node_id: "3",
  output_prefix_input: "filename_prefix",
  output_fields: ["videos"],
};
const active = {
  profile_id: "builtin-official-h3",
  display_name: "Built-in Official H3",
  source: "builtin",
  workflow_sha256: "official",
  contract_version: 1,
  warning: null,
};
let analysis: Record<string, unknown>;
let jobStatus: string;
let requested: { url: string; init?: RequestInit }[];
let lifecycle: {
  status: string;
  workflow_sha256: string;
  mapping_sha256: string;
  validated_at: string | null;
  test_job_id: string | null;
};
beforeEach(() => {
  localStorage.clear();
  lifecycle = {
    status: "draft",
    workflow_sha256: "hash-1",
    mapping_sha256: "mapping-1",
    validated_at: null,
    test_job_id: null,
  };
  jobStatus = "succeeded";
  requested = [];
  analysis = {
    import_id: "import-1",
    workflow_sha256: "hash-1",
    compatibility: "auto_compatible",
    mapping,
    seed_candidates: [
      { node_id: "2", class_type: "RandomNoise", title: "Noise" },
    ],
    saver_candidates: [
      { node_id: "3", class_type: "SaveVideo", title: "Final" },
    ],
    fixed_dependencies: [],
    issues: [],
    agent_manifest: {
      nodes: [
        {
          node_id: "1",
          class_type: "MiniMaxH3ReferenceToVideo",
          title: "H3",
          candidate_roles: ["h3"],
          input_names: [],
        },
      ],
    },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      requested.push({ url, init });
      let body: unknown;
      if (url === "/api/workflow-profiles/h3")
        body = { active, profiles: [{ ...active, status: "active" }] };
      else if (url.startsWith("/api/library?"))
        body = [
          {
            id: url.includes("voices") ? "voice-1" : "picture-1",
            name: url.includes("voices") ? "Narrator" : "Test portrait",
            kind: url.includes("voices") ? "voices" : "actors",
            files: { reference: "voice.wav", master: "portrait.png" },
            urls: {},
            meta: { h3_ready: true },
          },
        ];
      else if (url.endsWith("/imports"))
        body = {
          import_id: "import-1",
          workflow_sha256: "hash-1",
          filename: "test.json",
        };
      else if (url.endsWith("/analysis")) body = { ...analysis, lifecycle };
      else if (url.endsWith("/validate")) {
        lifecycle = { ...lifecycle, status: "validated", validated_at: "now" };
        body = {
          valid: true,
          workflow_sha256: "hash-1",
          import_id: "import-1",
          issues: [],
          fixed_dependencies: [],
          validated_at: "now",
        };
      } else if (url.endsWith("/test"))
        body = {
          import_id: "import-1",
          job_id: "job-1",
          job_url: "/api/h3-ref2va/jobs/job-1",
          workflow_sha256: "hash-1",
          mapping_sha256: "mapping-1",
          status: "queued",
        };
      else if (url.includes("/jobs/")) {
        if (jobStatus === "succeeded")
          lifecycle = { ...lifecycle, status: "tested", test_job_id: "job-1" };
        body = {
          id: "job-1",
          status: jobStatus,
          error: jobStatus === "failed" ? "ComfyUI unavailable" : null,
          outputs:
            jobStatus === "succeeded" ? { video: { url: "/test.mp4" } } : {},
        };
      } else if (url.endsWith("/activate"))
        body = {
          active: {
            ...active,
            source: "custom",
            display_name: "My H3 Quality Profile",
          },
          profile_id: "custom",
        };
      else if (url.endsWith("/mapping")) {
        lifecycle = {
          ...lifecycle,
          status: "mapped",
          validated_at: null,
          test_job_id: null,
        };
        analysis = { ...analysis, mapping: JSON.parse(String(init?.body)) };
        body = { import_id: "import-1", mapping: analysis.mapping };
      } else if (url.endsWith("/propose-mapping"))
        body = {
          mapping,
          explanations: ["Final saver preserves the enhanced output."],
        };
      else if (url.endsWith("/select")) body = { active };
      else throw new Error(`Unexpected request ${url}`);
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
async function importWorkflow() {
  fireEvent.change(screen.getByLabelText("Import H3 API workflow"), {
    target: {
      files: [new File(["{}"], "test.json", { type: "application/json" })],
    },
  });
  await screen.findByText("Auto-compatible");
}
async function startTest() {
  await importWorkflow();
  fireEvent.click(screen.getByRole("button", { name: "Validate workflow" }));
  await screen.findByText("Validated");
  fireEvent.change(screen.getByLabelText("Picture for test"), {
    target: { value: "picture-1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Run test" }));
}
it("imports API JSON with multipart workflow field and exposes auto-compatible bindings", async () => {
  render(<H3WorkflowSetup />);
  await importWorkflow();
  expect(screen.getByText("MiniMaxH3ReferenceToVideo")).toBeTruthy();
  expect(screen.getByText("ref_images.ref_image_{index}")).toBeTruthy();
  const request = requested.find((item) => item.url.endsWith("/imports"));
  expect((request?.init?.body as FormData).get("workflow")).toBeInstanceOf(
    File,
  );
  expect(
    (
      screen.getByRole("button", {
        name: "Activate profile",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
});
it("requires validation and one Picture, then polls a test and activates only the matching identity", async () => {
  render(<H3WorkflowSetup />);
  expect(
    (screen.getByRole("button", { name: "Run test" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  await startTest();
  await screen.findByText("Tested");
  expect(screen.getByLabelText("Workflow test video").getAttribute("src")).toBe(
    "/test.mp4",
  );
  const request = requested.find((item) => item.url.endsWith("/test"));
  expect(JSON.parse(String(request?.init?.body))).toEqual({
    picture_asset_id: "picture-1",
    audio_asset_id: null,
  });
  fireEvent.click(screen.getByRole("button", { name: "Activate profile" }));
  await screen.findByText("My H3 Quality Profile");
});
it("does not activate a successful test if the workflow changed", async () => {
  render(<H3WorkflowSetup />);
  await importWorkflow();
  fireEvent.click(screen.getByRole("button", { name: "Validate workflow" }));
  await screen.findByText("Validated");
  analysis = { ...analysis, workflow_sha256: "hash-changed" };
  fireEvent.change(screen.getByLabelText("Picture for test"), {
    target: { value: "picture-1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Run test" }));
  await screen.findByRole("alert");
  expect(
    (
      screen.getByRole("button", {
        name: "Activate profile",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
});
it("shows test failure and allows retry without enabling activation", async () => {
  jobStatus = "failed";
  render(<H3WorkflowSetup />);
  await startTest();
  await screen.findByText("ComfyUI unavailable");
  await waitFor(() =>
    expect(
      (screen.getByRole("button", { name: "Run test" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false),
  );
  expect(
    (
      screen.getByRole("button", {
        name: "Activate profile",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
});
it("shows candidate controls only for ambiguous bindings and keeps mapping advice advisory", async () => {
  analysis = {
    ...analysis,
    compatibility: "needs_confirmation",
    mapping: null,
    seed_candidates: [
      { node_id: "2", class_type: "RandomNoise", title: "Noise" },
      { node_id: "4", class_type: "RandomNoise", title: "Other noise" },
    ],
  };
  render(<H3WorkflowSetup />);
  fireEvent.change(screen.getByLabelText("Import H3 API workflow"), {
    target: { files: [new File(["{}"], "test.json")] },
  });
  await screen.findByText("Needs confirmation");
  expect(screen.getByLabelText("Seed node")).toBeTruthy();
  expect(screen.queryByRole("combobox", { name: "Output saver" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Suggest mapping" }));
  await screen.findByText("Final saver preserves the enhanced output.");
  expect(requested.some((item) => item.url.endsWith("/mapping"))).toBe(false);
  fireEvent.click(
    screen.getByRole("button", { name: "Use suggested mapping" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Save mapping" }));
  await screen.findByText("Mapped");
});

it("restores tested server evidence after a reload", async () => {
  localStorage.setItem(
    "director-studio.h3-setup",
    JSON.stringify({ importId: "import-1" }),
  );
  lifecycle = {
    ...lifecycle,
    status: "tested",
    validated_at: "now",
    test_job_id: "job-1",
  };
  render(<H3WorkflowSetup />);
  await screen.findByText("Tested");
  expect(
    (
      screen.getByRole("button", {
        name: "Activate profile",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(false);
});

it("shows structured server validation issues as actionable errors", async () => {
  const base = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (url, init) =>
    String(url).endsWith("/validate")
      ? new Response(
          JSON.stringify({
            code: "contract_validation_failed",
            message: "Workflow validation failed",
            details: { issues: [{ message: "Missing SaveVideo node" }] },
          }),
          { status: 422 },
        )
      : base(url, init),
  );
  render(<H3WorkflowSetup />);
  await importWorkflow();
  fireEvent.click(screen.getByRole("button", { name: "Validate workflow" }));
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Missing SaveVideo node",
  );
});

it("can retry loading profiles after an unavailable server", async () => {
  vi.mocked(fetch).mockRejectedValueOnce(new Error("Server offline"));
  render(<H3WorkflowSetup />);
  await screen.findByRole("alert");
  expect(screen.queryByText("Loading active workflow…")).toBeNull();
  fireEvent.click(
    screen.getByRole("button", { name: "Retry loading profiles" }),
  );
  expect(
    await screen.findByText("Built-in Official H3", { selector: "strong" }),
  ).toBeTruthy();
});

it("sends an optional selected Voice with the single Picture", async () => {
  render(<H3WorkflowSetup />);
  await importWorkflow();
  fireEvent.click(screen.getByRole("button", { name: "Validate workflow" }));
  await screen.findByText("Validated");
  fireEvent.change(screen.getByLabelText("Picture for test"), {
    target: { value: "picture-1" },
  });
  fireEvent.change(screen.getByLabelText("Voice for test (optional)"), {
    target: { value: "voice-1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Run test" }));
  await screen.findByText("Tested");
  expect(
    JSON.parse(
      String(requested.find((item) => item.url.endsWith("/test"))?.init?.body),
    ),
  ).toEqual({ picture_asset_id: "picture-1", audio_asset_id: "voice-1" });
});

it("waits for durable evidence and offers recovery if job success was not recorded", async () => {
  const base = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (url, init) => {
    const response = await base(url, init);
    if (String(url).includes("/jobs/"))
      lifecycle = { ...lifecycle, status: "validated", test_job_id: null };
    return response;
  });
  render(<H3WorkflowSetup />);
  await startTest();
  await screen.findByText("Test job: succeeded");
  expect(
    (
      screen.getByRole("button", {
        name: "Activate profile",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  vi.useFakeTimers();
  // The first pending poll uses real timers; Check status restarts it under fake time.
  cleanup();
  render(<H3WorkflowSetup />);
  for (let poll = 0; poll < 8; poll += 1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
  expect(screen.getByRole("alert").textContent).toContain(
    "Test evidence is not available",
  );
  vi.useRealTimers();
});
