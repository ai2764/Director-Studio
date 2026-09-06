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
import { listLibraryAssets, type LibraryAsset, type LibraryKind } from "../library/api";
import { getH3Job, type H3JobRecord } from "../production/api";
import type {
  H3Analysis,
  H3Candidate,
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
type Operation = "idle" | "loading" | "importing" | "saving" | "validating" | "testing" | "activating" | "selecting";

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

function mappingFor(analysis: H3Analysis, h3NodeId: string, seedNodeId: string): H3Mapping | null {
  const outputNodeId = analysis.selected_output_node_id || analysis.mapping?.output.node_id;
  if (!outputNodeId || !h3NodeId) return null;
  return {
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
  const [test, setTest] = useState<H3TestRun | null>(null);
  const [job, setJob] = useState<H3JobRecord | null>(null);
  const [assetRefresh, setAssetRefresh] = useState(0);
  const [pollVersion, setPollVersion] = useState(0);
  const errorRef = useRef<HTMLDivElement>(null);
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
    return () => { cancelled = true; };
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
        if (!cancelled) setError(`Could not load test assets: ${err instanceof Error ? err.message : String(err)}`);
      });
    return () => { cancelled = true; };
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
        const candidates = Object.keys(nextJob.outputs).filter((key) => key.startsWith("video_candidate_"));
        if (candidates.length > 1) {
          setOperation("idle");
          return;
        }
        const current = await fetchH3ImportAnalysis(test.import_id);
        if (current.lifecycle.status !== "tested") {
          evidenceAttempts += 1;
          if (evidenceAttempts >= 5) throw new Error("Test completed, but its result is not available yet.");
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
    return () => { cancelled = true; clearTimeout(timer); };
  }, [test, pollVersion]);

  const outputCandidates = analysis?.output_candidates || [];
  const selectedOutput = analysis?.selected_output_node_id || mapping?.output.node_id || "";
  const h3Candidates = analysis?.h3_candidates || [];
  const seedCandidates = analysis?.seed_candidates || [];
  const testCandidates = job
    ? Object.entries(job.outputs)
        .filter(([key, slot]) => key.startsWith("video_candidate_") && slot.url)
        .sort(([left], [right]) => left.localeCompare(right, undefined, { numeric: true }))
    : [];
  const canValidate = Boolean(mapping && analysis && stage === "mapped" && !busy);
  const canTest = Boolean(mapping && picture && ["validated", "tested"].includes(stage) && !busy);
  const canActivate = Boolean(analysis && stage === "tested" && !busy);

  return (
    <div className="h3-workflow-setup" aria-busy={busy}>
      <aside className="workflow-profile-rail section-card" aria-labelledby="active-workflow-title">
        <h2 id="active-workflow-title" className="section-card-title">Current Workflow</h2>
        {profiles ? <>
          <strong className="workflow-profile-name">{profiles.active.display_name}</strong>
          <p className="field-hint">Local H3 jobs use this ComfyUI workflow.</p>
          <label className="field">
            <span>Installed workflow</span>
            <select value={selectedWorkflow} disabled={busy} onChange={(event) => setSelectedWorkflow(event.target.value)}>
              {profiles.profiles.map((item) => <option key={item.profile_id} value={item.profile_id}>{item.display_name}</option>)}
            </select>
          </label>
          <button type="button" className="btn secondary" disabled={busy || selectedWorkflow === profiles.active.profile_id} onClick={() => void perform("selecting", async () => {
            const result = await selectH3Profile(selectedWorkflow);
            setProfiles({ ...profiles, active: result.active });
          })}>Use Workflow</button>
          <details><summary>Workflow identity</summary><code className="workflow-hash">{profiles.active.workflow_sha256}</code></details>
          {profiles.active.warning ? <div className="banner" role="status">{profiles.active.warning.message}</div> : null}
        </> : <p className="field-hint">Loading active workflow…</p>}
      </aside>

      <div className="workflow-setup-main">
        {error ? <div className="banner error" role="alert" tabIndex={-1} ref={errorRef}>{error}</div> : null}
        <section className="section-card" aria-labelledby="custom-h3-title">
          <h2 id="custom-h3-title" className="section-card-title">Custom H3 Workflows</h2>
          <p className="field-hint">Import a ComfyUI API JSON. Director Studio leaves the internal graph unchanged and only connects its input and final-video boundaries.</p>
          <label className="field"><span>Import Workflow</span><input type="file" accept=".json,application/json" disabled={busy} onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) void perform("importing", async () => {
              setAnalysis(null); setMapping(null); setJob(null); setTest(null); setStage("draft");
              const imported = await importH3Workflow(file);
              remember(imported.import_id);
              applyAnalysis(await fetchH3ImportAnalysis(imported.import_id));
            });
          }} /></label>
          {analysis ? <>
            {analysis.issues.map((issue, index) => <p className="field-hint" key={`${issue.code}-${index}`}>{issue.message}</p>)}
            {analysis.fixed_dependencies.length ? <details><summary>Workflow-owned files</summary>{analysis.fixed_dependencies.map((item) => <p key={`${item.node_id}-${item.input_name}`}>{item.value}</p>)}</details> : null}
          </> : <p className="empty-copy">Choose the API-format JSON exported by ComfyUI.</p>}
        </section>

        <section className="section-card" aria-labelledby="output-title">
          <h2 id="output-title" className="section-card-title">1. Final video output</h2>
          <p className="field-hint">Choose the terminal node whose video Director Studio should keep. Node name is shown first; ID is secondary.</p>
          {analysis ? <label className="field"><span>Final video node</span><select aria-label="Final video node" value={selectedOutput} disabled={busy} onChange={(event) => void perform("selecting", async () => {
            const next = await selectH3ImportOutput(analysis.import_id, event.target.value);
            applyAnalysis(next);
          })}><option value="">Choose a final video node…</option>{outputCandidates.map((candidate) => <option key={candidate.node_id} value={candidate.node_id}>{nodeLabel(candidate)}</option>)}</select></label> : null}
        </section>

        <section className="section-card" aria-labelledby="inputs-title">
          <h2 id="inputs-title" className="section-card-title">2. H3 Inputs</h2>
          <p className="field-hint">After output selection, upstream H3 and optional seed nodes are discovered by reverse traversal.</p>
          {analysis && selectedOutput ? <>
            <label className="field"><span>H3 generation node</span><select aria-label="H3 generation node" value={mapping?.inputs.h3_node_id || ""} disabled={busy} onChange={(event) => {
              const next = mappingFor(analysis, event.target.value, mapping?.inputs.seed_node_id || "");
              setMapping(next); setStage("mapped");
            }}><option value="">Choose the H3 node…</option>{h3Candidates.map((candidate) => <option key={candidate.node_id} value={candidate.node_id}>{nodeLabel(candidate)}</option>)}</select></label>
            <label className="field"><span>Seed node (optional)</span><select aria-label="Seed node (optional)" value={mapping?.inputs.seed_node_id || ""} disabled={busy || !mapping} onChange={(event) => {
              if (!mapping) return;
              setMapping({ ...mapping, inputs: { ...mapping.inputs, seed_node_id: event.target.value || null, seed_input: event.target.value ? "noise_seed" : null } });
              setStage("mapped");
            }}><option value="">Use workflow seed settings</option>{seedCandidates.map((candidate) => <option key={candidate.node_id} value={candidate.node_id}>{nodeLabel(candidate)}</option>)}</select></label>
            {mapping ? <div className="workflow-dependencies"><strong>Connected inputs</strong><p>Prompt, width, height, frames · Picture 1–9 · Audio 1–3 · optional seed</p></div> : null}
            <button type="button" className="btn secondary" disabled={busy || !mapping} onClick={() => void perform("saving", async () => {
              await saveH3Mapping(analysis.import_id, mapping!);
              applyAnalysis(await fetchH3ImportAnalysis(analysis.import_id));
              remember(analysis.import_id);
            })}>Confirm input nodes</button>
          </> : <p className="empty-copy">Choose the final video output first.</p>}
        </section>

        <section className="section-card" aria-labelledby="test-title">
          <div className="section-card-head"><h2 id="test-title" className="section-card-title">3. Validate &amp; Test</h2><span role="status" aria-live="polite">{busy ? `${operation}…` : STATUS_LABELS[stage]}</span></div>
          <p className="field-hint">ComfyUI validates the graph, then a 56-frame test confirms the selected boundary. Multiple videos can be previewed and selected without rerunning.</p>
          <button type="button" className="btn secondary" disabled={!canValidate} onClick={() => analysis && void perform("validating", async () => {
            await validateH3Import(analysis.import_id);
            applyAnalysis(await fetchH3ImportAnalysis(analysis.import_id));
          })}>Validate with ComfyUI</button>
          <div className="workflow-test-assets">
            <label className="field"><span>Picture for test</span><select value={picture} disabled={busy} onChange={(event) => setPicture(event.target.value)}><option value="">Choose one Picture…</option>{pictures.map((asset) => <option key={asset.id} value={asset.id}>{asset.name}</option>)}</select></label>
            <label className="field"><span>Voice for test (optional standalone Audio)</span><select value={voice} disabled={busy || !mapping?.inputs.audio_input_pattern} onChange={(event) => setVoice(event.target.value)}><option value="">No Audio reference</option>{voices.map((asset) => <option key={asset.id} value={asset.id}>{asset.name}</option>)}</select></label>
          </div>
          <div className="actions">
            <button type="button" className="btn secondary" disabled={!projectId || busy} onClick={() => setAssetRefresh((value) => value + 1)}>Refresh assets</button>
            <button type="button" className="btn secondary" disabled={!canTest} onClick={() => analysis && void perform("testing", async () => {
              setJob(null);
              const run = await testH3Import(analysis.import_id, picture, voice || null);
              remember(analysis.import_id, run); setTest(run); setStage("validated");
            })}>Run 56-frame test</button>
          </div>
          {testCandidates.length ? <div className="workflow-test-result"><strong>{testCandidates.length > 1 ? "Choose the final test video" : "Test video"}</strong>{testCandidates.map(([key, slot], index) => <div key={key} className="workflow-test-candidate"><video className="h3-preview" aria-label={`Workflow test video ${index + 1}`} controls preload="metadata" src={slot.url || undefined} />{testCandidates.length > 1 ? <button type="button" className="btn secondary" disabled={busy} onClick={() => analysis && void perform("selecting", async () => {
                await selectH3TestOutput(analysis.import_id, index);
                applyAnalysis(await fetchH3ImportAnalysis(analysis.import_id));
                remember(analysis.import_id); setTest(null);
              })}>Use video {index + 1}</button> : null}</div>)}</div> : null}
          <div className="actions"><button type="button" className="btn primary" disabled={!canActivate} onClick={() => analysis && void perform("activating", async () => {
            const result = await activateH3Import(analysis.import_id);
            const next = await fetchH3Profiles();
            setProfiles({ ...next, active: result.active }); setSelectedWorkflow(result.active.profile_id); setStage("active");
          })}>Use Workflow</button>{test && error ? <button type="button" className="btn secondary" onClick={() => { setError(null); setPollVersion((value) => value + 1); }}>Check test status</button> : null}</div>
        </section>
      </div>
    </div>
  );
}
