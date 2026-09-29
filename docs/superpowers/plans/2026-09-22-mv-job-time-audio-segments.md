# MV Job-Time Audio Segments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let every H3 submission entry point automatically stage a Shot's planned MV song window as Job-local `<Audio 1>` while leaving normal Voice references unchanged.

**Architecture:** Persist one project-relative song master on MV Projects and an optional four-timestamp `music_segment` on Shots. Extend the existing canonical Shot submit preflight to prepare the segment before Job creation; Agent, manual Production, managed run, and direct REST callers continue to share that endpoint. Keep ffmpeg work in a focused media module and gate every new behavior on `project.mode == ProjectMode.mv` plus a non-null segment.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, ffmpeg/ffprobe, React 19, TypeScript, Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-09-22-mv-job-time-audio-segments-design.md`

## Global Constraints

- A Project stores one authoritative master below its own project root using a relative path; never persist the uploader's absolute path.
- `music_segment` is optional; absence means no song audio is sent.
- Only MV Projects may import a master or persist `music_segment`.
- A submit interval must contain its core interval and stay within the master duration.
- Song segmentation runs inside the canonical submit preflight, not inside `start_h3_video` or the UI.
- Normal `voice_refs` behavior, numbering, signatures, frame calculation, and UI remain unchanged.
- A Shot may not combine `music_segment` and `voice_refs` in the first version.
- Prepared excerpts are stored only in Job inputs, never in the Voice Library.
- H3 output audio is reference-generation output; final soundtrack muxing is out of scope.

## Review Focus

- A non-MV Shot containing `music_segment` must fail before persistence or Job creation, rather than silently ignoring it.
- Replacing a master with a shorter file must keep authored Shot intervals but block only the invalid submission with a precise bounds error.
- An MV segment and `voice_refs` together must fail before prompt refresh or durable Job creation.
- Segment extraction failure must leave no Job directory and must not change the Shot status.
- A normal Director Voice-reference submit must retain `voice_audio_1`, its existing prompt signature, and its previous frame calculation.

---

### Task 1: Persist MV master and Shot segment contracts

**Files:**
- Modify: `backend/app/core/projects/models.py`
- Modify: `backend/app/agents/director/planner.py`
- Modify: `backend/app/agents/director/service.py`
- Modify: `backend/app/api/projects.py`
- Test: `backend/tests/test_projects_api.py`
- Test: `backend/tests/test_director_native_tools.py`

**Interfaces:**
- Produces: `ProjectMusicMaster`, `ShotMusicSegment`, `Project.music_master`, `Shot.music_segment`.
- Produces: Agent storyboard/revision payloads that accept `music_segment: ShotMusicSegment | None` only for MV projects.
- Consumes: Existing `ProjectMode.mv`, storyboard save, append, and revise paths.

- [ ] **Step 1: Write failing model and API tests**

Add tests that express the public contract:

```python
def test_legacy_project_and_shot_default_mv_audio_fields_to_none():
    project = Project.model_validate({
        "id": "prj_old", "name": "Old", "script_text": "", "mode": "director",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    })
    shot = Shot(id="sht_old", project_id=project.id, scene_id="sc1",
                title="Old", script_beat="beat", duration_s=2)
    assert project.music_master is None
    assert shot.music_segment is None


def test_music_segment_requires_submit_interval_to_contain_core():
    with pytest.raises(ValueError, match="contain"):
        ShotMusicSegment(core_start_s=4.5, core_end_s=8.0,
                         submit_start_s=5.0, submit_end_s=8.5)


def test_non_mv_patch_rejects_music_segment(client, api_env):
    project, shot = seed_project_and_shot(mode="director")
    response = client.patch(f"/api/shots/{shot.id}", json={
        "music_segment": {"core_start_s": 1, "core_end_s": 2,
                          "submit_start_s": 0.5, "submit_end_s": 2.75}
    })
    assert response.status_code == 400
    assert "Music Video" in response.text
```

Add native-tool tests proving MV storyboard and revision payloads persist all four values while the same payload is rejected for a Director project.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
py -3 -m pytest -q tests/test_projects_api.py tests/test_director_native_tools.py -k "music_segment or mv_audio_fields"
```

Expected: collection or assertion failures because the types and fields do not exist.

- [ ] **Step 3: Add the minimal data types and persistence plumbing**

