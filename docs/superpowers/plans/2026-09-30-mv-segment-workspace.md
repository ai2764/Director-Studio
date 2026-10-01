# MV Segment Workspace Implementation Plan

> **Scope update (2026-09-30):** The user narrowed the current implementation to two UI surfaces and internal segment normalization: song upload in Assets; an MV Music page for text import, correction, song map, selection, and Director discussion; project-local JSON for the saved segments. Selected saved segments are passed to Agent chat without changing `script_text`. The Shot provenance, revision history, Production status, and H3 workflow tasks below are deferred and are not part of this implementation.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let MV projects upload the original song in Assets, import externally prepared timed lyrics through one flexible entry, and plan and discuss Shots against selected song segments.

**Architecture:** Keep the song as the existing project music master and store a revisioned segment document beside it. Give Director chat a validated, structured segment selection and record segment provenance on Shots, while retaining the current `music_segment` field solely for H3 job audio. Add an MV-only Music workspace and move the upload control into Assets on both layouts.

**Tech Stack:** FastAPI, Pydantic, JSON project storage, existing configured LLM provider, React 19, TypeScript, Vitest, pytest. No transcription package or new service.

**Spec:** `docs/superpowers/specs/2026-09-30-mv-segment-workspace-design.md`

## Global Constraints

- `mode == "mv"` exposes **Assets → Music → Director → Production**; other modes retain current navigation.
- Assets Music is the sole upload and replacement location for the Project song master. Do not add it to Voice Library.
- One **Import segments** panel accepts pasted text or bounded text files. The existing LLM proposes records; a separate deterministic validator decides what can be saved.
- The original song controls source audio and timing. Imported words are editable working text; do not install Whisper or auto-transcribe.
- Segment IDs remain stable across ordinary edits, each save creates a recoverable revision, and Shot links store content signatures separately from H3 `music_segment` intervals.
- Partial coverage and gaps are allowed; overlaps and duplicate ranges require explicit user review. Missing or out-of-range times cannot be saved.
- Old MV projects and non-MV Director/JSON Production projects continue to load. Do not auto-migrate freeform scripts, delete Jobs, retime Shots, or rewrite prompts.
- Custom H3 profiles must prove their Audio 1 route with a one-audio boundary validation before an MV singing Shot starts; fixed workflow audio is never silently substituted for the Project master.
- H3's three audio positions are generic ordered references; the MV song excerpt occupies Audio 1 by DS policy, and final edit audio comes from the original master.
- For custom H3 workflows, this phase supports the H3 `ref_audios` route. Do not rebind standalone `LoadAudio` nodes.
- Preserve unrelated working-tree files. Stage only files from the task; run the repository's staged-secret check before each commit.

## Review Focus

- A transcript with only a few timed lines in a long song saves and renders blank time between lines; pin in Tasks 2 and 5.
- Duplicate or overlapping source ranges remain visible in preview but fail save with row-specific errors; pin in Tasks 2 and 3.
- Replacing a master with the same duration but different bytes marks old segments and linked Shots stale; pin in Task 2.
- Chat selection from another project, a missing ID, or an older revision returns a conflict before reaching the LLM; pin in Task 6.
- A legacy MV Shot with no segment links still loads and H3 uses its existing `music_segment`; pin in Tasks 1 and 7.
- A custom H3 graph whose direct Audio 1 route is absent or invalid can still run a no-audio cutaway, while a singing Shot fails before a Job starts; pin in Task 8.

---

### Task 1: Segment models and revision store

**Files:**
- Modify: `backend/app/core/projects/models.py` (add `ShotSegmentLink` and optional Shot links)
- Create: `backend/app/core/projects/song_segments.py` (document types, signatures, validation, revisions)
- Test: `backend/tests/test_song_segment_store.py`

