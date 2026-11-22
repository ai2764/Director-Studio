import { useEffect, useRef, useState } from "react";
import {
  activateH3Import,
  fetchH3ImportAnalysis,
  fetchH3Profiles,
  importH3Workflow,
  saveH3Mapping,
  selectH3ImportOutput,
  selectH3Profile,
  selectH3TestOutput,
  testH3Import,
  validateH3Import,
} from "../../shared/api/client";
import type { H3Profiles } from "../../shared/api/types";
import { useProject } from "../../shared/project/ProjectContext";
import { Badge, Banner, Button, Select, StatusDot } from "../../shared/ui";
import { listLibraryAssets, type LibraryAsset, type LibraryKind } from "../library/api";
import { getH3Job, type H3JobRecord } from "../production/api";
import { uploadVideoContext } from "../director/api";
import type {
  H3Analysis,
  H3Candidate,
  H3ContextVideoInput,
  H3LifecycleStatus,
  H3Mapping,
  H3TestRun,
} from "./types";

const STORAGE_KEY = "director-studio.h3-setup";
const PICTURE_KINDS: LibraryKind[] = ["actors", "costumes", "scenes", "props", "layouts"];
const STATUS_LABELS: Record<H3LifecycleStatus, string> = {
  draft: "Choose output",
  mapped: "Inputs confirmed",
  validated: "Validated",
  tested: "Tested",
  active: "Active",
};
const STATUS_TONES: Record<H3LifecycleStatus, "neutral" | "info" | "warn" | "ok" | "accent"> = {
  draft: "neutral",
  mapped: "info",
  validated: "accent",
  tested: "ok",
  active: "ok",
};

type Operation =
  | "idle"
  | "loading"
  | "importing"
  | "uploading"
  | "saving"
  | "validating"
  | "testing"
  | "activating"
  | "selecting";

function remember(importId: string, test?: H3TestRun) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ importId, test }));
  } catch {
    // Runtime setup still works when browser storage is unavailable.
  }
}

