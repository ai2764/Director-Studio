"""Missing source metadata is resolved before creative prompt generation."""
import json

import pytest

from test_director_dialogue_attribution import authored_shot, Orchestrator, writer_sections
from app.agents.director.dialogue_preflight import prepare_dialogue, dialogue_contract_current
from app.agents.director.service import DirectorService
from app.core.projects.dialogue import apply_dialogue_update, DialogueContractError
from app.core.projects.store import load_shot, save_shot


def without_language(shot, text="Hello."):
    line = shot.dialogue_lines[0].model_dump()
    line.update(language="", text=text)
    changed = apply_dialogue_update(shot, {"dialogue_lines": [line]})
    save_shot(changed)
    return changed


def resolution(language="English", quote="Hello."):
    return {"languages": [{"line_id": "l1", "language": language,
        "evidence": [{"source_id": "line:l1", "quote": quote}]}], "unresolved": []}


class MetadataProvider:
    def __init__(self, response):
        self.response, self.requests = response, []

    async def complete(self, system, user, **kwargs):
        self.requests.append(json.loads(user) if user.startswith("{") else {"writer_request": user})
        return json.dumps(self.response, ensure_ascii=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("text,language", [("我这是在哪儿啊", "Chinese"),
    ("Olá… atenção: vem!", "Português"), ("Where are we?", "English")])
async def test_missing_language_is_completed_without_reauthoring_words(authored_shot, text, language):
    project, shot = authored_shot
    shot = without_language(shot, text)
    provider = MetadataProvider(resolution(language, text))
    lines = await prepare_dialogue(project, shot, provider)
    assert lines[0].language == language
    assert lines[0].model_dump(exclude={"language"}) == shot.dialogue_lines[0].model_dump(exclude={"language"})
    assert load_shot(project.id, shot.id) == shot


@pytest.mark.asyncio
async def test_metadata_question_does_not_offer_a_prompt_only_retry(authored_shot):
    from app.agents.director.prompt_retry import pending_prompt_retry
    project, shot = authored_shot
    shot = without_language(shot, "Ah!")
    provider = MetadataProvider({"languages": [], "unresolved": [
        {"line_id": "l1", "question": "Which language should this interjection use?"}]})
    with pytest.raises(DialogueContractError):
        await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert pending_prompt_retry(project.id) is None


@pytest.mark.asyncio
async def test_old_prompt_retry_is_blocked_when_source_needs_clarification(authored_shot):
    from app.agents.director.prompt_retry import pending_prompt_retry, record_prompt_failure, run_prompt_retry
    project, shot = authored_shot
    shot = without_language(shot, "Ah!")
    receipt = record_prompt_failure(shot, "", ValueError("Previous prompt failure"))
    provider = MetadataProvider({"languages": [], "unresolved": [
        {"line_id": "l1", "question": "Which language should this interjection use?"}]})
    with pytest.raises(DialogueContractError):
        await run_prompt_retry(project.id, receipt,
            DirectorService(plan_provider=provider, orchestrator=Orchestrator()))
    assert pending_prompt_retry(project.id) is None


@pytest.mark.asyncio
async def test_complete_metadata_never_calls_resolver(authored_shot):
    project, shot = authored_shot
    provider = MetadataProvider({})
    assert await prepare_dialogue(project, shot, provider) == shot.dialogue_lines
    assert provider.requests == []


@pytest.mark.asyncio
async def test_ambiguous_language_requests_clarification_before_writer(authored_shot):
    project, shot = authored_shot
    shot = without_language(shot, "Ah!")
    provider = MetadataProvider({"languages": [], "unresolved": [
        {"line_id": "l1", "question": "这句 Ah! 要用哪种语言？"}]})
    with pytest.raises(DialogueContractError) as error:
        await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert getattr(error.value, "code", None) == "DIALOGUE_CLARIFICATION_REQUIRED"
    assert "这句 Ah! 要用哪种语言？" in str(error.value)
    assert len(provider.requests) == 1
    assert load_shot(project.id, shot.id) == shot


@pytest.mark.asyncio
async def test_explicit_current_request_is_available_to_resolver(authored_shot):
    project, shot = authored_shot
    shot = without_language(shot, "Ah!")
    response = resolution("Chinese", "中文台词")
    response["languages"][0]["evidence"][0]["source_id"] = "current_request"
    provider = MetadataProvider(response)
    lines = await prepare_dialogue(project, shot, provider, revision_request="中文台词")
    assert lines[0].language == "Chinese"
    assert provider.requests[0]["sources"]["current_request"] == "中文台词"


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["unknown_id", "duplicate", "empty", "markup", "invented_evidence", "rewrite", "missing"])
async def test_invalid_metadata_never_reaches_writer_or_changes_shot(authored_shot, defect):
    project, shot = authored_shot
    shot = without_language(shot)
    response = resolution()
    item = response["languages"][0]
    if defect == "unknown_id": item["line_id"] = "other"
    if defect == "duplicate": response["languages"].append(dict(item))
    if defect == "empty": item["language"] = "  "
    if defect == "markup": item["language"] = "<d>Chinese</d>"
    if defect == "invented_evidence": item["evidence"][0]["quote"] = "Invented source"
    if defect == "rewrite": item["text"] = "Different words"
    if defect == "missing": response["languages"] = []
    provider = MetadataProvider(response)
    with pytest.raises(DialogueContractError) as error:
        await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert getattr(error.value, "code", None) == "DIALOGUE_METADATA_INVALID"
    assert len(provider.requests) == 2  # one bounded schema/evidence repair, no creative writer
    assert load_shot(project.id, shot.id) == shot


