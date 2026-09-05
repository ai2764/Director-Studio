import { useEffect, useRef, useState } from "react";
import {
  activateH3Import,
  fetchH3ImportAnalysis,
  fetchH3Profiles,
  importH3Workflow,
  proposeH3Mapping,
  saveH3Mapping,
  selectH3Profile,
  testH3Import,
  validateH3Import,
} from "../../shared/api/client";
import type { H3Profiles } from "../../shared/api/types";
import { useProject } from "../../shared/project/ProjectContext";
import {
  listLibraryAssets,
  type LibraryAsset,
  type LibraryKind,
} from "../library/api";
import { getH3Job, type H3JobRecord } from "../production/api";
import type {
  H3Analysis,
  H3Candidate,
  H3LifecycleStatus,
  H3Mapping,
  H3Proposal,
  H3TestRun,
} from "./types";

const STORAGE_KEY = "director-studio.h3-setup";
const PICTURE_KINDS: LibraryKind[] = [
  "actors",
  "costumes",
  "scenes",
  "props",
  "layouts",
];
const STATUS_LABELS: Record<H3LifecycleStatus, string> = {
  draft: "Draft",
  mapped: "Mapped",
  validated: "Validated",
  tested: "Tested",
  active: "Active",
};
type Operation =
  | "idle"
  | "loading"
  | "importing"
  | "suggesting"
  | "saving"
  | "validating"
  | "testing"
  | "activating"
  | "selecting";
function remember(importId: string, test?: H3TestRun) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ importId, test }));
  } catch {
    /* Setup still works when browser storage is unavailable. */
  }
}
function remembered(): { importId?: string; test?: H3TestRun } {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}
function sameMapping(left: H3Mapping | null, right: H3Mapping | null): boolean {
  return Boolean(
    left &&
      right &&
      Object.keys(left).every(
        (key) =>
          JSON.stringify(left[key as keyof H3Mapping]) ===
          JSON.stringify(right[key as keyof H3Mapping]),
      ),
  );
}
function initialMapping(analysis: H3Analysis): H3Mapping | null {
  if (analysis.mapping) return analysis.mapping;
  if (analysis.compatibility === "unsupported") return null;
  const h3 = analysis.agent_manifest.nodes.find((node) =>
    node.candidate_roles.includes("h3"),
  );
  if (!h3) return null;
  return {
    h3_node_id: h3.node_id,
    prompt_input: "prompt",
    width_input: "width",
    height_input: "height",
    frames_input: "length",
    picture_input_pattern: "ref_images.ref_image_{index}",
    audio_input_pattern: "ref_audios.ref_audio_{index}",
    seed_node_id:
      analysis.seed_candidates.length === 1
        ? analysis.seed_candidates[0].node_id
        : "",
    seed_input: "noise_seed",
    saver_node_id:
      analysis.saver_candidates.length === 1
        ? analysis.saver_candidates[0].node_id
        : "",
    output_prefix_input: "filename_prefix",
    output_fields: ["videos"],
  };
}

