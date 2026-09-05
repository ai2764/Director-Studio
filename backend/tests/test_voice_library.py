from __future__ import annotations

import io
import json
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.projects.store import create_project


def _wav_bytes(*, duration_s: float, sample_rate: int = 16000, channels: int = 1) -> bytes:
    frames = int(duration_s * sample_rate)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\x00\x00" * frames * channels)
    return buffer.getvalue()


@pytest.fixture
def voice_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    projects = tmp_path / "projects"
    jobs = tmp_path / "jobs"
    library = tmp_path / "library"
    projects.mkdir()
    jobs.mkdir()
    library.mkdir()
    monkeypatch.setattr(settings, "projects_dir", projects)
    monkeypatch.setattr(settings, "jobs_dir", jobs)
    monkeypatch.setattr(settings, "library_root", library)
    return {"projects": projects, "jobs": jobs, "library": library}


@pytest.fixture
def client(voice_env):
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def test_import_voice_reference_creates_h3_ready_derivative(client, voice_env):
    project = create_project("Voice project", "Mia whispers.")

    response = client.post(
        "/api/library/import",
        data={
            "kind": "voices",
            "name": "Mia",
            "notes": "Warm neutral English, intimate delivery",
            "project_id": project.id,
        },
        files={"file": ("mia.wav", _wav_bytes(duration_s=2.1), "audio/wav")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"].startswith("voi_")
    assert body["kind"] == "voices"
    assert body["name"] == "Mia"
    assert body["project_id"] == project.id
    assert body["files"]["source"] == "source.wav"
    assert body["files"]["reference"] == "reference.wav"
    assert body["meta"]["reference_sample_rate"] == 32000
    assert body["meta"]["reference_channels"] == 2
    assert body["meta"]["h3_ready"] is True
    assert body["meta"]["duration_s"] == pytest.approx(2.1, abs=0.02)

    asset_dir = voice_env["projects"] / project.id / "library" / "voices" / body["id"]
    assert (asset_dir / "source.wav").read_bytes() == _wav_bytes(duration_s=2.1)
    with wave.open(str(asset_dir / "reference.wav"), "rb") as normalized:
        assert normalized.getframerate() == 32000
        assert normalized.getnchannels() == 2

    manifest = json.loads((asset_dir / "asset.json").read_text(encoding="utf-8"))
    assert manifest["meta"]["description"] == "Warm neutral English, intimate delivery"


@pytest.mark.parametrize(
    ("name", "payload", "expected"),
    [
        ("", _wav_bytes(duration_s=2.1), "name"),
        ("Mia", b"not-audio", "audio"),
        ("Mia", _wav_bytes(duration_s=1.0), "between 2 and 15"),
        ("Mia", _wav_bytes(duration_s=15.2), "between 2 and 15"),
    ],
    ids=["missing-name", "unreadable", "too-short", "too-long"],
)
def test_import_voice_reference_rejects_invalid_input(
    client,
    voice_env,
    name: str,
    payload: bytes,
    expected: str,
):
    project = create_project("Voice project", "script")

    response = client.post(
        "/api/library/import",
        data={"kind": "voices", "name": name, "project_id": project.id},
        files={"file": ("voice.wav", payload, "audio/wav")},
    )

    assert response.status_code == 400
    assert expected in response.json()["detail"].lower()
    voices_dir = voice_env["projects"] / project.id / "library" / "voices"
    assert not voices_dir.exists() or not any(voices_dir.iterdir())


def test_voice_import_removes_partial_asset_when_normalization_fails(
    client,
    voice_env,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.core.library import store

    project = create_project("Voice project", "script")

    def fail_normalize(source: Path, destination: Path):
        destination.write_bytes(b"partial")
        raise ValueError("normalization failed")

    monkeypatch.setattr(store, "normalize_voice_reference", fail_normalize)
    response = client.post(
        "/api/library/import",
        data={"kind": "voices", "name": "Mia", "project_id": project.id},
        files={"file": ("voice.wav", _wav_bytes(duration_s=2.1), "audio/wav")},
    )

    assert response.status_code == 400
    assert "normalization failed" in response.json()["detail"]
    voices_dir = voice_env["projects"] / project.id / "library" / "voices"
    assert not voices_dir.exists() or not any(voices_dir.iterdir())