@pytest.mark.asyncio
async def test_resolved_metadata_produces_current_prompt_without_staling_authored_plan(authored_shot):
    from app.core.managed_runs.store import _fingerprint
    project, shot = authored_shot
    shot = without_language(shot)
    before = _fingerprint(project.id)

    class Provider(MetadataProvider):
        async def complete(self, system, user, **kwargs):
            if user.startswith("{") and "sources" in json.loads(user):
                return await super().complete(system, user, **kwargs)
            sections = writer_sections()
            sections["detailed_description"] = "0–6 seconds: {{speech:l1}}"
            return json.dumps({"prompt_sections": sections})

    provider = Provider(resolution())
    updated = await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert "<d>[English] Hello.</d>" in updated.prompt_sections.detailed_description
    assert dialogue_contract_current(project, updated)
    assert updated.dialogue_lines == shot.dialogue_lines
    assert _fingerprint(project.id) == before
    assert (await prepare_dialogue(project, updated, provider))[0].language == "English"
    assert len(provider.requests) == 1  # successful, source-current contract is reusable
    assert not dialogue_contract_current(project, updated.model_copy(update={"feedback": "Speak French."}))
    assert not dialogue_contract_current(project, updated.model_copy(update={"script_beat": "A different language context."}))
    changed = apply_dialogue_update(updated, {"dialogue_language_updates": [
        {"line_id": "l1", "language": "French"}]})
    save_shot(changed)
    assert not dialogue_contract_current(project, changed)
    assert (await prepare_dialogue(project, changed, provider))[0].language == "French"


@pytest.mark.asyncio
async def test_harness_surfaces_question_and_next_turn_still_allows_edits(authored_shot):
    from app.agents.director.harness_runtime import BackendTurn
    project, shot = authored_shot
    shot = without_language(shot, "Ah!")
    provider = MetadataProvider({"languages": [], "unresolved": [
        {"line_id": "l1", "question": "这句 Ah! 要用哪种语言？"}]})
    svc = DirectorService(plan_provider=provider, orchestrator=Orchestrator())
    turn = BackendTurn(project.id, "写提示词", svc, None)
    await turn.dispatch("context", {})
    result = await turn.dispatch("tool", {"name": "write_prompt",
        "arguments": {"shot_id": shot.id}, "call_id": "write-1"})
    assert result["code"] == "DIALOGUE_CLARIFICATION_REQUIRED"
    finished = turn.finish({"reply": "Done", "thinking": ""})
    assert finished.failure_code == "DIALOGUE_CLARIFICATION_REQUIRED"
    assert "这句 Ah! 要用哪种语言？" in finished.reply
    assert "bounded internal repair" not in finished.reply
    assert turn.context()["tools"] == []
    fresh = BackendTurn(project.id, "中文台词", svc, None)
    names = {tool["function"]["name"] for tool in fresh.context()["tools"]}
    assert "revise_shot" in names
    assert load_shot(project.id, shot.id) == shot