**Interfaces:**
- Produces: `SongSegment(id: str, start_s: float, end_s: float, text: str)`, `SongSegmentDocument(revision: int, master_sha256: str, raw_input: str, segments: list[SongSegment])`, `ShotSegmentLink(segment_id: str, signature: str)`.
- Produces: `segment_signature(segment: SongSegment) -> str`, `load_song_segments(project_id: str) -> SongSegmentDocument | None`, `save_song_segments(project: Project, *, expected_revision: int, raw_input: str, segments: list[SongSegment]) -> SongSegmentDocument`, `linked_shot_needs_review(shot: Shot, document: SongSegmentDocument | None, master_sha256: str) -> bool`.

- [ ] **Step 1: Write failing model and storage tests.** In the new test file use a temporary `settings.projects_dir`; create an MV project and master metadata, then assert a first save is revision 1, an edit preserving `seg_a` becomes revision 2, both `music/segments.v1.json` and `music/segments.json` exist, and a link to `seg_a` becomes stale after its text changes. Also assert `Shot.model_validate` accepts an old Shot JSON without `segment_links`.

```python
first = save_song_segments(project, expected_revision=0, raw_input="0-2 hello", segments=[SongSegment(id="seg_a", start_s=0, end_s=2, text="hello")])
second = save_song_segments(project, expected_revision=1, raw_input="0-2 hallo", segments=[SongSegment(id="seg_a", start_s=0, end_s=2, text="hallo")])
assert (first.revision, second.revision) == (1, 2)
assert (project_dir(project.id) / "music/segments.v1.json").exists()
assert load_song_segments(project.id) == second
```

- [ ] **Step 2: Verify the test fails.** Run `python -m pytest backend/tests/test_song_segment_store.py -q` from repository root; expect an import error for `song_segments`.
- [ ] **Step 3: Implement the store.** Canonical signatures use `json.dumps({"id", "start_s", "end_s", "text"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` hashed with SHA-256. Inside `_project_lock(project.id)`, check the current revision, write the next document atomically with `_atomic_model_write`, and preserve the prior document at `segments.v{revision}.json`. Reject non-MV projects and repeated IDs. Add `segment_links: list[ShotSegmentLink] = Field(default_factory=list)` to `Shot`; treat a missing link as a legacy Shot, not stale.

```python
class ShotSegmentLink(BaseModel):
    segment_id: str
    signature: str

class SongSegmentDocument(BaseModel):
    revision: int = Field(ge=1)
    master_sha256: str
    raw_input: str
    segments: list[SongSegment]
```

- [ ] **Step 4: Verify pass and commit.** Run the same pytest target; then `python scripts/check_staged_secrets.py` after staging exactly these three files and commit `feat: store revisioned MV song segments`.

### Task 2: Deterministic bounds and stale-state rules

**Files:**
- Modify: `backend/app/core/projects/song_segments.py`
- Test: `backend/tests/test_song_segment_store.py`

**Interfaces:**
- Produces: `validate_song_segments(segments: list[SongSegment], duration_s: float) -> list[SegmentIssue]`, where `SegmentIssue(row: int, code: str, message: str)`; nonempty issues block save.
- Produces: `song_segments_stale(project: Project, document: SongSegmentDocument | None) -> bool` and `linked_shot_needs_review(...)` used by APIs and UI.

- [ ] **Step 1: Write failing boundary tests.** Test a 325.12-second master with only rows `0–2` and `300–325.12` succeeds, with no synthesized gap segment. Test `NaN`, negative start, zero length, end beyond 325.12, reversed input order, duplicate range, and overlap each return a row-specific issue; pass the same rows to `save_song_segments` and verify no revision change. Test a master hash replacement with unchanged duration returns `song_segments_stale(...) is True` and marks a linked Shot for review.

```python
assert validate_song_segments([SongSegment(id="a", start_s=0, end_s=2, text="a"), SongSegment(id="b", start_s=300, end_s=325.12, text="b")], 325.12) == []
assert "overlap" in {issue.code for issue in validate_song_segments([SongSegment(id="a", start_s=0, end_s=3, text="a"), SongSegment(id="b", start_s=2, end_s=4, text="b")], 325.12)}
```

