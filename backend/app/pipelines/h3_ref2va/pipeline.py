from __future__ import annotations

import base64
import io
import json
import tempfile
import wave
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from ...config import settings
from ...core.h3.prompt import (
    validate_no_time_addressable_pictures,
    validate_required_picture_bindings,
)
from ...core.library.audio import probe_audio
from ...core.schemas import ComfyImageRef, JobRecord
from ...workflow_profiles.h3 import (
    H3ProfileStore,
    ResolvedH3Profile,
    load_job_profile_snapshot,
    resolve_active_h3_profile,
    snapshot_profile_for_job,
)
from ..base import Pipeline
from . import workflow


class H3Ref2VaPipeline(Pipeline):
    id = "h3_ref2va"
    asset_kind = "productions"
    display_name = "H3 Ref2AV"
    description = (
        "Official ComfyUI MiniMax H3 Ref2AV workflow with native generated audio."
    )

    @property
    def execution_adapter_id(self) -> str:
        return {
            "local": "comfy_mcp",
            "mcp": "comfy_mcp",
            "minimax": "h3_api",
        }[self._provider_name()]

    def execution_adapter_id_for_job(self, job: JobRecord) -> str:
        provider = str((job.params or {}).get("h3_provider") or "").strip().lower()
        if provider:
            if provider not in {"local", "mcp", "minimax"}:
                raise ValueError(f"Unsupported H3 provider: {provider}")
            if provider in {"local", "mcp"}:
                return "comfy_mcp"
            return "h3_api"
        return self.execution_adapter_id

    def prepare_job_submission(self, job: JobRecord) -> None:
        """Snapshot local workflow state before the job can enter the queue."""
        if self.execution_adapter_id_for_job(job) == "h3_api":
            return
        if bool((job.params or {}).get("h3_profile_test")):
            H3ProfileStore().snapshot_import_for_job(job)
            return
        snapshot_profile_for_job(job)

    def postprocess_job_outputs(self, job: JobRecord, saved: dict[str, Any]) -> None:
        """Bind a downloaded setup-test video to its immutable import identity."""
        params = job.params or {}
        if not bool(params.get("h3_profile_test")):
            return
        video_path = saved.get("video")
        if video_path is None or not Path(video_path).is_file():
            return
        import_id = params.get("h3_profile_import_id")
        workflow_sha256 = params.get("h3_profile_test_workflow_sha256")
        mapping_sha256 = params.get("h3_profile_test_mapping_sha256")
        if not all(
            isinstance(value, str) and value
            for value in (import_id, workflow_sha256, mapping_sha256)
        ):
            raise ValueError("H3 profile test job has no captured import identity")
        snapshot = load_job_profile_snapshot(job.id)
        if (
            snapshot.profile_id != import_id
            or snapshot.workflow_sha256 != workflow_sha256
            or H3ProfileStore.mapping_sha256(snapshot.mapping) != mapping_sha256
        ):
            raise ValueError("H3 profile test snapshot identity changed")
        H3ProfileStore().record_test_success(
            import_id,
            workflow_sha256=workflow_sha256,
            mapping_sha256=mapping_sha256,
            job_id=job.id,
        )

    @staticmethod
    def _profile_for_job(job: JobRecord) -> ResolvedH3Profile:
        params = job.params or {}
        expected_id = params.get("h3_profile_id")
        expected_hash = params.get("h3_profile_sha256")
        expected_contract = params.get("h3_contract_version")
        if not expected_id and not expected_hash and expected_contract is None:
            return resolve_active_h3_profile()
        profile = load_job_profile_snapshot(job.id)
        if (
            profile.profile_id != expected_id
            or profile.workflow_sha256 != expected_hash
        ):
            raise ValueError("H3 job profile snapshot does not match its job record")
        if expected_contract != 1:
            raise ValueError("H3 job profile snapshot contract version is unsupported")
        return profile

    @staticmethod
    def _provider_name() -> str:
        provider = str(settings.h3_provider or "local").strip().lower()
        if provider not in {"local", "mcp", "minimax"}:
            raise ValueError(f"Unsupported H3 provider: {provider}")
        return provider

    @property
    def output_labels(self) -> dict[str, str]:
        return dict(workflow.OUTPUT_LABELS)

    def meta_defaults(self) -> dict[str, Any]:
        base = super().meta_defaults()
        base.update(
            {
                "defaults": {
                    "width": workflow.DEFAULT_WIDTH,
                    "height": workflow.DEFAULT_HEIGHT,
                    "max_ref_images": workflow.MAX_REF_IMAGES,
                },
                "fields": [
                    {
                        "id": "prompt",
                        "label": "H3 six-section prompt",
                        "required": True,
                    },
                    {
                        "id": "dialogue",
                        "label": "Dialogue lines (exact match in prompt)",
                        "required": False,
                    },
                    {
                        "id": "frames",
                        "label": "Frame count (n % 17 == 5, 5–362; 124+ nominal)",
                        "required": True,
                    },
                    {
                        "id": "image_keys",
                        "label": "Ordered logical input keys for refs",
                        "required": True,
                    },
                ],
                "output_slots": [
                    {"key": "video", "label": workflow.OUTPUT_LABELS["video"]},
                ],
            }
        )
        return base

    def build_prompt(
        self,
        job: JobRecord,
        *,
        uploaded_images: dict[str, str],
    ) -> tuple[dict[str, Any], int]:
        p = job.params or {}
        prompt_text = (p.get("prompt") or "").strip()
        if not prompt_text:
            raise ValueError("prompt is required")
        validate_no_time_addressable_pictures(prompt_text)
        layout_picture_indices = p.get("layout_picture_indices") or []
        if not isinstance(layout_picture_indices, (list, tuple)):
            raise TypeError("layout_picture_indices must be a list")

        frames = p.get("frames")
        if frames is None:
            raise ValueError("frames is required")
        frames = int(frames)

        image_keys = p.get("image_keys")
        if isinstance(image_keys, list) and image_keys:
            ordered_keys = [str(k) for k in image_keys]
        else:
            # Fall back to upload order as dict iteration (stable in Py3.7+)
            ordered_keys = list(uploaded_images.keys())

        image_names: list[str] = []
        for key in ordered_keys:
            name = uploaded_images.get(key)
            if name:
                image_names.append(name)

        if not image_names and uploaded_images:
            # If keys mismatched, use all uploads in stable order
            image_names = list(uploaded_images.values())

        if not image_names:
            raise ValueError("at least one reference image is required")
        validate_required_picture_bindings(
            prompt_text,
            (int(index) for index in layout_picture_indices),
            submitted_picture_indices=range(1, len(image_names) + 1),
        )

        dialogue = p.get("dialogue") or []
        if not isinstance(dialogue, list):
            dialogue = []

        audio_keys = p.get("audio_keys") or []
        if isinstance(audio_keys, list) and audio_keys:
            audio_names = [
                uploaded_images[str(key)]
                for key in audio_keys
                if str(key) in uploaded_images
            ]
        else:
            audio_names = p.get("audios") or p.get("audio_names") or []
            if not isinstance(audio_names, list):
                audio_names = []
        native_audio_name = None
        native_audio_key = p.get("native_audio_key")
        if native_audio_key:
            native_audio_name = uploaded_images.get(str(native_audio_key))

        output_prefix = p.get("output_prefix")
        profile = self._profile_for_job(job)
        return workflow.build_ref2va_prompt(
            prompt=prompt_text,
            dialogue=[str(x) for x in dialogue],
            image_names=image_names,
            audio_names=[str(x) for x in audio_names],
            native_audio_name=native_audio_name,
            frames=frames,
            width=int(p.get("width") or workflow.DEFAULT_WIDTH),
            height=int(p.get("height") or workflow.DEFAULT_HEIGHT),
            seed=job.seed,
            output_prefix=output_prefix,
            job_id=job.id,
            profile=profile,
        )

    def build_api_payload(
        self,
        job: JobRecord,
        *,
        inputs: dict[str, tuple[str, bytes]],
    ) -> dict[str, Any]:
        p = job.params or {}
        prompt_text = str(p.get("prompt") or "").strip()
        if not prompt_text:
            raise ValueError("prompt is required")
        if len(prompt_text) > 7000:
            raise ValueError("MiniMax H3 API prompt must not exceed 7000 characters")
        validate_no_time_addressable_pictures(prompt_text)

        image_keys = self._ordered_keys(p.get("image_keys"), inputs)
        if not image_keys:
            raise ValueError("at least one reference image is required")
        if len(image_keys) > 9:
            raise ValueError("H3 API supports at most 9 reference images")
        validate_required_picture_bindings(
            prompt_text,
            (int(index) for index in (p.get("layout_picture_indices") or [])),
            submitted_picture_indices=range(1, len(image_keys) + 1),
        )

        audio_keys = self._ordered_keys(p.get("audio_keys"), inputs)
        overlapping_keys = sorted(set(image_keys) & set(audio_keys))
        if overlapping_keys:
            raise ValueError(
                "H3 API input keys cannot be both image and audio: "
                + ", ".join(overlapping_keys)
            )
        native_audio_key = str(p.get("native_audio_key") or "").strip()
        if native_audio_key:
            raise ValueError(
                "The official H3 Ref2AV workflow cannot preserve locked source "
                "audio exactly"
            )
        if len(audio_keys) > 3:
            raise ValueError("H3 API supports at most 3 reference audio files")

        duration_raw = p.get("duration_s")
        if duration_raw is None:
            duration_raw = int(p.get("frames") or 0) / 24.0
        duration_number = float(duration_raw)
        if not duration_number.is_integer():
            raise ValueError(
                "MiniMax H3 API duration must be an integer number of seconds"
            )
        duration = int(duration_number)
        if not 4 <= duration <= 15:
            raise ValueError(
                "MiniMax H3 API duration must be an integer from 4 to 15 seconds"
            )
        if settings.h3_minimax_model != "MiniMax-H3":
            raise ValueError("Ref2AV requires the MiniMax-H3 model")
        if settings.h3_minimax_resolution not in {"768P", "2K"}:
            raise ValueError("MiniMax-H3 resolution must be 768P or 2K")

        total_audio_duration = 0.0
        for key in audio_keys:
            filename, data = inputs[key]
            audio_duration = self._audio_duration_seconds(filename, data)
            if not 2.0 <= audio_duration <= 15.0:
                raise ValueError(
                    f"H3 API reference audio must be between 2 and 15 seconds: {filename}"
                )
            total_audio_duration += audio_duration
        if total_audio_duration > 15.0:
            raise ValueError(
                "H3 API reference audio total duration must not exceed 15 seconds"
            )

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt_text}]
        for key in image_keys:
            filename, data = inputs[key]
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": self._data_url(filename, data, media_kind="image")
                    },
                    "role": "reference_image",
                }
            )
        for key in audio_keys:
            filename, data = inputs[key]
            content.append(
                {
                    "type": "audio_url",
                    "audio_url": {
                        "url": self._data_url(filename, data, media_kind="audio")
                    },
                    "role": "reference_audio",
                }
            )

        width = int(p.get("width") or workflow.DEFAULT_WIDTH)
        height = int(p.get("height") or workflow.DEFAULT_HEIGHT)
        payload = {
            "model": settings.h3_minimax_model,
            "content": content,
            "duration": duration,
            "resolution": settings.h3_minimax_resolution,
            "ratio": "16:9" if width >= height else "9:16",
        }
        body_size = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        configured_limit_mb = min(float(settings.h3_minimax_max_request_mb), 64.0)
        limit = int(configured_limit_mb * 1024 * 1024)
        if body_size > limit:
            raise ValueError(
                f"MiniMax H3 API request body is {body_size / 1024 / 1024:.1f} MB; "
                f"the configured/official limit is {configured_limit_mb:g} MB"
            )
        return payload

    @staticmethod
    def _ordered_keys(value: Any, inputs: dict[str, tuple[str, bytes]]) -> list[str]:
        if not isinstance(value, list):
            return []
        keys = [str(key) for key in value]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(
                "H3 API declared duplicate input keys: " + ", ".join(duplicates)
            )
        missing = [key for key in keys if key not in inputs]
        if missing:
            raise ValueError(
                "H3 API declared missing input keys: " + ", ".join(missing)
            )
        return keys

    @staticmethod
    def _data_url(filename: str, data: bytes, *, media_kind: str) -> str:
        suffix = Path(filename).suffix.lower()
        allowed = {
            "image": {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".webp": "image/webp",
                ".heic": "image/heic",
                ".heif": "image/heif",
            },
            "audio": {".wav": "audio/wav", ".mp3": "audio/mp3"},
        }[media_kind]
        mime = allowed.get(suffix)
        if not mime:
            raise ValueError(
                f"Unsupported H3 API {media_kind} format: {suffix or '(none)'}"
            )
        per_file_limit = 30 if media_kind == "image" else 15
        if len(data) > per_file_limit * 1024 * 1024:
            raise ValueError(
                f"H3 API {media_kind} file exceeds {per_file_limit} MB: {filename}"
            )
        if media_kind == "image":
            H3Ref2VaPipeline._validate_image(filename, data, suffix=suffix)
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    @staticmethod
    def _validate_image(filename: str, data: bytes, *, suffix: str) -> None:
        expected_formats = {
            ".jpg": {"JPEG"},
            ".jpeg": {"JPEG"},
            ".png": {"PNG"},
            ".webp": {"WEBP"},
            ".heic": {"HEIC", "HEIF"},
            ".heif": {"HEIC", "HEIF"},
        }
        try:
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                actual_format = str(image.format or "").upper()
        except (OSError, UnidentifiedImageError) as exc:
            raise ValueError(
                f"Unable to decode H3 API reference image: {filename}"
            ) from exc
        if actual_format not in expected_formats[suffix]:
            raise ValueError(
                f"H3 API image content does not match its extension: {filename}"
            )
        if not 256 <= width <= 5760 or not 256 <= height <= 5760:
            raise ValueError(
                f"H3 API image dimensions must each be between 256 and 5760 pixels: {filename}"
            )
        ratio = width / height
        if not 0.4 <= ratio <= 2.5:
            raise ValueError(
                f"H3 API image aspect ratio must be between 0.4 and 2.5: {filename}"
            )

    @staticmethod
    def _audio_duration_seconds(filename: str, data: bytes) -> float:
        suffix = Path(filename).suffix.lower()
        if suffix == ".wav":
            try:
                with wave.open(io.BytesIO(data), "rb") as source:
                    frame_rate = source.getframerate()
                    return source.getnframes() / frame_rate
            except (EOFError, wave.Error, ZeroDivisionError) as exc:
                raise ValueError(
                    f"Unable to decode H3 API reference audio: {filename}"
                ) from exc
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
                temporary.write(data)
                temporary_path = Path(temporary.name)
            return probe_audio(temporary_path).duration_s
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def map_history_outputs(
        self,
        history: dict[str, Any],
        *,
        job: JobRecord | None = None,
    ) -> dict[str, ComfyImageRef]:
        profile = self._profile_for_job(job) if job is not None else None
        return workflow.map_history_outputs(history, profile=profile)

    def library_input_keys(self) -> list[str]:
        return []
