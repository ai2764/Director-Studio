"""Error copy uses generic authored dialogue and synthetic model responses."""
import json
from types import SimpleNamespace
from pydantic import ValidationError
from app.core.projects.models import PromptSections
from app.core.projects.dialogue import DialogueIssue, DialogueContractError
from app.shot_workspace.errors import validation_reason


def test_dialogue_reason_identifies_language_and_words_without_response_dump():
    shot = SimpleNamespace(dialogue_lines=[SimpleNamespace(language="English", text="Welcome.")])
    raw = json.dumps({"prompt_sections": {"detailed_description": "<d>[French] Hello.</d>"}, "private_model_metadata": "must not appear"})
    error = DialogueContractError([DialogueIssue(code="dialogue_bindings_missing", actual=raw)])
    reason = validation_reason(error, raw, shot)
    assert "Dialogue line 1" in reason and "[English]" in reason and "[French]" in reason
    assert "spoken words differ" in reason and "must not appear" not in reason


def test_missing_dialogue_reason_reports_counts():
    shot = SimpleNamespace(dialogue_lines=[SimpleNamespace(language="English", text="Welcome.")])
    reason = validation_reason(DialogueContractError([DialogueIssue(code="dialogue_bindings_missing")]), '{"prompt_sections":{}}', shot)
    assert "Expected 1 saved dialogue line(s); found 0" in reason


def test_schema_reason_names_missing_field():
    try:
        PromptSections.model_validate({"subject_definitions": []})
    except ValidationError as error:
        reason = validation_reason(error, "", None)
    assert "subject_definitions" in reason and "valid string" in reason


def test_reference_validator_reason_retains_specific_index():
    assert "Picture 3" in validation_reason(ValueError("prompt must reference submitted <Picture 3>"), "", None)