- [ ] **Step 2: Verify failure.** Run `python -m pytest backend/tests/test_song_segment_store.py -q`; expect the new assertions to fail.
- [ ] **Step 3: Implement validation and stale checks.** Reject non-finite times with `math.isfinite`, compare every row to the master duration, and compare adjacent rows for order, overlaps, and duplicate ranges without merging them. In `save_song_segments`, call this validator before the first write. Determine stale state by master SHA-256 and by comparing each stored Shot link signature to the matching current segment; unrelated links stay current. Keep a missing document readable.

```python
if not math.isfinite(segment.start_s) or not math.isfinite(segment.end_s):
    issues.append(SegmentIssue(row=index, code="non_finite", message="Enter a finite time"))
if document.master_sha256 != project.music_master.content_sha256:
    return True
```

- [ ] **Step 4: Verify pass and commit.** Run the target pytest file; stage only the two task files, run `python scripts/check_staged_secrets.py`, and commit `feat: validate MV segments against song master`.

### Task 3: One import preview and segment API

**Files:**
- Create: `backend/app/agents/director/segment_import.py` (LLM conversion only)
- Create: `backend/app/api/song_segments.py` (preview, load, save, correct, master playback)
- Modify: `backend/app/api/__init__.py` (include router)
- Test: `backend/tests/test_song_segment_api.py`

**Interfaces:**
- Produces: `parse_segment_text(raw_input: str, provider: DirectorLLMPlanProvider) -> SegmentPreview`, with `SegmentPreview.rows: list[SegmentDraft]` and `unresolved: list[str]`; `SegmentDraft.start_s/end_s` may be `None` in preview only.
- HTTP: `GET /api/projects/{id}/song-segments` returns `{document, master_stale, linked_shots}` (`linked_shots` contains Shot ID, status, segment IDs, and `needs_review`); `POST /api/projects/{id}/song-segments/preview` body `{raw_input}`; `PUT /api/projects/{id}/song-segments` body `{expected_revision, raw_input, segments}`; `PATCH /api/projects/{id}/song-segments/{segment_id}` body `{expected_revision, start_s, end_s, text}`; `GET /api/projects/{id}/music-master/audio` streams the resolved original master.

- [ ] **Step 1: Write failing API tests.** Use a fake configured LLM response containing JSON rows, and submit both Whisper-shaped JSON and human prose through the same preview endpoint. Assert the LLM sees input as quoted data and no new dependency or transcription process runs. Test that preview with a missing time returns an unresolved row, not an invented time; a malformed LLM response returns a clear 502 and leaves saved revision unchanged. Test save rejects overlap and non-MV mode with 422/404, stale `expected_revision` with 409, and missing master with 409. Test an explicit PATCH changes only one stable ID. Test GET returns a document and per-Shot stale flags without mutating a Shot. Test audio endpoint serves master bytes and rejects another project's path.

```python
response = client.post(f"/api/projects/{project.id}/song-segments/preview", json={"raw_input": "[00:04.54] Yodelie"})
assert response.status_code == 200
assert response.json()["rows"][0]["text"] == "Yodelie"
assert load_song_segments(project.id) is None
```

- [ ] **Step 2: Verify failure.** Run `python -m pytest backend/tests/test_song_segment_api.py -q`; expect 404 for the new route.
- [ ] **Step 3: Implement parser and routes.** Bound UTF-8 input to 256 KiB and reject NUL/binary-like content. Call `DirectorLLMPlanProvider.complete_bounded` with a strict JSON schema, instructions to preserve explicit wording and timestamps and return `null` for unknown time, and an output budget sufficient for 71 sentence rows. Validate response shape with Pydantic; never execute imported text as instructions. Preview invokes the LLM only on POST. `PUT` uses Task 2's deterministic validator; `PATCH` reuses it after changing one ID. Guard all routes with `project.mode == ProjectMode.mv`, and stream `resolve_music_master(project)` with an audio media type.