@pytest.mark.asyncio
async def test_metadata_transport_failure_cannot_become_prompt_retry(authored_shot):
    from app.agents.director.prompt_retry import pending_prompt_retry
    project, shot = authored_shot
    shot = without_language(shot)
    class OfflineProvider:
        async def complete(self, *args, **kwargs):
            raise TimeoutError("resolver offline")
    with pytest.raises(DialogueContractError) as error:
        await DirectorService(plan_provider=OfflineProvider(), orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert getattr(error.value, "code", None) == "DIALOGUE_METADATA_INVALID"
    assert pending_prompt_retry(project.id) is None


@pytest.mark.asyncio
async def test_one_bad_metadata_response_can_recover_before_writer(authored_shot):
    project, shot = authored_shot
    shot = without_language(shot)
    class Provider(MetadataProvider):
        async def complete(self, system, user, **kwargs):
            result = await super().complete(system, user, **kwargs)
            return "not json" if len(self.requests) == 1 else result
    provider = Provider(resolution())
    assert (await prepare_dialogue(project, shot, provider))[0].language == "English"
    assert len(provider.requests) == 2


@pytest.mark.asyncio
async def test_only_blank_languages_are_completed_in_multilingual_dialogue(authored_shot):
    project, shot = authored_shot
    first = shot.dialogue_lines[0].model_dump()
    second = {**first, "line_id": "l2", "speaker_id": "other", "language": "", "text": "Bonjour."}
    shot = apply_dialogue_update(shot, {"dialogue_lines": [first, second]})
    provider = MetadataProvider({"languages": [{"line_id": "l2", "language": "French",
        "evidence": [{"source_id": "line:l2", "quote": "Bonjour."}]}], "unresolved": []})
    lines = await prepare_dialogue(project, shot, provider)
    assert [line.language for line in lines] == ["English", "French"]
    assert lines[0] == shot.dialogue_lines[0]
    assert provider.requests[0]["missing_line_ids"] == ["l2"]


@pytest.mark.asyncio
async def test_user_edit_during_resolution_prevents_stale_publication(authored_shot):
    project, shot = authored_shot
    shot = without_language(shot)
    changed = apply_dialogue_update(shot, {"dialogue_language_updates": [{"line_id": "l1", "language": "French"}]})
    class EditingProvider(MetadataProvider):
        async def complete(self, *args, **kwargs):
            save_shot(changed)
            return await super().complete(*args, **kwargs)
    with pytest.raises(ValueError, match="changed"):
        await DirectorService(plan_provider=EditingProvider(resolution()), orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert load_shot(project.id, shot.id) == changed


@pytest.mark.parametrize("field,value", [("speaker_id", "forged"), ("text", "Different"), ("line_id", "other")])
def test_derived_language_overlay_does_not_authorize_other_changes(authored_shot, field, value):
    from app.agents.director.dialogue_metadata import verify_prepared_dialogue
    project, shot = authored_shot
    shot = without_language(shot)
    lines = [shot.dialogue_lines[0].model_copy(update={"language": "English", field: value})]
    with pytest.raises(DialogueContractError, match="source_changed"):
        verify_prepared_dialogue(project, shot, lines)


@pytest.mark.asyncio
async def test_managed_run_pauses_for_metadata_without_spending_prompt_retry(monkeypatch):
    from test_managed_run_continuation import _run
    from app.core.managed_runs import continuation
    from app.core.managed_runs.store import load_run
    from app.agents.director.chat_orchestrator import ChatResult
    run = _run()
    attempts = []
    async def question(current, svc):
        attempts.append(current.prompt_retry_count)
        return ChatResult(reply="Which language?", failure_code="DIALOGUE_CLARIFICATION_REQUIRED",
                          failure_message="Which language?", failure_kind="contract")
    monkeypatch.setattr(continuation, "_agent_turn", question)
    await continuation.continue_run(run.project_id, run.run_id)
    saved = load_run(run.project_id, run.run_id)
    assert attempts == [0]
    assert saved.state == "paused"
    assert saved.paused_reason == "Which language?"


@pytest.mark.asyncio
async def test_legacy_inferred_language_is_not_promoted_to_authored_setting(authored_shot):
    import hashlib
    from app.agents.director.brief import remember_directing_request
    project, shot = authored_shot
    line = shot.dialogue_lines[0].model_dump()
    line.update(language="", source={"kind": "script", "source_hash": hashlib.sha256(project.script_text.encode()).hexdigest(),
                                    "scene_id": shot.scene_id, "quote": "Visitor: Hello.", "occurrence": 0})
    shot = shot.model_copy(update={"dialogue_lines": None, "meta": {"dialogue_grounding": {"lines": [line]}}})
    save_shot(shot)
    class Provider(MetadataProvider):
        async def complete(self, system, user, **kwargs):
            if user.startswith("{") and "sources" in json.loads(user):
                return await super().complete(system, user, **kwargs)
            sections = writer_sections()
            sections["detailed_description"] = "0–6 seconds: {{speech:l1}}"
            return json.dumps({"prompt_sections": sections})
    first = await DirectorService(plan_provider=Provider(resolution()), orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    remember_directing_request(project.id, "Use British English for the visitor.")
    newer = resolution("British English", "Use British English for the visitor.")
    newer["languages"][0]["evidence"][0]["source_id"] = "request:0"
    provider = MetadataProvider(newer)
    lines = await prepare_dialogue(project, first, provider)
    assert lines[0].language == "British English"
    assert first.dialogue_lines is None


@pytest.mark.asyncio
async def test_unrelated_prompt_edit_retains_previous_explicit_language_evidence(authored_shot):
    from app.agents.director.dialogue_preflight import prompt_dialogue_record
    project, shot = authored_shot
    shot = without_language(shot, "Ah!")
    first_response = resolution("French", "Use French for Ah!")
    first_response["languages"][0]["evidence"][0]["source_id"] = "current_request"
    first = await prepare_dialogue(project, shot, MetadataProvider(first_response), revision_request="Use French for Ah!")
    record = prompt_dialogue_record(project, shot, first, None)
    shot = shot.model_copy(update={"meta": {"dialogue_grounding": record}})
    newer = resolution("French", "Use French for Ah!")
    newer["languages"][0]["evidence"][0]["source_id"] = "previous_request:0"
    provider = MetadataProvider(newer)
    lines = await prepare_dialogue(project, shot, provider, revision_request="Use a slower camera move.")
    assert lines[0].language == "French"
    assert provider.requests[0]["sources"]["previous_request:0"] == "Use French for Ah!"
    newer = resolution("Chinese", "改用中文")
    newer["languages"][0]["evidence"][0]["source_id"] = "current_request"
    changed = await prepare_dialogue(project, shot, MetadataProvider(newer), revision_request="改用中文")
    assert changed[0].language == "Chinese"


@pytest.mark.asyncio
async def test_repeated_explicit_language_choice_keeps_latest_order(authored_shot):
    project, shot = authored_shot
    shot = without_language(shot, "Ah!")
    shot.meta["dialogue_grounding"] = {"metadata": {"previous_requests": ["Use French", "Use Chinese"],
                                                       "revision_request": "Use French"}}
    response = resolution("French", "Use French")
    response["languages"][0]["evidence"][0]["source_id"] = "previous_request:1"
    provider = MetadataProvider(response)
    await prepare_dialogue(project, shot, provider, revision_request="Move camera closer")
    prior = [value for key, value in provider.requests[0]["sources"].items() if key.startswith("previous_request:")]
    assert prior == ["Use Chinese", "Use French"]


@pytest.mark.asyncio
async def test_managed_tail_metadata_question_does_not_retry_prompt(monkeypatch):
    from test_managed_run_continuation import _run
    from app.core.managed_runs import continuation
    from app.core.managed_runs.store import _save_run, load_run, bind_job, record_terminal
    from app.core.managed_runs.models import RunStep
    from app.core.projects.layouts import LayoutReference, LayoutReviewStatus
    from app.core.schemas import JobStatus
    from app.agents.director.dialogue_metadata import DialogueClarificationRequired
    from app.core.projects.dialogue import DialogueIssue
    run = _run()
    run = _save_run(run.model_copy(update={"steps": [RunStep(shot_id="sht_1"),
        RunStep(shot_id="sht_2", tail_from_shot_id="sht_1", tail_reason="Continue motion")]}))
    bind_job(run.project_id, run.run_id, "sht_1", "job_first")
    run = record_terminal(run.project_id, "job_first", JobStatus.succeeded)
    def extract(**kwargs):
        shot = load_shot(run.project_id, "sht_2")
        layout = LayoutReference(id="tail", purpose="continuity", review_status=LayoutReviewStatus.pending_review)
        save_shot(shot.model_copy(update={"layout_refs": [layout]}))
        return {"layout_ref_id": layout.id}
    class Service:
        async def write_prompts_after_layout(self, *args, **kwargs):
            raise DialogueClarificationRequired([DialogueIssue(code="dialogue_language_unresolved", action="Which language?")])
    attempts = []
    async def agent(*args):
        attempts.append(True)
        from app.agents.director.chat_orchestrator import ChatResult
        return ChatResult(reply="Unexpected attempt")
    monkeypatch.setattr(continuation, "extract_clip_tail_frame", extract)
    monkeypatch.setattr(continuation, "_agent_turn", agent)
    monkeypatch.setattr("app.agents.director.DirectorService", lambda **kwargs: Service())
    await continuation.continue_run(run.project_id, run.run_id)
    saved = load_run(run.project_id, run.run_id)
    assert saved.state == "paused"
    assert "Which language?" in saved.paused_reason
    assert saved.prompt_retry_count == 0
    assert attempts == []