Implement:

```python
class ProjectMusicMaster(BaseModel):
    filename: str
    relative_path: str
    duration_s: float = Field(gt=0)
    content_sha256: str
    source_format: str


class ShotMusicSegment(BaseModel):
    core_start_s: float = Field(ge=0)
    core_end_s: float = Field(gt=0)
    submit_start_s: float = Field(ge=0)
    submit_end_s: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_intervals(self) -> "ShotMusicSegment":
        if self.core_start_s >= self.core_end_s:
            raise ValueError("music core interval must have positive duration")
        if self.submit_start_s >= self.submit_end_s:
            raise ValueError("music submit interval must have positive duration")
        if self.submit_start_s > self.core_start_s or self.submit_end_s < self.core_end_s:
            raise ValueError("music submit interval must contain the core interval")
        return self
```

Add optional fields to `Project`, `Shot`, `ShotPatchBody`, `ShotDraft`, and `ShotRevisionSubmission`. In service/API persistence code, look up the owning Project and reject a non-null segment unless its mode is `mv`. Preserve explicit `null` so a user or Agent can remove a segment. Invalidate only a new `prompt_music_signature` metadata entry when a segment changes.

- [ ] **Step 4: Run Task 1 tests and the existing planning suite**

Run:

```powershell
py -3 -m pytest -q tests/test_projects_api.py tests/test_director_native_tools.py tests/test_director_agent.py
```

Expected: all pass.

- [ ] **Step 5: Commit Task 1**

```powershell
git add backend/app/core/projects/models.py backend/app/agents/director/planner.py backend/app/agents/director/service.py backend/app/api/projects.py backend/tests/test_projects_api.py backend/tests/test_director_native_tools.py
git commit -m "feat(mv): persist song masters and shot segments"
```

### Task 2: Import and safely resolve one project song master

**Files:**
- Create: `backend/app/core/media/music_segments.py`
- Modify: `backend/app/api/projects.py`
- Modify: `backend/app/core/projects/store.py`
- Test: `backend/tests/test_mv_music_segments.py`
- Test: `backend/tests/test_projects_api.py`

**Interfaces:**
- Produces: `import_music_master(project_id: str, filename: str, data: bytes) -> ProjectMusicMaster`.
- Produces: `resolve_music_master(project: Project) -> Path` constrained to the Project root.
- Consumes: `core.library.audio.probe_audio`, `project_dir`, `save_project`, existing maximum upload size.

- [ ] **Step 1: Write failing import and path-safety tests**

Create tests using a generated short WAV:

```python
def test_mv_music_upload_is_project_owned_and_relative(client, mv_project, wav_bytes):
    response = client.post(
        f"/api/projects/{mv_project.id}/music-master",
        files={"file": ("song.wav", wav_bytes, "audio/wav")},
    )
    assert response.status_code == 200
    master = response.json()["music_master"]
    assert master["relative_path"] == "music/master.wav"
    assert master["duration_s"] == pytest.approx(3.0, abs=0.05)
    assert Path(master["relative_path"]).is_absolute() is False


def test_director_project_cannot_import_music_master(client, director_project, wav_bytes):
    response = client.post(
        f"/api/projects/{director_project.id}/music-master",
        files={"file": ("song.wav", wav_bytes, "audio/wav")},
    )
    assert response.status_code == 409


def test_invalid_replacement_keeps_previous_master(client, seeded_mv_master):
    response = client.post(
        f"/api/projects/{seeded_mv_master.project_id}/music-master",
        files={"file": ("broken.wav", b"not audio", "audio/wav")},
    )
    assert response.status_code == 400
    assert load_project(seeded_mv_master.project_id).music_master == seeded_mv_master.metadata
```