```python
class SegmentDraft(BaseModel):
    start_s: float | None = None
    end_s: float | None = None
    text: str

class SegmentPreview(BaseModel):
    rows: list[SegmentDraft]
    unresolved: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Verify pass and commit.** Run the API and store pytest files; stage only listed files, run `python scripts/check_staged_secrets.py`, and commit `feat: preview and save imported MV segments`.

### Task 4: Move song upload to Assets and add MV navigation

**Files:**
- Modify: `frontend/src/app/navigation.ts`, `frontend/src/app/App.tsx`, `frontend/src/app/App.test.tsx`
- Modify: `frontend/src/features/assets/AssetWorkspace.tsx`, `frontend/src/features/assets/MobileAssetWorkspace.tsx`, `frontend/src/features/assets/AssetWorkspace.test.tsx`
- Modify: `frontend/src/features/assets/MobileAssetWorkspace.test.tsx`, `frontend/src/shared/styles.css`
- Move: `frontend/src/features/director/MusicMasterControl.tsx` to `frontend/src/features/assets/MusicMasterControl.tsx` (keep `uploadMusicMaster` API reuse)
- Modify: `frontend/src/features/director/DirectorPage.tsx`, `frontend/src/features/director/DirectorPage.test.tsx`
- Create: `frontend/src/features/music/MusicPage.tsx` (compilable initial shell; Task 5 fills it)

**Interfaces:**
- Produces: `navItemsForMode(mode: ProjectMode | undefined): NavItem[]`; MV gets four stages and other modes get existing three.
- Produces: `MusicMasterControl` rendered once by Assets Music on desktop/mobile; its existing upload API remains the source of project master updates.
- Produces: `AssetWorkspace` and `MobileAssetWorkspace` accept `categoryRequest?: {category: "music"; nonce: number} | null`; the incrementing nonce lets App route every “Manage song” action directly to Assets Music, including repeat visits.

- [ ] **Step 1: Write failing UI tests.** Assert MV nav order is Assets, Music, Director, Production; Director mode retains Assets, Director, Production; JSON Production retains its current mode notice. Render MV Assets desktop/mobile and assert Music category contains `Import song` or `Replace song`; render MV Director and assert no song upload input. Assert non-MV Assets hides Music category.

```tsx
expect(navItemsForMode("mv").map((item) => item.id)).toEqual(["assets", "music", "director", "production"]);
expect(navItemsForMode("director").map((item) => item.id)).toEqual(["assets", "director", "production"]);
```

- [ ] **Step 2: Verify failure.** Run `npm test -- --run src/app/App.test.tsx src/features/assets/AssetWorkspace.test.tsx src/features/director/DirectorPage.test.tsx` from `frontend`; expect missing Music navigation/category assertions.
- [ ] **Step 3: Implement shell and upload relocation.** Add `"music"` to `NavId`, build stage numbers from the mode-specific array, and create a compilable `MusicPage` with a heading and a Manage song action; Task 5 replaces its body. Add a Music category only when `project.mode === "mv"`; render the moved control there in both asset layouts. Have App route Manage song to Assets with an incremented `categoryRequest` nonce, and update both asset layouts' local category state when that nonce changes. Remove the control's import and JSX from Director. Keep both desktop and mobile views wired to the same project refresh behavior.

```tsx
export function navItemsForMode(mode?: ProjectMode): NavItem[] {
  const ids: NavId[] = mode === "mv" ? ["assets", "music", "director", "production"] : ["assets", "director", "production"];
  return ids.map((id, index) => ({ id, label: id[0].toUpperCase() + id.slice(1), step: String(index + 1).padStart(2, "0") }));
}