function remembered(): { importId?: string; test?: H3TestRun } {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

function nodeLabel(candidate: H3Candidate): string {
  return `${candidate.display_name} — ${candidate.class_type} (Node ${candidate.node_id})`;
}

function mappingFor(
  analysis: H3Analysis,
  h3NodeId: string,
  seedNodeId: string,
  contextVideo?: H3ContextVideoInput | null,
): H3Mapping | null {
  const outputNodeId = analysis.selected_output_node_id || analysis.mapping?.output.node_id;
  if (!outputNodeId || !h3NodeId) return null;
  const mapping: H3Mapping = {
    inputs: {
      h3_node_id: h3NodeId,
      prompt_input: "prompt",
      width_input: "width",
      height_input: "height",
      frames_input: "length",
      picture_input_pattern: "ref_images.ref_image_{index}",
      audio_input_pattern: "ref_audios.ref_audio_{index}",
      seed_node_id: seedNodeId || null,
      seed_input: seedNodeId ? "noise_seed" : null,
    },
    output: { node_id: outputNodeId, artifact_index: null },
  };
  if (contextVideo) mapping.context_video = contextVideo;
  return mapping;
}

export function H3WorkflowSetup({ active = true }: { active?: boolean }) {
  const { projectId } = useProject();
  const [profiles, setProfiles] = useState<H3Profiles | null>(null);
  const [selectedWorkflow, setSelectedWorkflow] = useState("");
  const [analysis, setAnalysis] = useState<H3Analysis | null>(null);
  const [mapping, setMapping] = useState<H3Mapping | null>(null);
  const [stage, setStage] = useState<H3LifecycleStatus>("draft");
  const [operation, setOperation] = useState<Operation>("loading");
  const [error, setError] = useState<string | null>(null);
  const [pictures, setPictures] = useState<LibraryAsset[]>([]);
  const [voices, setVoices] = useState<LibraryAsset[]>([]);
  const [picture, setPicture] = useState("");
  const [voice, setVoice] = useState("");
  const [contextUploadId, setContextUploadId] = useState("");
  const [contextFilename, setContextFilename] = useState("");
  const [test, setTest] = useState<H3TestRun | null>(null);
  const [job, setJob] = useState<H3JobRecord | null>(null);
  const [assetRefresh, setAssetRefresh] = useState(0);
  const [pollVersion, setPollVersion] = useState(0);
  const errorRef = useRef<HTMLDivElement>(null);
  const projectIdRef = useRef(projectId);
  projectIdRef.current = projectId;
  const busy = operation !== "idle";

  const applyAnalysis = (next: H3Analysis) => {
    setAnalysis(next);
    setMapping(next.mapping);
    setStage(next.lifecycle.status);
  };

  async function perform(name: Operation, action: () => Promise<void>) {
    setOperation(name);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setOperation("idle");
    }
  }

  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);

  useEffect(() => {
    setContextUploadId("");
    setContextFilename("");
  }, [projectId]);

  useEffect(() => {
    let cancelled = false;
    const saved = remembered();
    void (async () => {
      try {
        const current = await fetchH3Profiles();
        if (cancelled) return;
        setProfiles(current);
        setSelectedWorkflow(current.active.profile_id);
        if (saved.importId) {
          const next = await fetchH3ImportAnalysis(saved.importId);
          if (cancelled) return;
          applyAnalysis(next);
          if (saved.test && saved.test.import_id === saved.importId) setTest(saved.test);
          else if (next.lifecycle.test_job_id) setJob(await getH3Job(next.lifecycle.test_job_id));
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setOperation("idle");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (!active || !projectId) return;
    Promise.all(
      [...PICTURE_KINDS, "voices" as LibraryKind].map((kind) =>
        listLibraryAssets(kind, projectId, true),
      ),
    )
      .then((groups) => {
        if (cancelled) return;
        setPictures([
          ...new Map(
            groups
              .slice(0, 5)
              .flat()
              .filter((asset) =>
                Object.values(asset.files).some(
                  (file) => file && /\.(png|jpe?g|webp)$/i.test(file),
                ),
              )
              .map((asset) => [asset.id, asset]),
          ).values(),
        ]);
        setVoices(groups[5].filter((asset) => asset.meta?.h3_ready && asset.files.reference));
      })
      .catch((err) => {
        if (!cancelled)
          setError(`Could not load test assets: ${err instanceof Error ? err.message : String(err)}`);
      });
    return () => {
      cancelled = true;
    };
  }, [active, projectId, assetRefresh]);

  useEffect(() => {
    if (!test) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    let evidenceAttempts = 0;
    const poll = async () => {
      try {
        const nextJob = await getH3Job(test.job_id);
        if (cancelled) return;
        setJob(nextJob);
        if (["queued", "uploading", "running"].includes(nextJob.status)) {
          setOperation("testing");
          timer = setTimeout(poll, 2000);
          return;
        }
        if (nextJob.status !== "succeeded") throw new Error(nextJob.error || `Test ${nextJob.status}.`);
        const candidates = Object.keys(nextJob.outputs).filter((key) =>
          key.startsWith("video_candidate_"),
        );
        if (candidates.length > 1) {
          setOperation("idle");
          return;
        }
        const current = await fetchH3ImportAnalysis(test.import_id);
        if (current.lifecycle.status !== "tested") {
          evidenceAttempts += 1;
          if (evidenceAttempts >= 5)
            throw new Error("Test completed, but its result is not available yet.");
          timer = setTimeout(poll, 1000);
          return;
        }
        applyAnalysis(current);
        remember(current.import_id);
        setTest(null);
        setOperation("idle");
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
          setOperation("idle");
        }
      }
    };
    void poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [test, pollVersion]);

  const outputCandidates = analysis?.output_candidates || [];
  const selectedOutput = analysis?.selected_output_node_id || mapping?.output.node_id || "";
  const h3Candidates = analysis?.h3_candidates || [];
  const seedCandidates = analysis?.seed_candidates || [];
  const contextCandidates = analysis?.context_video_candidates || [];
  const testCandidates = job
    ? Object.entries(job.outputs)
        .filter(([key, slot]) => key.startsWith("video_candidate_") && slot.url)
        .sort(([left], [right]) => left.localeCompare(right, undefined, { numeric: true }))
    : [];
  const canValidate = Boolean(mapping && analysis && stage === "mapped" && !busy);
  const contextReady = !mapping?.context_video || Boolean(projectId && contextUploadId.trim());
  const canTest = Boolean(
    mapping && picture && contextReady && ["validated", "tested"].includes(stage) && !busy,
  );
  const canActivate = Boolean(analysis && stage === "tested" && !busy);

  return (
    <div
      className="h3-workflow-setup grid grid-cols-1 gap-5 lg:grid-cols-[300px_minmax(0,1fr)] lg:items-start"
      aria-busy={busy}
    >
      <aside
        className="workflow-profile-rail section-card flex flex-col gap-3.5 rounded-lg border border-border-subtle bg-panel p-4 shadow-[var(--shadow-card)] lg:sticky lg:top-4"
        aria-labelledby="active-workflow-title"
      >
        <div className="flex items-center justify-between gap-2 border-b border-border-subtle pb-2.5">
          <h2
            id="active-workflow-title"
            className="section-card-title text-[13px] font-semibold tracking-[-0.01em] text-ink"
          >
            Current Workflow
          </h2>
          <StatusDot tone={profiles ? "ok" : "neutral"} />
        </div>

        {profiles ? (
          <>
            <div className="rounded-md border border-border-subtle bg-elevated/50 px-3 py-2.5">
              <strong className="workflow-profile-name block text-[13px] font-semibold text-ink">
                {profiles.active.display_name}
              </strong>
              <p className="field-hint mt-0.5 text-[11px] leading-relaxed text-ink-muted">
                Local H3 jobs use this ComfyUI workflow.
              </p>
            </div>

            <label className="field flex flex-col gap-1.5">
              <span className="text-xs font-medium text-ink-secondary">Installed workflow</span>
              <Select
                value={selectedWorkflow}
                disabled={busy}
                onChange={(event) => setSelectedWorkflow(event.target.value)}
              >
                {profiles.profiles.map((item) => (
                  <option key={item.profile_id} value={item.profile_id}>
                    {item.display_name}
                  </option>
                ))}
              </Select>
            </label>

            <Button
              type="button"
              variant="secondary"
              disabled={busy || selectedWorkflow === profiles.active.profile_id}
              onClick={() =>
                void perform("selecting", async () => {
                  const result = await selectH3Profile(selectedWorkflow);
                  setProfiles({ ...profiles, active: result.active });
                })
              }
            >
              Use Workflow
            </Button>

            <details className="rounded-md border border-border-subtle bg-elevated/30 px-3 py-2 text-xs text-ink-secondary">
              <summary className="cursor-pointer font-medium text-ink select-none">
                Workflow identity
              </summary>
              <code className="workflow-hash mt-2 block break-all font-mono text-[11px] text-ink-muted">
                {profiles.active.workflow_sha256}
              </code>
            </details>

            {profiles.active.warning ? (
              <Banner tone="warn" role="status">
                {profiles.active.warning.message}
              </Banner>
            ) : null}
          </>
        ) : (
          <p className="field-hint text-xs text-ink-muted">Loading active workflow…</p>
        )}
      </aside>

      <div className="workflow-setup-main flex min-w-0 flex-col gap-4">
        {error ? (
          <div role="alert" tabIndex={-1} ref={errorRef} className="focus:outline-none">
            <Banner tone="danger" role="presentation">
              {error}
            </Banner>
          </div>
        ) : null}

        <section
          className="section-card flex flex-col gap-3 rounded-lg border border-border-subtle bg-panel p-4 shadow-[var(--shadow-card)]"
          aria-labelledby="custom-h3-title"
        >
          <div className="border-b border-border-subtle pb-2.5">
            <h2
              id="custom-h3-title"
              className="section-card-title text-[13px] font-semibold tracking-[-0.01em] text-ink"
            >
              Custom H3 Workflows
            </h2>
            <p className="field-hint mt-0.5 text-xs leading-relaxed text-ink-secondary">
              Import a ComfyUI API JSON. Director Studio leaves the internal graph unchanged and
              only connects its input and final-video boundaries.
            </p>
          </div>

          <label className="field flex flex-col gap-1.5">
            <span className="text-xs font-medium text-ink-secondary">Import Workflow</span>
            <input
              type="file"
              accept=".json,application/json"
              disabled={busy}
              className="block w-full cursor-pointer rounded-md border border-border-subtle bg-elevated/40 px-2.5 py-1.5 text-xs text-ink file:mr-3 file:cursor-pointer file:rounded file:border-0 file:bg-panel file:px-2.5 file:py-1 file:text-xs file:font-medium file:text-ink hover:border-border-strong"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file)
                  void perform("importing", async () => {
                    setAnalysis(null);
                    setMapping(null);
                    setJob(null);
                    setTest(null);
                    setStage("draft");
                    const imported = await importH3Workflow(file);
                    remember(imported.import_id);
                    applyAnalysis(await fetchH3ImportAnalysis(imported.import_id));
                  });
              }}
            />
          </label>

          {analysis ? (
            <div className="flex flex-col gap-2">
              {analysis.issues.map((issue, index) => (
                <p
                  className="field-hint rounded-md border border-border-subtle bg-elevated/40 px-3 py-2 text-xs text-ink-secondary"
                  key={`${issue.code}-${index}`}
                >
                  {issue.message}
                </p>
              ))}
              {analysis.fixed_dependencies.length ? (
                <details className="rounded-md border border-border-subtle bg-elevated/30 px-3 py-2 text-xs text-ink-secondary">
                  <summary className="cursor-pointer font-medium text-ink select-none">
                    Workflow-owned files
                  </summary>
                  <div className="mt-2 flex flex-col gap-1 font-mono text-[11px] text-ink-muted">
                    {analysis.fixed_dependencies.map((item) => (
                      <p key={`${item.node_id}-${item.input_name}`}>{item.value}</p>
                    ))}
                  </div>
                </details>
              ) : null}
            </div>
          ) : (
            <p className="empty-copy text-xs text-ink-muted">
              Choose the API-format JSON exported by ComfyUI.
            </p>
          )}
        </section>

        <section
          className="section-card flex flex-col gap-3 rounded-lg border border-border-subtle bg-panel p-4 shadow-[var(--shadow-card)]"
          aria-labelledby="output-title"
        >
          <div className="border-b border-border-subtle pb-2.5">
            <h2
              id="output-title"
              className="section-card-title text-[13px] font-semibold tracking-[-0.01em] text-ink"
            >
              1. Final video output
            </h2>
            <p className="field-hint mt-0.5 text-xs leading-relaxed text-ink-secondary">
              Choose the terminal node whose video Director Studio should keep. Node name is shown
              first; ID is secondary.
            </p>
          </div>

          {analysis ? (
            <label className="field flex flex-col gap-1.5">
              <span className="text-xs font-medium text-ink-secondary">Final video node</span>
              <Select
                aria-label="Final video node"
                value={selectedOutput}
                disabled={busy}
                onChange={(event) =>
                  void perform("selecting", async () => {
                    const next = await selectH3ImportOutput(analysis.import_id, event.target.value);
                    applyAnalysis(next);
                  })
                }
              >
                <option value="">Choose a final video node…</option>
                {outputCandidates.map((candidate) => (
                  <option key={candidate.node_id} value={candidate.node_id}>
                    {nodeLabel(candidate)}
                  </option>
                ))}
              </Select>
            </label>
          ) : null}
        </section>

        <section
          className="section-card flex flex-col gap-3 rounded-lg border border-border-subtle bg-panel p-4 shadow-[var(--shadow-card)]"
          aria-labelledby="inputs-title"
        >
          <div className="border-b border-border-subtle pb-2.5">
            <h2
              id="inputs-title"
              className="section-card-title text-[13px] font-semibold tracking-[-0.01em] text-ink"
            >
              2. H3 Inputs
            </h2>
            <p className="field-hint mt-0.5 text-xs leading-relaxed text-ink-secondary">
              After output selection, H3, seed and video file inputs are discovered upstream. A
              single video input is proposed automatically; confirm it below.
            </p>
          </div>

          {analysis && selectedOutput ? (
            <div className="flex flex-col gap-3">
              <label className="field flex flex-col gap-1.5">
                <span className="text-xs font-medium text-ink-secondary">H3 generation node</span>
                <Select
                  aria-label="H3 generation node"
                  value={mapping?.inputs.h3_node_id || ""}
                  disabled={busy}
                  onChange={(event) => {
                    const next = mappingFor(
                      analysis,
                      event.target.value,
                      mapping?.inputs.seed_node_id || "",
                      mapping?.context_video,
                    );
                    setMapping(next);
                    setStage("mapped");
                  }}
                >
                  <option value="">Choose the H3 node…</option>
                  {h3Candidates.map((candidate) => (
                    <option key={candidate.node_id} value={candidate.node_id}>
                      {nodeLabel(candidate)}
                    </option>
                  ))}
                </Select>
              </label>

              <label className="field flex flex-col gap-1.5">
                <span className="text-xs font-medium text-ink-secondary">Seed node (optional)</span>
                <Select
                  aria-label="Seed node (optional)"
                  value={mapping?.inputs.seed_node_id || ""}
                  disabled={busy || !mapping}
                  onChange={(event) => {
                    if (!mapping) return;
                    setMapping({
                      ...mapping,
                      inputs: {
                        ...mapping.inputs,
                        seed_node_id: event.target.value || null,
                        seed_input: event.target.value ? "noise_seed" : null,
                      },
                    });
                    setStage("mapped");
                  }}
                >
                  <option value="">Use workflow seed settings</option>
                  {seedCandidates.map((candidate) => (
                    <option key={candidate.node_id} value={candidate.node_id}>
                      {nodeLabel(candidate)}
                    </option>
                  ))}
                </Select>
              </label>

              {contextCandidates.length ? (
                <label className="field flex flex-col gap-1.5">
                  <span className="text-xs font-medium text-ink-secondary">
                    Context video file (optional)
                  </span>
                  <Select
                    aria-label="Context video file"
                    value={
                      mapping?.context_video
                        ? `${mapping.context_video.node_id}:${mapping.context_video.input_name}`
                        : ""
                    }
                    disabled={busy || !mapping}
                    onChange={(event) => {
                      if (!mapping) return;
                      const selected = contextCandidates.find(
                        (candidate) =>
                          `${candidate.node_id}:${candidate.input_name}` === event.target.value,
                      );
                      const contextVideo = selected
                        ? { node_id: selected.node_id, input_name: selected.input_name }
                        : null;
                      const next = {
                        ...mapping,
                        inputs: { ...mapping.inputs },
                        output: { ...mapping.output },
                      };
                      if (contextVideo) next.context_video = contextVideo;
                      else delete next.context_video;
                      setMapping(next);
                      setStage("mapped");
                    }}
                  >
                    <option value="">No context video</option>
                    {contextCandidates.map((candidate) => (
                      <option
                        key={`${candidate.node_id}:${candidate.input_name}`}
                        value={`${candidate.node_id}:${candidate.input_name}`}
                      >
                        {candidate.display_name} — {candidate.class_type}.{candidate.input_name}
                      </option>
                    ))}
                  </Select>
                </label>
              ) : null}

              {contextCandidates.length > 1 && !mapping?.context_video ? (
                <p className="field-hint text-[11px] text-ink-muted">
                  Choose which video input receives the previous shot, or keep the workflow's video
                  files.
                </p>
              ) : null}
              {!contextCandidates.length ? (
                <p className="field-hint text-[11px] text-ink-muted">
                  No supported video file input found. To use previous-shot continuation, connect
                  LoadVideo or VHS_LoadVideo to Motion Context and reimport.
                </p>
              ) : null}

              {mapping ? (
                <div className="workflow-dependencies rounded-md border border-border-subtle bg-elevated/40 px-3 py-2 text-xs">
                  <strong className="font-semibold text-ink">Connected inputs</strong>
                  <p className="mt-0.5 text-ink-secondary">
                    Prompt, width, height, frames · Picture 1–9 · Audio 1–3 · optional seed
                    {mapping.context_video ? " · context video" : ""}
                  </p>
                </div>
              ) : null}

              <div>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={busy || !mapping}
                  onClick={() =>
                    void perform("saving", async () => {
                      await saveH3Mapping(analysis.import_id, mapping!);
                      applyAnalysis(await fetchH3ImportAnalysis(analysis.import_id));
                      remember(analysis.import_id);
                    })
                  }
                >
                  Confirm input nodes
                </Button>
              </div>
            </div>
          ) : (
            <p className="empty-copy text-xs text-ink-muted">Choose the final video output first.</p>
          )}
        </section>

        <section
          className="section-card flex flex-col gap-3.5 rounded-lg border border-border-subtle bg-panel p-4 shadow-[var(--shadow-card)]"
          aria-labelledby="test-title"
        >
          <div className="section-card-head flex items-center justify-between gap-3 border-b border-border-subtle pb-2.5">
            <h2
              id="test-title"
              className="section-card-title text-[13px] font-semibold tracking-[-0.01em] text-ink"
            >
              3. Validate &amp; Test
            </h2>
            <Badge tone={busy ? "info" : STATUS_TONES[stage]}>
              <StatusDot tone={busy ? "info" : STATUS_TONES[stage]} pulse={busy} />
              <span role="status" aria-live="polite">
                {busy ? `${operation}…` : STATUS_LABELS[stage]}
              </span>
            </Badge>
          </div>

          <p className="field-hint text-xs leading-relaxed text-ink-secondary">
            ComfyUI validates the graph, then a 56-frame test confirms the selected boundary.
            Multiple videos can be previewed and selected without rerunning.
          </p>

          <div>
            <Button
              type="button"
              variant="secondary"
              disabled={!canValidate}
              onClick={() =>
                analysis &&
                void perform("validating", async () => {
                  await validateH3Import(analysis.import_id);
                  applyAnalysis(await fetchH3ImportAnalysis(analysis.import_id));
                })
              }
            >
              Validate with ComfyUI
            </Button>
          </div>

          <div className="workflow-test-assets grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="field flex flex-col gap-1.5">
              <span className="text-xs font-medium text-ink-secondary">Picture for test</span>
              <Select
                value={picture}
                disabled={busy}
                onChange={(event) => setPicture(event.target.value)}
              >
                <option value="">Choose one Picture…</option>
                {pictures.map((asset) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.name}
                  </option>
                ))}
              </Select>
            </label>

            <label className="field flex flex-col gap-1.5">
              <span className="text-xs font-medium text-ink-secondary">
                Voice for test (optional standalone Audio)
              </span>
              <Select
                value={voice}
                disabled={busy || !mapping?.inputs.audio_input_pattern}
                onChange={(event) => setVoice(event.target.value)}
              >
                <option value="">No Audio reference</option>
                {voices.map((asset) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.name}
                  </option>
                ))}
              </Select>
            </label>

            {mapping?.context_video ? (
              <div className="sm:col-span-2 flex flex-col gap-1.5 rounded-md border border-border-subtle bg-elevated/40 p-3">
                <label className="field flex flex-col gap-1.5">
                  <span className="text-xs font-medium text-ink-secondary">Video for test</span>
                  <input
                    aria-label="Video for test"
                    type="file"
                    accept="video/*,.mp4,.mov,.webm,.mkv"
                    disabled={busy || !projectId}
                    className="block w-full cursor-pointer rounded-md border border-border-subtle bg-panel px-2.5 py-1.5 text-xs text-ink file:mr-3 file:cursor-pointer file:rounded file:border-0 file:bg-elevated file:px-2.5 file:py-1 file:text-xs file:font-medium file:text-ink"
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      event.target.value = "";
                      if (!file || !projectId) return;
                      const uploadProjectId = projectId;
                      setContextUploadId("");
                      setContextFilename("");
                      void perform("uploading", async () => {
                        const uploaded = await uploadVideoContext(uploadProjectId, file);
                        if (projectIdRef.current !== uploadProjectId) return;
                        setContextUploadId(uploaded.upload_id);
                        setContextFilename(file.name);
                      });
                    }}
                  />
                </label>
                {contextFilename ? (
                  <p className="field-hint font-mono text-[11px] text-ink">{contextFilename}</p>
                ) : null}
                <p className="field-hint text-[11px] text-ink-muted">
                  {projectId
                    ? "Upload a sample source clip to test the video input. Production uses the shot's selected source video."
                    : "Open a project to upload a test video."}
                </p>
              </div>
            ) : null}
          </div>

          <div className="actions flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="secondary"
              disabled={!projectId || busy}
              onClick={() => setAssetRefresh((value) => value + 1)}
            >
              Refresh assets
            </Button>
            <Button
              type="button"
              variant="secondary"
              disabled={!canTest}
              onClick={() =>
                analysis &&
                void perform("testing", async () => {
                  setJob(null);
                  const run = await testH3Import(
                    analysis.import_id,
                    picture,
                    voice || null,
                    mapping?.context_video && projectId && contextUploadId.trim()
                      ? { projectId, uploadId: contextUploadId.trim() }
                      : null,
                  );
                  remember(analysis.import_id, run);
                  setTest(run);
                  setStage("validated");
                })
              }
            >
              Run 56-frame test
            </Button>
          </div>

          {testCandidates.length ? (
            <div className="workflow-test-result flex flex-col gap-3 rounded-md border border-border-subtle bg-elevated/40 p-3">
              <strong className="text-xs font-semibold text-ink">
                {testCandidates.length > 1 ? "Choose the final test video" : "Test video"}
              </strong>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {testCandidates.map(([key, slot], index) => (
                  <div
                    key={key}
                    className="workflow-test-candidate flex flex-col gap-2 rounded-md border border-border-subtle bg-panel p-2"
                  >
                    <video
                      className="h3-preview aspect-video w-full rounded bg-black object-contain"
                      aria-label={`Workflow test video ${index + 1}`}
                      controls
                      preload="metadata"
                      src={slot.url || undefined}
                    />
                    {testCandidates.length > 1 ? (
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        disabled={busy}
                        onClick={() =>
                          analysis &&
                          void perform("selecting", async () => {
                            await selectH3TestOutput(analysis.import_id, index);
                            applyAnalysis(await fetchH3ImportAnalysis(analysis.import_id));
                            remember(analysis.import_id);
                            setTest(null);
                          })
                        }
                      >
                        Use video {index + 1}
                      </Button>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          <div className="actions flex flex-wrap items-center gap-2 border-t border-border-subtle pt-3">
            <Button
              type="button"
              variant="primary"
              disabled={!canActivate}
              onClick={() =>
                analysis &&
                void perform("activating", async () => {
                  const result = await activateH3Import(analysis.import_id);
                  const next = await fetchH3Profiles();
                  setProfiles({ ...next, active: result.active });
                  setSelectedWorkflow(result.active.profile_id);
                  setStage("active");
                })
              }
            >
              Use Workflow
            </Button>
            {test && error ? (
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  setError(null);
                  setPollVersion((value) => value + 1);
                }}
              >
                Check test status
              </Button>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}
