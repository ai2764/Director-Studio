// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { H3WorkflowSetup } from "./H3WorkflowSetup";

vi.mock("../../shared/project/ProjectContext", () => ({
  useProject: () => ({ projectId: "project-1" }),
}));

const candidate = (node_id: string, display_name: string, class_type: string) => ({
  node_id,
  display_name,
  class_type,
  title: display_name,
  object_display_name: class_type,
  terminal: class_type === "VHS_VideoCombine",
  output_node: class_type === "VHS_VideoCombine",
  output_types: class_type === "VHS_VideoCombine" ? ["VHS_FILENAMES"] : ["CONDITIONING"],
});

const mapping = {
  inputs: {
    h3_node_id: "136",
    prompt_input: "prompt",
    width_input: "width",
    height_input: "height",
    frames_input: "length",
    picture_input_pattern: "ref_images.ref_image_{index}",
    audio_input_pattern: "ref_audios.ref_audio_{index}",
    seed_node_id: "129",
    seed_input: "noise_seed",
  },
  output: { node_id: "214", artifact_index: null },
};

const active = {
  profile_id: "builtin-official-h3",
  display_name: "Built-in Official H3",
  source: "builtin",
  workflow_sha256: "official-hash",
  contract_version: 2,
  warning: null,
};

let selectedOutput = false;
let lifecycleStatus = "draft";
let jobOutputs: Record<string, { url: string }> = {};
let requests: { url: string; init?: RequestInit }[] = [];
let contextCandidates: { node_id: string; input_name: string; display_name: string; class_type: string }[] = [];
let mappedContext: { node_id: string; input_name: string } | null = null;

function analysis() {
  return {
    import_id: "imp-1",
    workflow_sha256: "workflow-hash",
    selected_output_node_id: selectedOutput ? "214" : null,
    compatibility: selectedOutput ? "auto_compatible" : "needs_confirmation",
    mapping: selectedOutput ? { ...mapping, ...(mappedContext ? { context_video: mappedContext } : {}) } : null,
    output_candidates: [
      candidate("214", "Final Video Combine", "VHS_VideoCombine"),
      candidate("300", "Preview Video", "VHS_VideoCombine"),
    ],
    h3_candidates: selectedOutput ? [candidate("136", "Main H3 Generator", "MiniMaxH3ReferenceToVideo")] : [],
    seed_candidates: selectedOutput ? [candidate("129", "Generation Seed", "RandomNoise")] : [],
    context_video_candidates: contextCandidates,
    fixed_dependencies: [],
    issues: [],
    lifecycle: {
      status: lifecycleStatus,
      workflow_sha256: "workflow-hash",
      mapping_sha256: selectedOutput ? "mapping-hash" : null,
      validated_at: lifecycleStatus === "draft" || lifecycleStatus === "mapped" ? null : "now",
      test_job_id: lifecycleStatus === "tested" ? "job-1" : null,
    },
  };
}