useEffect(() => {
  if (project?.mode === "mv" && categoryRequest?.category === "music") setCategory("music");
}, [categoryRequest?.nonce, project?.mode]);
```

- [ ] **Step 4: Verify pass and commit.** Run those Vitest files and `npm run build`; stage only listed files, run `python scripts/check_staged_secrets.py` from repository root, and commit `feat: put MV song upload in Assets`.

### Task 5: Music workspace, preview editor, and song map

**Files:**
- Create: `frontend/src/features/music/api.ts`, `frontend/src/features/music/MusicPage.test.tsx`, `frontend/src/features/music/music.css`
- Modify: `frontend/src/features/music/MusicPage.tsx`
- Modify: `frontend/src/app/App.tsx` (wire the full `MusicPage`)
- Modify: `frontend/src/shared/api/types.ts` (segment document and preview types)

**Interfaces:**
- Produces: `MusicPage({onManageSong}: {onManageSong: () => void})`, with internal `SegmentSelection = {segmentIds: string[]; revision: number}` state. Task 7 renders the existing Director chat in its work area with that selection.
- Consumes Task 3 HTTP routes; uses the song audio endpoint for playback. Supplies Task 7's chat connection with selected IDs/revision, not a freeform prompt prefix.

- [ ] **Step 1: Write failing UI tests.** Verify opening Import segments does not fetch preview; pasted prose and an uploaded UTF-8 `.json` file populate the same text box and invoke the same preview API. A file over 256 KiB or binary text shows an error before POST. Preview rows support time/text corrections; save sends precisely the edited rows and raw input. A 325.12-second song with two distant rows shows a visible unannotated gap, and selecting adjacent rows sends both IDs but nonadjacent rows cannot form one selection. A missing master shows a link to Assets Music and disables final save without discarding draft text. Playback seeks to the selected start and can stop at its end.

```tsx
await user.click(screen.getByRole("button", { name: "Import segments" }));
expect(fetchMock).not.toHaveBeenCalledWith(expect.stringContaining("/preview"), expect.anything());
await user.type(screen.getByLabelText("Segment source text"), "0:04.54 Yodelie");
await user.click(screen.getByRole("button", { name: "Preview segments" }));
expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/song-segments/preview"), expect.anything());
```

- [ ] **Step 2: Verify failure.** Run `npm test -- --run src/features/music/MusicPage.test.tsx` from `frontend`; expect missing module.
- [ ] **Step 3: Implement the UI.** Fetch the current document and `linked_shots` from Task 3's GET route on project change. Keep `rawInput`, editable preview, errors, and selection as separate state. Decode text with `TextDecoder("utf-8", {fatal: true})` and impose the same 256 KiB cap. Use percent positions from source seconds divided by master duration; render both proportional blocks and an accessible list. Derive each block's progress from returned Shot status; show stale status from the API. Use the native audio element with `/api/projects/{id}/music-master/audio`, seek on selection, and stop range playback at selected end. Preserve typed text when preview or network calls fail.

```ts
export interface SegmentSelection { segmentIds: string[]; revision: number }
export interface SongSegment { id: string; start_s: number; end_s: number; text: string }
export interface SongSegmentDocument { revision: number; master_sha256: string; raw_input: string; segments: SongSegment[] }
```

- [ ] **Step 4: Verify pass and commit.** Run the Music Vitest file and `npm run build`; stage only listed files, run `python scripts/check_staged_secrets.py`, and commit `feat: add MV segment workspace`.

### Task 6: Scope Director Agent planning to selected segments

**Files:**
- Modify: `backend/app/api/projects.py` (JSON and image stream selection fields)
- Modify: `backend/app/agents/director/chat_orchestrator.py`, `backend/app/agents/director/chat_context.py`, `backend/app/agents/director/service.py`, `backend/app/agents/director/tool_handlers/project.py`, `backend/app/agents/director/planner.py`
- Modify: `backend/app/agents/director/tool_schema.py`, `backend/app/agents/director/tool_execution.py`, `backend/app/agents/director/brief.py`, `backend/app/agents/director/asset_catalog.py`
- Create: `backend/app/agents/director/segment_scope.py` (selection validation, planning source/hash, links)
- Modify: `backend/app/agents/director/guides/music-video-planning.md`
- Test: `backend/tests/test_mv_segment_chat.py`

**Interfaces:**
- Produces: `resolve_segment_scope(project: Project, segment_ids: list[str], revision: int) -> SegmentScope`; `SegmentScope` contains selected records, adjacent context, master identity/duration, and linked Shots; `planning_source_hash(project: Project, scope: SegmentScope | None) -> str`; `links_for_scope(scope: SegmentScope) -> list[ShotSegmentLink]`. Hash includes creative direction, master hash, and selected segment signatures, but not the whole document revision; an unrelated segment edit must not stale this batch.
- Chat accepts `segment_ids: list[str]` and `segment_revision: int | None` in JSON, and the same fields (JSON list plus revision string) in image FormData. A response remains the existing SSE contract.

- [ ] **Step 1: Write failing scope and integration tests.** Check MV selection IDs are unique, belong to the project, appear in source order, and are adjacent; missing or old revision returns 409 before LLM work. Check a different project cannot supply its IDs. Check a new MV project with empty `script_text` but valid selected segments can reach asset review and save two Shots linked to one segment. Verify the existing Director mode still requires its script. Verify chat does not advance to unselected segments and imported text is serialized as data, not instructions. Verify image stream carries the same selection. Add a correction request test showing it proposes a change and only an explicit correction tool/save mutates the segment document.

```python
scope = resolve_segment_scope(project, ["seg_a", "seg_b"], revision=2)
assert [row.id for row in scope.selected] == ["seg_a", "seg_b"]
assert len(links_for_scope(scope)) == 2
with pytest.raises(SegmentRevisionConflict):
    resolve_segment_scope(project, ["seg_a"], revision=1)