export function H3WorkflowSetup() {
  const { projectId } = useProject();
  const [profiles, setProfiles] = useState<H3Profiles | null>(null);
  const [selectedProfile, setSelectedProfile] = useState("");
  const [analysis, setAnalysis] = useState<H3Analysis | null>(null);
  const [mapping, setMapping] = useState<H3Mapping | null>(null);
  const [dirty, setDirty] = useState(false);
  const [proposal, setProposal] = useState<H3Proposal | null>(null);
  const [stage, setStage] = useState<H3LifecycleStatus>("draft");
  const [operation, setOperation] = useState<Operation>("loading");
  const [error, setError] = useState<string | null>(null);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [pictures, setPictures] = useState<LibraryAsset[]>([]);
  const [voices, setVoices] = useState<LibraryAsset[]>([]);
  const [picture, setPicture] = useState("");
  const [voice, setVoice] = useState("");
  const [test, setTest] = useState<H3TestRun | null>(null);
  const [job, setJob] = useState<H3JobRecord | null>(null);
  const [pollVersion, setPollVersion] = useState(0);
  const errorRef = useRef<HTMLDivElement>(null);
  const busy = operation !== "idle";
  const applyAnalysis = (next: H3Analysis) => {
    setAnalysis(next);
    setMapping(initialMapping(next));
    setDirty(false);
    setStage(next.lifecycle.status);
  };
  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);
  useEffect(() => {
    let cancelled = false;
    const saved = remembered();
    (async () => {
      try {
        const current = await fetchH3Profiles();
        if (cancelled) return;
        setProfiles(current);
        setSelectedProfile(current.active.profile_id);
        if (saved.importId) {
          const next = await fetchH3ImportAnalysis(saved.importId);
          if (cancelled) return;
          applyAnalysis(next);
          if (next.lifecycle.test_job_id) {
            const restored = await getH3Job(next.lifecycle.test_job_id);
            if (!cancelled) setJob(restored);
          } else if (saved.test?.import_id === saved.importId) {
            setTest(saved.test);
          }
        }
      } catch (err) {
        if (!cancelled)
          setError(err instanceof Error ? err.message : String(err));
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
    setPicture("");
    setVoice("");
    setPictures([]);
    setVoices([]);
    setLibraryError(null);
    if (!projectId) return;
    Promise.all(
      [...PICTURE_KINDS, "voices" as LibraryKind].map((kind) =>
        listLibraryAssets(kind, projectId, true),
      ),
    )
      .then((groups) => {
        if (!cancelled) {
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
          setVoices(
            groups[5].filter(
              (asset) => asset.meta?.h3_ready && asset.files.reference,
            ),
          );
        }
      })
      .catch((err) => {
        if (!cancelled)
          setLibraryError(
            `Could not load test assets: ${err instanceof Error ? err.message : String(err)}`,
          );
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  useEffect(() => {
    if (!test || !analysis) return;
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
        if (nextJob.status !== "succeeded")
          throw new Error(
            nextJob.error || `Test ${nextJob.status}. Run the test again.`,
          );
        const current = await fetchH3ImportAnalysis(test.import_id);
        if (cancelled) return;
        if (
          current.workflow_sha256 !== test.workflow_sha256 ||
          current.lifecycle.workflow_sha256 !== test.workflow_sha256 ||
          current.lifecycle.mapping_sha256 !== test.mapping_sha256 ||
          !sameMapping(current.mapping, mapping)
        ) {
          setStage("mapped");
          throw new Error(
            "Workflow or mapping changed. Validate and test the current workflow again.",
          );
        }
        if (
          current.lifecycle.status !== "tested" ||
          current.lifecycle.test_job_id !== test.job_id
        ) {
          // Job completion may precede the durable evidence write by one poll.
          evidenceAttempts += 1;
          if (evidenceAttempts >= 5)
            throw new Error(
              "Test evidence is not available yet. Check test status to retry, or run another test.",
            );
          setOperation("testing");
          timer = setTimeout(poll, 2000);
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
    // Captures the mapping that was submitted; edits are disabled during polling.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [test, pollVersion]);

  async function perform(
    operationName: Operation,
    action: () => Promise<void>,
  ) {
    setOperation(operationName);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setOperation("idle");
    }
  }
  const canActivate =
    !busy &&
    !dirty &&
    analysis?.lifecycle.status === "tested" &&
    stage === "tested" &&
    analysis.lifecycle.workflow_sha256 === analysis.workflow_sha256 &&
    Boolean(
      analysis.lifecycle.mapping_sha256 && analysis.lifecycle.test_job_id,
    );
  const canTest =
    !busy &&
    !dirty &&
    Boolean(picture && analysis && ["validated", "tested"].includes(stage));
  const editMapping = (value: H3Mapping) => {
    setMapping(value);
    setDirty(true);
    setStage("mapped");
    setJob(null);
    setTest(null);
  };
  const binding = (
    label: string,
    key: "seed_node_id" | "saver_node_id",
    candidates: H3Candidate[],
  ) =>
    mapping && (
      <div className="workflow-binding" key={key}>
        <span>{label}</span>
        {candidates.length > 1 ? (
          <select
            aria-label={label}
            value={mapping[key]}
            disabled={busy}
            onChange={(event) =>
              editMapping({ ...mapping, [key]: event.target.value })
            }
          >
            <option value="">Choose a node…</option>
            {candidates.map((candidate) => (
              <option key={candidate.node_id} value={candidate.node_id}>
                {candidate.title || candidate.class_type} (node{" "}
                {candidate.node_id})
              </option>
            ))}
          </select>
        ) : (
          <code>
            {mapping[key]} · {candidates[0]?.class_type || "Unavailable"}
          </code>
        )}
      </div>
    );
  return (
    <div className="h3-workflow-setup" aria-busy={busy}>
      <aside
        className="workflow-profile-rail section-card"
        aria-labelledby="active-profile-title"
      >
        <h2 id="active-profile-title" className="section-card-title">
          Active profile
        </h2>
        {profiles ? (
          <>
            <strong className="workflow-profile-name">
              {profiles.active.display_name}
            </strong>
            <p className="field-hint">
              Local ComfyUI generation uses this workflow.
            </p>
            {profiles.active.warning ? (
              <div className="banner" role="status">
                <strong>Using Built-in Official H3</strong>
                <span>{profiles.active.warning.message}</span>
              </div>
            ) : null}
            <label className="field">
              <span>Installed profile</span>
              <select
                value={selectedProfile}
                disabled={busy}
                onChange={(event) => setSelectedProfile(event.target.value)}
              >
                {profiles.profiles.map((profile) => (
                  <option key={profile.profile_id} value={profile.profile_id}>
                    {profile.display_name}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="btn secondary"
              disabled={busy || selectedProfile === profiles.active.profile_id}
              onClick={() =>
                void perform("selecting", async () => {
                  const result = await selectH3Profile(selectedProfile);
                  setProfiles({ ...profiles, active: result.active });
                })
              }
            >
              Use profile
            </button>
          </>
        ) : busy ? (
          <p className="field-hint">Loading active workflow…</p>
        ) : (
          <>
            <p className="field-hint">Active workflow status is unavailable.</p>
            <button
              type="button"
              className="btn secondary"
              onClick={() =>
                void perform("loading", async () => {
                  const current = await fetchH3Profiles();
                  setProfiles(current);
                  setSelectedProfile(current.active.profile_id);
                })
              }
            >
              Retry loading profiles
            </button>
          </>
        )}
      </aside>
      <div className="workflow-setup-main">
        {error ? (
          <div
            className="banner error"
            role="alert"
            tabIndex={-1}
            ref={errorRef}
          >
            {error}
          </div>
        ) : null}
        <section
          className="section-card"
          aria-labelledby="workflow-analysis-title"
        >
          <h2 id="workflow-analysis-title" className="section-card-title">
            Workflow analysis
          </h2>
          <p className="field-hint">
            Import a ComfyUI API workflow for H3 Ref2AV. Your workflow keeps its
            models and enhancement stages.
          </p>
          <label className="field">
            <span>Import H3 API workflow</span>
            <input
              type="file"
              accept=".json,application/json"
              disabled={busy}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file)
                  void perform("importing", async () => {
                    setTest(null);
                    setJob(null);
                    setProposal(null);
                    setAnalysis(null);
                    setMapping(null);
                    setStage("draft");
                    const imported = await importH3Workflow(file);
                    remember(imported.import_id);
                    applyAnalysis(
                      await fetchH3ImportAnalysis(imported.import_id),
                    );
                  });
              }}
            />
          </label>
          {analysis ? (
            <>
              <p className="workflow-compatibility">
                {
                  {
                    auto_compatible: "Auto-compatible",
                    needs_confirmation: "Needs confirmation",
                    unsupported: "Unsupported",
                  }[analysis.compatibility]
                }
              </p>
              {analysis.agent_manifest.nodes
                .filter((node) => node.candidate_roles.includes("h3"))
                .map((node) => (
                  <p key={node.node_id}>{node.class_type}</p>
                ))}
              <details>
                <summary>Workflow identity</summary>
                <code className="workflow-hash">
                  {analysis.workflow_sha256}
                </code>
              </details>
              {analysis.issues.map((issue, index) => (
                <p className="field-hint" key={index}>
                  {issue.message}
                </p>
              ))}
              {analysis.fixed_dependencies.length ? (
                <div className="workflow-dependencies">
                  <strong>Files required by this workflow</strong>
                  {analysis.fixed_dependencies.map((item, index) => (
                    <p key={index}>
                      Node {item.node_id}: {item.value}
                    </p>
                  ))}
                </div>
              ) : null}
            </>
          ) : (
            <p className="empty-copy">
              Choose an API JSON export to inspect its H3 bindings.
            </p>
          )}
        </section>
        <section
          className="section-card"
          aria-labelledby="boundary-mapping-title"
        >
          <h2 id="boundary-mapping-title" className="section-card-title">
            Boundary mapping
          </h2>
          {mapping && analysis ? (
            <>
              <div className="workflow-binding">
                <span>H3 node</span>
                <code>{mapping.h3_node_id}</code>
              </div>
              {[
                ["Prompt", mapping.prompt_input],
                [
                  "Dimensions",
                  `${mapping.width_input} / ${mapping.height_input}`,
                ],
                ["Frames", mapping.frames_input],
                ["Pictures", mapping.picture_input_pattern],
                ["Audio", mapping.audio_input_pattern || "Not supported"],
                ["Output prefix", mapping.output_prefix_input],
              ].map(([label, value]) => (
                <div className="workflow-binding" key={label}>
                  <span>{label}</span>
                  <code>{value}</code>
                </div>
              ))}
              {binding("Seed node", "seed_node_id", analysis.seed_candidates)}
              {binding(
                "Output saver",
                "saver_node_id",
                analysis.saver_candidates,
              )}
              <div className="actions">
                <button
                  type="button"
                  className="btn secondary"
                  disabled={
                    busy || !mapping.seed_node_id || !mapping.saver_node_id
                  }
                  onClick={() =>
                    void perform("saving", async () => {
                      setTest(null);
                      setJob(null);
                      await saveH3Mapping(analysis.import_id, mapping);
                      applyAnalysis(
                        await fetchH3ImportAnalysis(analysis.import_id),
                      );
                      remember(analysis.import_id);
                    })
                  }
                >
                  Save mapping
                </button>
                {analysis.compatibility === "needs_confirmation" ? (
                  <button
                    type="button"
                    className="btn secondary"
                    disabled={busy}
                    onClick={() =>
                      void perform("suggesting", async () =>
                        setProposal(await proposeH3Mapping(analysis.import_id)),
                      )
                    }
                  >
                    Suggest mapping
                  </button>
                ) : null}
              </div>
              {proposal ? (
                <div className="workflow-advice">
                  <strong>Mapping advice — review before using</strong>
                  {proposal.explanations.map((explanation, index) => (
                    <p key={index}>{explanation}</p>
                  ))}
                  <button
                    type="button"
                    className="btn secondary"
                    disabled={busy}
                    onClick={() => editMapping(proposal.mapping)}
                  >
                    Use suggested mapping
                  </button>
                </div>
              ) : null}
            </>
          ) : (
            <p className="empty-copy">
              Compatible workflow bindings appear after import.
            </p>
          )}
        </section>
        <section className="section-card" aria-labelledby="workflow-test-title">
          <div className="section-card-head">
            <h2 id="workflow-test-title" className="section-card-title">
              Validation &amp; test
            </h2>
            <span role="status" aria-live="polite">
              {busy
                ? `${operation[0].toUpperCase()}${operation.slice(1)}…`
                : STATUS_LABELS[stage]}
            </span>
          </div>
          <p className="field-hint">
            Validate the contract and local dependencies, then run a short test
            with one Picture and an optional Voice. Activation requires a
            successful test of this exact workflow and mapping.
          </p>
          <button
            type="button"
            className="btn secondary"
            disabled={
              busy ||
              dirty ||
              !mapping ||
              !mapping.seed_node_id ||
              !mapping.saver_node_id
            }
            onClick={() =>
              analysis &&
              void perform("validating", async () => {
                setTest(null);
                setJob(null);
                setStage("mapped");
                await validateH3Import(analysis.import_id);
                applyAnalysis(await fetchH3ImportAnalysis(analysis.import_id));
                remember(analysis.import_id);
              })
            }
          >
            Validate workflow
          </button>
          {dirty ? (
            <p className="field-hint">
              Save your mapping before validating and testing again.
            </p>
          ) : null}
          {!projectId ? (
            <p className="field-hint">
              Select a project in the header to choose test assets.
            </p>
          ) : null}
          {libraryError ? (
            <p role="alert" className="field-hint">
              {libraryError}
            </p>
          ) : null}
          <div className="workflow-test-assets">
            <label className="field">
              <span>Picture for test</span>
              <select
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
              </select>
            </label>
            <label className="field">
              <span>Voice for test (optional)</span>
              <select
                value={voice}
                disabled={busy || !mapping?.audio_input_pattern}
                onChange={(event) => setVoice(event.target.value)}
              >
                <option value="">No Voice reference</option>
                {voices.map((asset) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="actions">
            <button
              type="button"
              className="btn secondary"
              disabled={!canTest}
              onClick={() =>
                analysis &&
                void perform("testing", async () => {
                  setTest(null);
                  setJob(null);
                  setStage("validated");
                  const run = await testH3Import(
                    analysis.import_id,
                    picture,
                    mapping?.audio_input_pattern ? voice || null : null,
                  );
                  remember(analysis.import_id, run);
                  setTest(run);
                })
              }
            >
              Run test
            </button>
            <button
              type="button"
              className="btn primary"
              disabled={!canActivate}
              onClick={() =>
                analysis &&
                void perform("activating", async () => {
                  const result = await activateH3Import(analysis.import_id);
                  const next = await fetchH3Profiles();
                  setProfiles({ ...next, active: result.active });
                  setSelectedProfile(result.active.profile_id);
                  setStage("active");
                })
              }
            >
              Activate profile
            </button>
            {test && !busy && error ? (
              <button
                type="button"
                className="btn secondary"
                onClick={() => {
                  setError(null);
                  setOperation("testing");
                  setPollVersion((version) => version + 1);
                }}
              >
                Check test status
              </button>
            ) : null}
          </div>
          {job ? (
            <div className="workflow-test-result">
              <p>Test job: {job.status}</p>
              {job.outputs.video?.url ? (
                <video
                  className="h3-preview"
                  aria-label="Workflow test video"
                  controls
                  preload="metadata"
                  src={job.outputs.video.url}
                />
              ) : null}
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}
