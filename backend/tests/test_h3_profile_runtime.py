"""Runtime coverage for profile-driven H3 graph filling and job snapshots."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from app.config import settings
from app.core.jobs import runner
from app.core.jobs.store import create_job, job_dir, load_job
from app.pipelines.h3_ref2va.pipeline import H3Ref2VaPipeline
from app.pipelines.h3_ref2va.schemas import H3Ref2VaJobResponse
from app.pipelines.h3_ref2va.workflow import fill_profile_graph, load_base_prompt
from app.workflow_profiles.h3 import (
    H3BoundaryMapping,
    H3ProfileStore,
    H3WorkflowProfile,
    ResolvedH3Profile,
    load_job_profile_snapshot,
)


SAMPLE_PROMPT = (
    "subject_definitions:\nA\n"
    "summary:\nB\n"
    "retention_analysis:\nC\n"
    "detailed_description:\nD\n"
    "overall_soundscape:\nE\n"
    "non_diegetic_music:\nF"
)


def _job_params(**overrides: object) -> dict[str, object]:
    params: dict[str, object] = {
        "prompt": SAMPLE_PROMPT,
        "dialogue": [],
        "images": ["p1.png", "p2.png"],
        "audios": [],
        "frames": 294,
        "seed": 42,
        "width": 864,
        "height": 480,
        "output_prefix": "director-studio/h3/sample",
    }
    params.update(overrides)
    return params


def _mapping(*, h3: str = "136", noise: str = "129", saver: str = "92") -> H3BoundaryMapping:
    return H3BoundaryMapping(
        h3_node_id=h3,
        prompt_input="prompt",
        width_input="width",
        height_input="height",
        frames_input="length",
        picture_input_pattern="ref_images.ref_image_{index}",
        audio_input_pattern="ref_audios.ref_audio_{index}",
        seed_node_id=noise,
        seed_input="noise_seed",
        saver_node_id=saver,
        output_prefix_input="filename_prefix",
    )


def _resolved(
    workflow: dict[str, object],
    *,
    profile_id: str = "test-profile",
    mapping: H3BoundaryMapping | None = None,
) -> ResolvedH3Profile:
    encoded = json.dumps(
        workflow, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return ResolvedH3Profile(
        profile_id=profile_id,
        workflow=workflow,
        mapping=mapping or _mapping(),
        workflow_sha256=hashlib.sha256(encoded).hexdigest(),
        source="custom",
    )


def _install_profile(store: H3ProfileStore, profile_id: str, *, saver_id: str) -> None:
    graph = load_base_prompt()
    graph[saver_id] = graph.pop("92")
    encoded = json.dumps(
        graph, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    store.install_profile(
        H3WorkflowProfile(
            id=profile_id,
            workflow_sha256=hashlib.sha256(encoded).hexdigest(),
            mapping=_mapping(saver=saver_id),
            status="active",
        ),
        graph,
    )


def test_builtin_profile_matches_current_official_fill() -> None:
    profile = H3ProfileStore().resolve_active()

    actual = fill_profile_graph(profile, _job_params())
    canonical = json.dumps(
        actual, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

    # Captured from the pre-profile official graph filler for these exact inputs.
    assert hashlib.sha256(canonical).hexdigest() == (
        "303ad90caec676d355f01d2fb6418386a42ef6165408addf66b22e83ac0ac134"
    )


def test_changed_node_ids_and_socket_names_fill_from_mapping() -> None:
    graph = {
        "900": {
            "class_type": "MiniMaxH3ReferenceToVideo",
            "inputs": {"text": "old", "pictures.8": ["77", 0]},
        },
        "901": {"class_type": "RandomNoise", "inputs": {"seed_value": 0}},
        "902": {"class_type": "SaveVideo", "inputs": {"prefix": "old"}},
    }
    mapping = H3BoundaryMapping(
        h3_node_id="900",
        prompt_input="text",
        width_input="w",
        height_input="h",
        frames_input="frame_count",
        picture_input_pattern="pictures.{index}",
        audio_input_pattern="sounds.{index}",
        seed_node_id="901",
        seed_input="seed_value",
        saver_node_id="902",
        output_prefix_input="prefix",
    )

    filled = fill_profile_graph(
        _resolved(graph, mapping=mapping),
        _job_params(
            prompt=SAMPLE_PROMPT.replace("A", "<Audio 1> defines the voice", 1),
            images=["one.png"],
            audios=["voice.wav"],
        ),
    )

    assert filled["900"]["inputs"]["text"].startswith("subject_definitions:")
    assert filled["900"]["inputs"]["w"] == 864
    assert filled["900"]["inputs"]["frame_count"] == 294
    assert "pictures.8" not in filled["900"]["inputs"]
    assert filled["901"]["inputs"]["seed_value"] == 42
    assert filled["902"]["inputs"]["prefix"] == "director-studio/h3/sample"
    picture_node = filled["900"]["inputs"]["pictures.0"][0]
    audio_node = filled["900"]["inputs"]["sounds.0"][0]
    assert filled[picture_node]["inputs"]["image"] == "one.png"
    assert filled[audio_node]["inputs"]["audio"] == "voice.wav"


@pytest.mark.asyncio
async def test_queued_job_keeps_profile_selected_at_submission(
    tmp_projects_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    monkeypatch.setattr(settings, "jobs_dir", jobs_root)
    monkeypatch.setattr(settings, "workflow_profiles_dir", tmp_path / "profiles")
    profile_store = H3ProfileStore()
    _install_profile(profile_store, "first-profile", saver_id="910")
    _install_profile(profile_store, "second-profile", saver_id="920")
    profile_store.select_profile("first-profile")

    job = create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name="snapshot",
        params={"h3_provider": "local"},
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def queued_run(job_id, images, cancel):
        started.set()
        await release.wait()

    async def no_reservation(job, pipeline, adapter):
        return False

    monkeypatch.setattr(runner, "_run_job", queued_run)
    monkeypatch.setattr(runner, "_reserve_local_generation", no_reservation)

    await runner.start_pipeline_job(job)
    await started.wait()
    profile_store.select_profile("second-profile")

    snapshot = load_job_profile_snapshot(job.id)
    persisted = load_job(job.id)
    assert snapshot.profile_id == "first-profile"
    assert snapshot.mapping.saver_node_id == "910"
    assert persisted is not None
    assert persisted.params["h3_profile_id"] == "first-profile"
    assert persisted.params["h3_profile_sha256"] == snapshot.workflow_sha256
    assert persisted.params["h3_contract_version"] == 1
    assert (job_dir(job.id) / "workflow_profile" / "workflow.api.json").is_file()
    assert (job_dir(job.id) / "workflow_profile" / "profile.json").is_file()

    release.set()
    await runner.await_pipeline_job(job.id)


def test_pipeline_reads_snapshot_for_graph_and_output_mapping(
    tmp_projects_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    monkeypatch.setattr(settings, "jobs_dir", jobs_root)
    monkeypatch.setattr(settings, "workflow_profiles_dir", tmp_path / "profiles")
    store = H3ProfileStore()
    _install_profile(store, "first-profile", saver_id="910")
    _install_profile(store, "second-profile", saver_id="920")
    store.select_profile("first-profile")
    job = create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name="snapshot consumer",
        params={
            "h3_provider": "local",
            "prompt": SAMPLE_PROMPT,
            "dialogue": [],
            "frames": 294,
            "image_keys": ["ref_0"],
        },
        seed=42,
    )
    pipeline = H3Ref2VaPipeline()
    pipeline.prepare_job_submission(job)
    store.select_profile("second-profile")

    graph, _ = pipeline.build_prompt(
        job, uploaded_images={"ref_0": "uploaded-picture.png"}
    )
    mapped = pipeline.map_history_outputs(
        {
            "outputs": {
                "920": {"videos": [{"filename": "wrong.mp4"}]},
                "910": {"videos": [{"filename": "right.mp4"}]},
            }
        },
        job=job,
    )

    assert graph["910"]["inputs"]["filename_prefix"].endswith("h3_ref2va")
    assert "920" not in graph
    assert mapped["video"].filename == "right.mp4"


def test_minimax_job_skips_local_profile_snapshot(
    tmp_projects_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    monkeypatch.setattr(settings, "jobs_dir", jobs_root)
    job = create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name="cloud",
        params={"h3_provider": "minimax"},
    )

    H3Ref2VaPipeline().prepare_job_submission(job)

    assert not (job_dir(job.id) / "workflow_profile").exists()
    assert "h3_profile_id" not in job.params


def test_builtin_profile_is_snapshotted_for_local_job(
    tmp_projects_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    monkeypatch.setattr(settings, "jobs_dir", jobs_root)
    monkeypatch.setattr(settings, "workflow_profiles_dir", tmp_path / "profiles")
    job = create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name="builtin snapshot",
        params={"h3_provider": "local"},
    )

    H3Ref2VaPipeline().prepare_job_submission(job)

    snapshot = load_job_profile_snapshot(job.id)
    assert snapshot.profile_id == "builtin-official-h3"
    assert snapshot.source == "builtin"
    assert job.params["h3_profile_id"] == "builtin-official-h3"
    assert job.params["h3_profile_sha256"] == snapshot.workflow_sha256


def test_h3_job_response_exposes_profile_identity() -> None:
    job = create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name="response",
        params={
            "h3_profile_id": "custom-profile",
            "h3_profile_sha256": "a" * 64,
            "h3_contract_version": 1,
        },
    )

    response = H3Ref2VaJobResponse.from_job(job)

    assert response.h3_profile_id == "custom-profile"
    assert response.h3_profile_sha256 == "a" * 64
    assert response.h3_contract_version == 1
