"""Runtime Director model selection (no restart)."""

from __future__ import annotations

from pathlib import Path
import json

import pytest

from app.core.vram import director_model as dm


@pytest.fixture(autouse=True)
def _isolate_model_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dm, "_override", None)
    monkeypatch.setattr(dm, "_persist_path", lambda: tmp_path / "director_model.json")
    yield
    monkeypatch.setattr(dm, "_override", None)


def test_missing_model_configuration_stays_empty(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dm.settings, "director_plan_model", "")
    assert dm.get_director_model() == ""
    st = dm.model_status()
    assert st["source"] == "env"
    assert st["model"] == ""


def test_set_persists_and_reads_back():
    name = dm.set_director_model("ornith:35b", persist=True)
    assert name == "ornith:35b"
    assert dm.get_director_model() == "ornith:35b"
    # Simulate process restart: clear in-memory override, keep file
    dm._override = None
    assert dm.get_director_model() == "ornith:35b"
    assert dm.model_status()["source"] == "persisted"


def test_runtime_override_beats_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dm.settings, "director_plan_model", "env-model:latest")
    dm.set_director_model("ornith:35b", persist=False)
    assert dm.get_director_model() == "ornith:35b"
    assert dm.model_status()["source"] == "runtime"


def test_model_selection_is_scoped_to_provider():
    dm.set_director_model(
        "qwen-local",
        provider_id="ollama",
        persist=True,
    )

    assert dm.get_director_model("ollama") == "qwen-local"
    assert dm.get_director_model("lm-studio") == ""
    assert json.loads(dm._persist_path().read_text(encoding="utf-8")) == {
        "provider": "ollama",
        "model": "qwen-local",
    }


def test_legacy_model_file_only_applies_to_ollama():
    dm._persist_path().write_text(
        json.dumps({"model": "legacy-qwen"}),
        encoding="utf-8",
    )

    assert dm.get_director_model("ollama") == "legacy-qwen"
    assert dm.get_director_model("openai-compatible") == ""


def test_provider_specific_status_hides_another_provider_selection():
    dm.set_director_model(
        "studio-model",
        provider_id="lm-studio",
        persist=True,
    )

    assert dm.model_status("lm-studio")["model"] == "studio-model"
    assert dm.model_status("lm-studio")["provider"] == "lm-studio"
    assert dm.model_status("ollama")["model"] == ""
    assert dm.model_status("ollama")["persisted"] is None