beforeEach(() => {
  localStorage.clear();
  selectedOutput = false;
  lifecycleStatus = "draft";
  jobOutputs = {};
  requests = [];
  contextCandidates = [];
  mappedContext = null;
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    requests.push({ url, init });
    let body: unknown;
    if (url === "/api/workflow-profiles/h3") body = { active, profiles: [{ ...active, status: "active" }] };
    else if (url.startsWith("/api/library?")) body = url.includes("kind=voices") ? [{ id: "voice-1", name: "Mia voice", files: { reference: "voice.wav" }, meta: { h3_ready: true } }] : [{ id: "picture-1", name: "Mia portrait", files: { master: "mia.png" }, meta: {} }];
    else if (url.endsWith("/imports")) body = { import_id: "imp-1", workflow_sha256: "workflow-hash", filename: "custom.api.json" };
    else if (url.endsWith("/output")) { selectedOutput = true; body = analysis(); }
    else if (url.endsWith("/mapping")) { lifecycleStatus = "mapped"; mappedContext = JSON.parse(String(init?.body)).context_video || null; body = { import_id: "imp-1", mapping }; }
    else if (url === "/api/projects/project-1/video-context/uploads") body = { upload_id: "vup_test", filename: "source.mp4" };
    else if (url.endsWith("/validate")) { lifecycleStatus = "validated"; body = { valid: true }; }
    else if (url.endsWith("/test-output")) { lifecycleStatus = "tested"; body = { import_id: "imp-1", artifact_index: 1, job_id: "job-1", status: "succeeded" }; }
    else if (url.endsWith("/test")) body = { import_id: "imp-1", job_id: "job-1", job_url: "/api/h3-ref2va/jobs/job-1", workflow_sha256: "workflow-hash", mapping_sha256: "mapping-hash", status: "queued" };
    else if (url.endsWith("/analysis")) body = analysis();
    else if (url.includes("/jobs/job-1")) body = { id: "job-1", status: "succeeded", outputs: jobOutputs, error: null };
    else if (url.endsWith("/activate")) body = { profile_id: "custom-1", active: { ...active, profile_id: "custom-1", display_name: "My H3 Workflow", source: "custom" } };
    else if (url.endsWith("/select")) body = { active };
    else throw new Error(`Unexpected request ${url}`);
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("uses workflow language and does not expose agent setup controls", async () => {
  render(<H3WorkflowSetup />);
  expect(await screen.findByRole("complementary", { name: "Current Workflow" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Custom H3 Workflows" })).toBeTruthy();
  expect(screen.queryByText(/profile/i)).toBeNull();
  expect(screen.queryByRole("button", { name: /suggest/i })).toBeNull();
});

it("selects output first and shows node names before IDs", async () => {
  render(<H3WorkflowSetup />);
  const file = new File(["{}"], "custom.api.json", { type: "application/json" });
  fireEvent.change(await screen.findByLabelText("Import Workflow"), { target: { files: [file] } });
  const output = await screen.findByLabelText("Final video node");
  expect(screen.getByRole("option", { name: "Final Video Combine — VHS_VideoCombine (Node 214)" })).toBeTruthy();
  fireEvent.change(output, { target: { value: "214" } });
  expect(await screen.findByRole("option", { name: "Main H3 Generator — MiniMaxH3ReferenceToVideo (Node 136)" })).toBeTruthy();
  expect(screen.getByRole("option", { name: "Generation Seed — RandomNoise (Node 129)" })).toBeTruthy();
  expect(requests.find((request) => request.url.endsWith("/output"))?.init?.body).toBe(JSON.stringify({ node_id: "214" }));
});

it("sends the nested confirmed boundary without an agent proposal", async () => {
  selectedOutput = true;
  lifecycleStatus = "mapped";
  localStorage.setItem("director-studio.h3-setup", JSON.stringify({ importId: "imp-1" }));
  render(<H3WorkflowSetup />);
  fireEvent.click(await screen.findByRole("button", { name: "Confirm input nodes" }));
  await waitFor(() => expect(requests.some((request) => request.url.endsWith("/mapping"))).toBe(true));
  const request = requests.find((item) => item.url.endsWith("/mapping"));
  expect(JSON.parse(String(request?.init?.body))).toEqual(mapping);
});

it.each(["50", "group:50"])("sends the selected context video file mapping for node %s", async (nodeId) => {
  selectedOutput = true;
  lifecycleStatus = "mapped";
  contextCandidates = [{
    node_id: nodeId,
    input_name: "file",
    display_name: "Previous clip",
    class_type: "LoadVideo",
  }];
  localStorage.setItem("director-studio.h3-setup", JSON.stringify({ importId: "imp-1" }));
  render(<H3WorkflowSetup />);
  fireEvent.change(await screen.findByLabelText("Context video file"), { target: { value: `${nodeId}:file` } });
  fireEvent.click(screen.getByRole("button", { name: "Confirm input nodes" }));
  await waitFor(() => expect(requests.some((request) => request.url.endsWith("/mapping"))).toBe(true));
  const request = requests.find((item) => item.url.endsWith("/mapping"));
  expect(JSON.parse(String(request?.init?.body)).context_video).toEqual({
    node_id: nodeId,
    input_name: "file",
  });
});

it("keeps the discovered video mapping and uploads a file for the setup test", async () => {
  selectedOutput = true;
  lifecycleStatus = "validated";
  mappedContext = { node_id: "50", input_name: "file" };
  contextCandidates = [{ ...mappedContext, display_name: "Previous shot", class_type: "LoadVideo" }];
  localStorage.setItem("director-studio.h3-setup", JSON.stringify({ importId: "imp-1" }));
  render(<H3WorkflowSetup />);
  expect((await screen.findByLabelText("Context video file") as HTMLSelectElement).value).toBe("50:file");
  fireEvent.change(await screen.findByLabelText("Picture for test"), { target: { value: "picture-1" } });
  expect((screen.getByRole("button", { name: "Run 56-frame test" }) as HTMLButtonElement).disabled).toBe(true);
  const file = new File(["video-bytes"], "source.mp4", { type: "video/mp4" });
  fireEvent.change(screen.getByLabelText("Video for test"), { target: { files: [file] } });
  expect(await screen.findByText("source.mp4")).toBeTruthy();
  await waitFor(() => expect((screen.getByRole("button", { name: "Run 56-frame test" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Run 56-frame test" }));
  await waitFor(() => expect(requests.some((request) => request.url.endsWith("/test"))).toBe(true));
  expect(JSON.parse(String(requests.find((request) => request.url.endsWith("/test"))?.init?.body))).toMatchObject({
    context_project_id: "project-1", context_upload_id: "vup_test",
  });
});

it("previews all observed output videos and selects one without another test run", async () => {
  selectedOutput = true;
  lifecycleStatus = "validated";
  jobOutputs = {
    video_candidate_0: { url: "/first.mp4" },
    video_candidate_1: { url: "/second.mp4" },
  };
  localStorage.setItem("director-studio.h3-setup", JSON.stringify({ importId: "imp-1" }));
  render(<H3WorkflowSetup />);
  fireEvent.change(await screen.findByLabelText("Picture for test"), { target: { value: "picture-1" } });
  fireEvent.click(screen.getByRole("button", { name: "Run 56-frame test" }));
  expect(await screen.findByLabelText("Workflow test video 1")).toBeTruthy();
  expect(screen.getByLabelText("Workflow test video 2")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Use video 2" }));
  await waitFor(() => expect(requests.some((request) => request.url.endsWith("/test-output"))).toBe(true));
  expect(requests.filter((request) => request.url.endsWith("/test"))).toHaveLength(1);
  expect(requests.find((request) => request.url.endsWith("/test-output"))?.init?.body).toBe(JSON.stringify({ artifact_index: 1 }));
});