```

- [ ] **Step 2: Verify failure.** Run `python -m pytest backend/tests/test_mv_segment_chat.py -q`; expect missing `segment_scope` import.
- [ ] **Step 3: Implement validated chat scope.** Resolve selection before `orchestrate_chat`; pass `SegmentScope` through context construction and tool dispatch. Add the explicit correction tool to `tool_schema.py`, dispatch it through `tool_execution.py`/`tool_handlers/project.py`, and require a user's correction instruction. Scope is a structured object, never only text prepended to the user's message. For MV with scope, use `planning_source_hash` for asset coverage and the preflight and commit-time expected hash in `brief.py`, `asset_catalog.py`, `append_shot`, `save_storyboard`, and project tool handlers; leave non-MV script hashing unchanged. Let Shot drafts carry `segment_ids`, bind `segment_links` from current signatures at save, and reject IDs outside scope. Keep batch size 1–10 and require a new user request for another selection. A correction calls Task 3's store path and reports affected Shot links; no implicit lyric rewrite. Update `music-video-planning.md` with source-song authority, multiple Shots per selection, and face-readable Audio 1 criteria.

```python
class SegmentScope(BaseModel):
    revision: int
    master_sha256: str
    master_duration_s: float
    selected: list[SongSegment]
    neighboring: list[SongSegment]
    linked_shots: list[Shot]