Add a direct `resolve_music_master` test that replaces `relative_path` with `../outside.wav` and expects a path-safety error.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
py -3 -m pytest -q tests/test_mv_music_segments.py tests/test_projects_api.py -k "music_master"
```

Expected: failures because the endpoint and media helper do not exist.

- [ ] **Step 3: Implement atomic import and safe resolution**

In `music_segments.py`, sanitize the upload suffix against the supported audio suffixes, write bytes to a UUID-named staging file below `<project>/music`, call `probe_audio`, compute SHA-256, then atomically replace `master.<suffix>`. Delete only the staging file on failure. Remove a prior master file only after the replacement validates and Project JSON saves.

Expose a multipart endpoint:

```python
@router.post("/projects/{project_id}/music-master", response_model=Project)
async def import_music_master_endpoint(project_id: str, file: UploadFile = File(...)) -> Project:
    project = load_project(project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    if project.mode != ProjectMode.mv:
        raise HTTPException(409, "Song masters are available only for Music Video projects")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Song master is empty")
    try:
        master = import_music_master(project.id, file.filename or "master.wav", data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    updated = project.model_copy(update={"music_master": master})
    save_project(updated)
    return updated
```

Reject non-MV mode, missing projects, empty uploads, unsupported suffixes, and files larger than `settings.max_upload_mb`. Persist the returned metadata with `save_project`.

- [ ] **Step 4: Run Task 2 tests**

Run:

```powershell
py -3 -m pytest -q tests/test_mv_music_segments.py tests/test_projects_api.py
```

Expected: all pass.

- [ ] **Step 5: Commit Task 2**

```powershell
git add backend/app/core/media/music_segments.py backend/app/api/projects.py backend/app/core/projects/store.py backend/tests/test_mv_music_segments.py backend/tests/test_projects_api.py
git commit -m "feat(mv): import project song masters"
```

### Task 3: Prepare MV Audio 1 inside the canonical H3 submit path

**Files:**
- Modify: `backend/app/core/media/music_segments.py`
- Modify: `backend/app/api/projects.py`
- Modify: `backend/app/agents/director/service.py`
- Test: `backend/tests/test_mv_music_segments.py`
- Test: `backend/tests/test_projects_api.py`
- Test: `backend/tests/test_managed_h3_start_tool.py`
- Test: `backend/tests/test_h3_dialogue_contract.py`

**Interfaces:**
- Produces: `PreparedMusicSegment(filename: str, data: bytes, duration_s: float)`.
- Produces: `prepare_music_segment(project: Project, segment: ShotMusicSegment) -> PreparedMusicSegment`.
- Produces: stable `music_prompt_signature(project, shot)` based on master SHA-256 and all four timestamps.
- Consumes: canonical `submit_shot_endpoint`, existing Job input mapping, `frames_for_audio_seconds`, existing prompt refresh.

- [ ] **Step 1: Write failing extraction and submission tests**

Add a real ffmpeg extraction test that creates a multi-tone WAV and verifies output metadata:

```python
def test_prepare_music_segment_outputs_32khz_stereo_exact_window(mv_master):
    prepared = prepare_music_segment(
        mv_master.project,
        ShotMusicSegment(core_start_s=1.0, core_end_s=2.0,
                         submit_start_s=0.5, submit_end_s=2.75),
    )
    path = write_temp(prepared.data, suffix=".wav")
    meta = probe_audio(path)
    assert meta.duration_s == pytest.approx(2.25, abs=0.05)
    assert meta.source_sample_rate == 32000
    assert meta.source_channels == 2
```

Add canonical submit tests:

```python
def test_manual_submit_stages_mv_segment_as_audio_1(client, ready_mv_shot):
    response = client.post(f"/api/shots/{ready_mv_shot.id}/submit", json={
        "h3_provider": "local", "width": 864, "height": 480,
    })
    assert response.status_code == 200
    job = load_job(response.json()["h3_job_id"])
    assert job.params["audio_keys"] == ["music_audio_1"]
    assert job.params["duration_s"] == pytest.approx(2.25)
    assert (job_dir(job.id, project_id=job.project_id) / "inputs" / "music_audio_1.wav").exists()


def test_director_voice_reference_submit_is_unchanged(client, ready_director_voice_shot):
    response = client.post(f"/api/shots/{ready_director_voice_shot.id}/submit", json={
        "h3_provider": "local", "width": 864, "height": 480,
    })
    job = load_job(response.json()["h3_job_id"])
    assert job.params["audio_keys"] == ["voice_audio_1"]
    assert job.params["duration_s"] == ready_director_voice_shot.duration_s
```

Add tests for missing master, out-of-bounds window, mixed segment plus Voice refs, ffmpeg failure, and stale prompt refresh failure. Assert no new Job ID/directory and unchanged Shot status in every failure. Add an Agent one-off or managed `start_h3_video` test that asserts the resulting Job has `music_audio_1`, proving the tool reaches the same endpoint.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
py -3 -m pytest -q tests/test_mv_music_segments.py tests/test_projects_api.py tests/test_managed_h3_start_tool.py tests/test_h3_dialogue_contract.py -k "music_segment or voice_reference_submit_is_unchanged"
```

Expected: extraction and staging assertions fail because canonical submission still reads only `voice_refs`.

- [ ] **Step 3: Implement Job-time preparation and prompt freshness**

Add `prepare_music_segment` using:

```text
ffmpeg -y -v error -ss <submit_start> -i <master> -t <duration> \
  -vn -ac 2 -ar 32000 -c:a pcm_s16le <temporary.wav>
```

Validate the generated file with `probe_audio`, read bytes, and remove the temporary file in `finally`.

In `submit_shot_endpoint`, load the Project before prompt validation and branch only as follows:

```python
if project.mode == ProjectMode.mv and shot.music_segment is not None:
    if shot.voice_refs:
        raise HTTPException(400, "MV music segments cannot be combined with Voice references")
    prepared = prepare_music_segment(project, shot.music_segment)
    audio_keys = ["music_audio_1"]
    inputs["music_audio_1"] = (prepared.filename, prepared.data)
    effective_duration_s = prepared.duration_s
    audio_count = 1
else:
    # Keep the existing voice_refs branch byte-for-byte in behavior.
```

Compute frames and Job `duration_s` from `effective_duration_s`. Perform preparation before `create_job`. Extend prompt freshness with `prompt_music_signature`; have `write_prompts_after_layout` tell the local model that the Job supplies the planned song excerpt as `<Audio 1>`. Keep `prompt_voice_signature` untouched.

- [ ] **Step 4: Run Task 3 tests and all H3/managed tests**

Run:

```powershell
py -3 -m pytest -q tests/test_mv_music_segments.py tests/test_projects_api.py tests/test_managed_h3_start_tool.py tests/test_h3_dialogue_contract.py tests/test_h3_ref2va_graph.py tests/test_managed_run_api.py tests/test_managed_run_continuation.py
```

Expected: all pass.

- [ ] **Step 5: Commit Task 3**

```powershell
git add backend/app/core/media/music_segments.py backend/app/api/projects.py backend/app/agents/director/service.py backend/tests/test_mv_music_segments.py backend/tests/test_projects_api.py backend/tests/test_managed_h3_start_tool.py backend/tests/test_h3_dialogue_contract.py
git commit -m "feat(mv): stage song segments during H3 submit"
```

### Task 4: Expose MV master and interval controls without changing normal UI

**Files:**
- Modify: `frontend/src/shared/api/types.ts`
- Modify: `frontend/src/features/director/api.ts`
- Create: `frontend/src/features/director/MusicMasterControl.tsx`
- Create: `frontend/src/features/director/MusicMasterControl.test.tsx`
- Modify: `frontend/src/features/director/DirectorPage.tsx`
- Modify: `frontend/src/features/production/ProductionPage.tsx`
- Modify: `frontend/src/features/production/ProductionPage.test.tsx`

**Interfaces:**
- Produces: TypeScript `ProjectMusicMaster` and `ShotMusicSegment` types.
- Produces: `uploadMusicMaster(projectId: string, file: File): Promise<Project>`.
- Produces: MV-only master import/replace control and four Shot interval inputs.
- Consumes: existing `updateProject`, `patchShot`, Project context refresh, Production Shot selection.

- [ ] **Step 1: Write failing UI tests**

Add tests:

```tsx
it("shows song import only for Music Video projects", () => {
  state.project = mvProject({ music_master: null });
  render(<MusicMasterControl />);
  expect(screen.getByLabelText("Song master")).toBeTruthy();
  cleanup();
  state.project = directorProject();
  render(<MusicMasterControl />);
  expect(screen.queryByLabelText("Song master")).toBeNull();
});


it("uploads one master and refreshes project state", async () => {
  render(<MusicMasterControl />);
  fireEvent.change(screen.getByLabelText("Song master"), {
    target: { files: [new File(["wav"], "song.wav", { type: "audio/wav" })] },
  });
  await waitFor(() => expect(uploadMusicMaster).toHaveBeenCalled());
  expect(refreshProjects).toHaveBeenCalled();
});
```

In `ProductionPage.test.tsx`, assert an MV Shot shows the four interval fields, saving calls `patchShot` with the structured object, clearing removes it, and a Director Shot renders none of those controls.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
npm test -- src/features/director/MusicMasterControl.test.tsx src/features/production/ProductionPage.test.tsx
```

Expected: component/type/import failures.

- [ ] **Step 3: Implement the MV-only controls**

Add the new types and multipart API call. Render `MusicMasterControl` only when `project.mode === "mv"`; show filename, duration, and Import/Replace action. In Production Shot details, render numeric inputs only in MV mode, with labels `Core start`, `Core end`, `Submit start`, and `Submit end`. Save one complete object only after client-side ordering/containment validation; the backend remains authoritative.

Do not add any song UI to Director or JSON Production mode. Do not modify the existing Voice reference editor.

- [ ] **Step 4: Run UI tests and production build**

Run:

```powershell
npm test -- src/features/director/MusicMasterControl.test.tsx src/features/production/ProductionPage.test.tsx
npm run build
```

Expected: tests and build pass.

- [ ] **Step 5: Commit Task 4**

```powershell
git add frontend/src/shared/api/types.ts frontend/src/features/director/api.ts frontend/src/features/director/MusicMasterControl.tsx frontend/src/features/director/MusicMasterControl.test.tsx frontend/src/features/director/DirectorPage.tsx frontend/src/features/production/ProductionPage.tsx frontend/src/features/production/ProductionPage.test.tsx
git commit -m "feat(mv): add song master and segment controls"
```

### Task 5: Update MV guidance and verify the real Agent workflow

**Files:**
- Modify: `backend/app/agents/director/guides/music-video-planning.md`
- Modify: `backend/app/agents/director/stage_guides.py`
- Test: `backend/tests/test_director_skill_loading.py`
- Test data only: `data/projects/prj_example/`

**Interfaces:**
- Produces: concise MV guidance that tells the Agent to author timestamps and never create Voice assets for song excerpts.
- Consumes: `music_segment` planning schema, music master import endpoint, canonical submit path, `start_h3_video`.

- [ ] **Step 1: Write the failing guide contract test**

Add assertions that the MV guide contains `music_segment`, distinguishes core and submit intervals, says audio is prepared during submission, and does not contain the obsolete claim that source audio is not attachable or should be replaced with a Voice asset.

- [ ] **Step 2: Run the guide test and verify RED**

Run:

```powershell
py -3 -m pytest -q tests/test_director_skill_loading.py -k music_video
```

Expected: failure on the obsolete guide language.

- [ ] **Step 3: Update the injected guide**

Replace the fallback language with the exact contract:

```text
Set music_segment only for face-readable song speech or singing. core_start_s/core_end_s
protect the edit content; submit_start_s/submit_end_s add generation handles. The canonical
H3 submit path extracts the interval from the Project song master as Audio 1. Do not create
or request a Voice Library asset for a song excerpt.
```

- [ ] **Step 4: Run automated suites**

Run:

```powershell
py -3 -m pytest -q
npm test
npm run build
```

Expected: backend and frontend suites pass; only already documented deprecation warnings may remain.

- [ ] **Step 5: Exercise the copied test project**

Using project `prj_example` in the `feature/mv-mode` worktree:

1. Import `C:\sample-media\song-master.wav` once through the new endpoint.
2. Clear Shot 1 `voice_refs` and set `music_segment` to core `0.000–1.160`, submit `0.000–2.000` (the master boundary prevents pre-roll).
3. Give the local Agent only: `请自行刷新 Shot 1 的 prompt，并生成这个镜头。`
4. Verify the resulting Job has `music_audio_1.wav`, 32 kHz stereo, approximately 2.0 seconds, and no `voice_audio_1`.
5. Manually resubmit Shot 1 and verify it stages the same interval through the same endpoint.
6. Submit one Shot with no `music_segment` and verify its Job has no song audio key.

- [ ] **Step 6: Commit Task 5**

```powershell
git add backend/app/agents/director/guides/music-video-planning.md backend/app/agents/director/stage_guides.py backend/tests/test_director_skill_loading.py
git commit -m "docs(mv): guide agents through lazy song segments"
```

- [ ] **Step 7: Record final evidence**

Report the exact backend/frontend test totals, build result, real Job IDs, staged audio metadata, and any remaining limitation. Do not commit copied Project data or generated outputs.