def planning_source_hash(project: Project, scope: SegmentScope | None) -> str:
    if scope is None:
        return _script_hash(project.script_text or "")
    payload = {"script_text": project.script_text or "", "master_sha256": scope.master_sha256,
               "segments": [segment_signature(item) for item in scope.selected]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
```

- [ ] **Step 4: Verify pass and commit.** Run the new test, existing Director chat/planner/service tests identified with `rg --files backend/tests | rg '(director|chat|mv)'`, and the song API tests. Stage listed files, run `python scripts/check_staged_secrets.py`, and commit `feat: scope MV Director planning to song segments`.

### Task 7: Connect Music discussion and Production Shot provenance

**Files:**
- Modify: `frontend/src/features/music/MusicPage.tsx`, `frontend/src/features/music/MusicPage.test.tsx`
- Modify: `frontend/src/features/director/DirectorPage.tsx`, `frontend/src/features/director/api.ts`, `frontend/src/features/director/api.test.ts`, `frontend/src/features/director/DirectorPage.test.tsx`
- Modify: `frontend/src/features/production/ProductionPage.tsx`, `frontend/src/features/production/ProductionPage.test.tsx`
- Modify: `frontend/src/shared/api/types.ts`
- Modify: `backend/app/api/projects.py` (block stale H3 submission)
- Modify: `backend/app/api/song_segments.py` (explicit Shot source-review endpoint)
- Test: `backend/tests/test_mv_segment_production.py`

**Interfaces:**
- `DirectorPage` accepts optional `segmentSelection: SegmentSelection | null` and displays it in the chat header; `chatWithDirectorStream` accepts the same optional final argument and serializes it into either JSON or image FormData.
- Production reads `Shot.segment_links` and derived stale state; its editing path preserves `Shot.music_segment` and the current H3 master audio behavior. `POST /api/projects/{id}/shots/{shot_id}/segment-review` with `{expected_revision, expected_shot_hash}` rebinds that Shot's links to the current segment signatures after explicit user review; it returns 409 if the Shot, revision, or master changed.

- [ ] **Step 1: Write failing connected-flow tests.** In Music, select a segment, send a message, and assert request JSON contains IDs and revision. Repeat with a chat image and check FormData. Ensure switching selection updates the header without silently sending chat. In Production, render a linked singing Shot and a linked cutaway: both display source segment coverage, only the singing Shot with `music_segment` submits Audio 1, and a corrected link shows “Needs source review” without altering existing Job output. Backend tests assert a stale linked Shot cannot start a new H3 Job, explicit review updates only its link signatures, and a concurrent Shot edit/revision/master replacement yields 409. Load an old MV Shot with no `segment_links` and assert it still renders and its existing H3 audio interval is unchanged.

```tsx
expect(JSON.parse(String(fetchBody))).toMatchObject({ segment_ids: ["seg_a"], segment_revision: 2 });
expect(screen.getByText("Needs source review")).toBeTruthy();
```

- [ ] **Step 2: Verify failure.** From `frontend`, run `npm test -- --run src/features/director/api.test.ts src/features/director/DirectorPage.test.tsx src/features/music/MusicPage.test.tsx src/features/production/ProductionPage.test.tsx`; from repository root run `python -m pytest backend/tests/test_mv_segment_production.py -q`. Expect missing selection fields/status.
- [ ] **Step 3: Implement the connection.** Render `<DirectorPage chatOnly segmentSelection={selection} />` in Music's segment work area. Disable a send when selected revision became stale, and show exact selected times/lyrics in the chat header. Serialize selection through both request paths. In Production, show source segment IDs, current text/time, and signature/master mismatch as an explicit review badge. Let the user inspect/edit prompt and `music_segment` timing, then explicitly confirm source review, which updates only that Shot's link signatures with revision and Shot-hash guards. Block new H3 submission while a linked Shot or its segment document is stale; leave legacy unlinked Shots on the existing path. Do not add song audio based solely on a segment link.

```ts
if (segmentSelection) {
  payload.segment_ids = segmentSelection.segmentIds;
  payload.segment_revision = segmentSelection.revision;
}
```

- [ ] **Step 4: Verify pass and commit.** Run all affected Vitest files, `npm run build`, and the MV backend tests. Stage listed files, run `python scripts/check_staged_secrets.py`, and commit `feat: connect MV segment discussion to Shots`.

### Task 8: Verify existing custom H3 audio mapping before queueing

**Files:**
- Modify: `backend/app/workflow_profiles/h3/validator.py`, `backend/app/pipelines/h3_ref2va/pipeline.py`
- Test: `backend/tests/test_h3_workflow_validator.py`, `backend/tests/test_h3_profile_runtime.py`, `backend/tests/test_mv_segment_production.py`

**Interfaces:**
- Keeps the inspector's existing `audio_input_pattern="ref_audios.ref_audio_{index}"` mapping for a confirmed official Ref2AV node and the filler's existing Audio 1–3 support. Produces: `validate_h3_audio_route(profile: ResolvedH3Profile) -> ValidationReport`; it exercises `fill_profile_graph` with one Picture and one Audio and checks that Audio 1 resolves to the expected uploaded name on the mapped H3 input. A graph without a valid audio route is still valid for zero-audio Shots.
- Local H3 queue preparation checks this capability against its captured profile snapshot when `audio_keys` contains the prepared song excerpt; a failing route returns a clear submission error before the Job enters the queue. The existing `prepare_music_segment` result remains the uploaded Audio 1 bytes.

- [ ] **Step 1: Write failing graph and submission tests.** Use a confirmed official Ref2AV node with the existing `ref_audios.ref_audio_{index}` mapping; assert one prepared master excerpt enters Audio 1. Assert three Voice-only references enter Audio 1–3 in order and a fourth fails, preserving current behavior. Include a separate fixed `LoadAudio` node and assert it is not substituted for the master. Remove the dynamic audio mapping and assert no-audio fill remains valid while an MV singing submission fails before the Job enters the queue. A graph that claims an invalid Audio 1 mapping reports a specific input issue during profile validation.

```python
report = validate_h3_audio_route(profile)
assert report.valid is True
filled = fill_profile_graph(profile, {"prompt": SAMPLE_PROMPT.replace("A", "<Audio 1> defines the song reference. A", 1), "images": ["picture.png"], "audios": ["master-excerpt.wav"], "frames": 56})
audio_node_id = filled[profile.mapping.inputs.h3_node_id]["inputs"]["ref_audios.ref_audio_0"][0]
assert filled[audio_node_id]["inputs"]["audio"] == "master-excerpt.wav"
```

- [ ] **Step 2: Verify failure.** Run `python -m pytest backend/tests/test_h3_workflow_validator.py backend/tests/test_h3_profile_runtime.py backend/tests/test_mv_segment_production.py -q`; expect the new route assertions to fail.
- [ ] **Step 3: Implement validation and preflight.** Keep inspector and graph fill behavior unchanged. In the validator, retain its zero-audio boundary check and add one-audio filling whenever the mapping claims audio support; report the mapped socket and failure, not a generic Comfy error. In `H3Ref2VAPipeline.prepare_job_submission`, first capture the existing immutable profile snapshot, then validate its Audio 1 route when `job.params.audio_keys` is nonempty; reject before queueing. Keep the current no-audio graph path for cutaways. Do not rebind standalone `LoadAudio` nodes.

```python
if job.params.get("audio_keys") and self.execution_adapter_id_for_job(job) == "comfy_mcp":
    report = validate_h3_audio_route(snapshot_profile_for_job(job))
    if not report.valid:
        raise ValueError("Active H3 workflow has no valid Audio 1 input")
```

- [ ] **Step 4: Verify pass and commit.** Run the three pytest files; stage only listed files, run `python scripts/check_staged_secrets.py`, and commit `feat: validate custom H3 song audio input`.

### Task 9: End-to-end acceptance and documentation

**Files:**
- Create: `backend/tests/test_mv_segment_acceptance.py`
- Create: `docs/mv-segment-workflow.md` (leave the user's existing `README.md` edit untouched)

**Interfaces:**
- Uses Tasks 1–8 public HTTP and UI contracts; introduces no new runtime API.

- [ ] **Step 1: Add acceptance tests using external fixtures without copying user media.** Build temporary 325.12-second Project master metadata. Test a small embedded Whisper-shaped JSON with optional `words`; when `C:\Users\AIBOX\dev\youtube-video-lab\tasks\anywhere_will_do_mv\audio\whisper.json` exists, also preview its 71 sentence rows. Assert boundaries validate and the master bytes/hash remain unchanged after text correction. Include human CSV-like prose through the same preview path and a non-MV API rejection.

```python
assert len(saved.segments) == len(fixture["segments"])
assert saved.master_sha256 == project.music_master.content_sha256
assert project.music_master.content_sha256 == original_hash
```

- [ ] **Step 2: Run the test and fix only concrete failures.** Run `python -m pytest backend/tests/test_mv_segment_acceptance.py -q`, `python -m pytest backend/tests/test_song_segment_store.py backend/tests/test_song_segment_api.py backend/tests/test_mv_segment_chat.py backend/tests/test_mv_segment_production.py -q`, then from `frontend` run `npm test -- --run` and `npm run build`. If a failure is unrelated or environment-specific, record the exact command/output rather than broadening scope.
- [ ] **Step 3: Document the workflow.** Explain Assets Music upload, the single Import segments panel, preview/edit/save, selecting segments for Agent discussion, Shot links versus generation audio, and the rule that final edit uses the original master. State explicitly that DS does not transcribe music and that corrections affect segment records rather than song audio.
- [ ] **Step 4: Verify and commit.** Inspect `git diff --check` and `git status --short`; stage only this task's files, run `python scripts/check_staged_secrets.py`, and commit `docs: explain MV segment workflow`.
