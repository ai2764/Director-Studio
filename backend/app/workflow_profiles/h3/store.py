"""Safe persistent storage and resolution for H3 workflow profiles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.config import settings
from app.core.jobs.store import job_dir
from app.core.schemas import JobRecord

from .errors import ProfileChangedError, ProfileStorageError, ProfileWarning
from .models import H3BoundaryMapping, H3WorkflowProfile, ResolvedH3Profile

_PROFILE_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
_IMPORT_ID_RE = re.compile(r"imp-[a-f0-9]{32}\Z")
_BUILTIN_PROFILE_ID = "builtin-official-h3"
_WORKFLOW_FILE = "workflow.api.json"
_PROFILE_FILE = "profile.json"
_ACTIVE_FILE = "active.json"
_JOB_SNAPSHOT_DIR = "workflow_profile"
_H3_REF2AV_NODE = "MiniMaxH3ReferenceToVideo"
_H3_I2V_NODE = "MiniMaxH3ImageToVideo"

_OFFICIAL_MAPPING = H3BoundaryMapping(
    h3_node_id="136",
    prompt_input="prompt",
    width_input="width",
    height_input="height",
    frames_input="length",
    picture_input_pattern="ref_images.ref_image_{index}",
    audio_input_pattern="ref_audios.ref_audio_{index}",
    seed_node_id="129",
    seed_input="noise_seed",
    saver_node_id="92",
    output_prefix_input="filename_prefix",
)


class H3ProfileStore:
    """Own H3 profile files below the external persistent-data root only."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or settings.workflow_profiles_dir) / "h3"

    @property
    def imports_dir(self) -> Path:
        return self.root / "imports"

    @property
    def profiles_dir(self) -> Path:
        return self.root / "profiles"

    @property
    def active_path(self) -> Path:
        return self.root / _ACTIVE_FILE

    def workflow_path(self, profile_id: str) -> Path:
        return self._profile_dir(profile_id) / _WORKFLOW_FILE

    def profile_path(self, profile_id: str) -> Path:
        return self._profile_dir(profile_id) / _PROFILE_FILE

    def import_workflow_path(self, import_id: str) -> Path:
        return self._import_dir(import_id) / _WORKFLOW_FILE

    def create_import(self, workflow: dict[str, Any]) -> str:
        """Persist an API workflow under a generated opaque import identifier."""
        if not isinstance(workflow, dict):
            raise ProfileStorageError("Imported workflow must be a JSON object")
        self.imports_dir.mkdir(parents=True, exist_ok=True)
        for _ in range(10):
            import_id = f"imp-{secrets.token_hex(16)}"
            import_dir = self._import_dir(import_id)
            try:
                import_dir.mkdir()
            except FileExistsError:
                continue
            self._atomic_write_json(import_dir / _WORKFLOW_FILE, workflow)
            return import_id
        raise ProfileStorageError("Could not allocate a unique workflow import ID")

    def install_profile(self, profile: H3WorkflowProfile, workflow: dict[str, Any]) -> None:
        """Persist one already-validated custom profile using only its safe ID."""
        profile_id = self._require_profile_id(profile.id)
        if not isinstance(workflow, dict):
            raise ProfileStorageError("Profile workflow must be a JSON object")
        self._assert_pure_ref2av(workflow)
        workflow_bytes = self._json_bytes(workflow)
        actual_hash = self._sha256(workflow_bytes)
        if profile.workflow_sha256 != actual_hash:
            raise ProfileChangedError("Profile workflow hash does not match supplied workflow")
        directory = self._profile_dir(profile_id)
        directory.mkdir(parents=True, exist_ok=True)
        self._atomic_write_bytes(directory / _WORKFLOW_FILE, workflow_bytes)
        self._atomic_write_json(
            directory / _PROFILE_FILE,
            profile.model_dump(mode="json"),
        )

    def select_profile(self, profile_id: str) -> None:
        """Atomically point future jobs at the requested verified profile."""
        profile_id = self._require_profile_id(profile_id)
        if profile_id == _BUILTIN_PROFILE_ID:
            resolved = self._resolve_builtin()
        else:
            resolved = self._resolve_custom(profile_id)
        self.root.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(
            self.active_path,
            {
                "profile_id": resolved.profile_id,
                "workflow_sha256": resolved.workflow_sha256,
            },
        )

    def resolve_active(self) -> ResolvedH3Profile:
        """Resolve a valid active profile, otherwise safely use the official graph."""
        if not self.active_path.exists():
            return self._resolve_builtin()
        try:
            pointer = self._read_json(self.active_path)
            profile_id = self._require_profile_id(pointer.get("profile_id"))
            expected_hash = pointer.get("workflow_sha256")
            if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
                raise ProfileStorageError("Active profile pointer has an invalid workflow hash")
            resolved = (
                self._resolve_builtin()
                if profile_id == _BUILTIN_PROFILE_ID
                else self._resolve_custom(profile_id)
            )
            if resolved.workflow_sha256 != expected_hash:
                raise ProfileChangedError("Active profile hash no longer matches its pointer")
            return resolved
        except ProfileChangedError as exc:
            return self._fallback("profile_changed", str(exc))
        except (ProfileStorageError, ValidationError, OSError, TypeError) as exc:
            return self._fallback("profile_unavailable", str(exc))

    def snapshot_for_job(self, job: JobRecord) -> ResolvedH3Profile:
        """Atomically capture the currently resolved profile for one local H3 job."""
        resolved = self.resolve_active()
        if resolved.source == "builtin":
            workflow_path = Path(settings.workflows_dir) / "h3_ref2va.api.json"
            profile = H3WorkflowProfile(
                id=resolved.profile_id,
                workflow_sha256=resolved.workflow_sha256,
                mapping=resolved.mapping,
                status="active",
            )
            profile_bytes = self._json_bytes(profile.model_dump(mode="json"))
        else:
            workflow_path = self.workflow_path(resolved.profile_id)
            profile_path = self.profile_path(resolved.profile_id)
            try:
                profile_bytes = profile_path.read_bytes()
            except OSError as exc:
                raise ProfileStorageError(
                    "Could not read active profile while snapshotting the job"
                ) from exc
            try:
                source_profile = H3WorkflowProfile.model_validate(
                    self._parse_json(profile_bytes, profile_path.name)
                )
            except ValidationError as exc:
                raise ProfileStorageError(
                    "Active profile metadata changed while snapshotting the job"
                ) from exc
            if (
                source_profile.id != resolved.profile_id
                or source_profile.workflow_sha256 != resolved.workflow_sha256
                or source_profile.mapping != resolved.mapping
            ):
                raise ProfileChangedError(
                    "Active profile metadata changed while snapshotting the job"
                )

        try:
            workflow_bytes = workflow_path.read_bytes()
        except OSError as exc:
            raise ProfileStorageError(
                "Could not read active workflow while snapshotting the job"
            ) from exc
        if self._sha256(workflow_bytes) != resolved.workflow_sha256:
            raise ProfileChangedError(
                "Active workflow changed while snapshotting the job"
            )

        snapshot_dir = job_dir(job.id, project_id=job.project_id) / _JOB_SNAPSHOT_DIR
        self._atomic_write_bytes(snapshot_dir / _WORKFLOW_FILE, workflow_bytes)
        self._atomic_write_bytes(snapshot_dir / _PROFILE_FILE, profile_bytes)
        job.params = dict(job.params or {})
        job.params.update(
            {
                "h3_profile_id": resolved.profile_id,
                "h3_profile_sha256": resolved.workflow_sha256,
                "h3_contract_version": 1,
            }
        )
        return resolved

    def load_job_snapshot(self, job_id: str) -> ResolvedH3Profile:
        """Load and verify the immutable profile snapshot captured for a job."""
        snapshot_dir = job_dir(job_id) / _JOB_SNAPSHOT_DIR
        profile_path = snapshot_dir / _PROFILE_FILE
        workflow_path = snapshot_dir / _WORKFLOW_FILE
        profile_data = self._read_json(profile_path)
        try:
            profile = H3WorkflowProfile.model_validate(profile_data)
        except ValidationError as exc:
            raise ProfileStorageError("Job profile snapshot metadata is invalid") from exc
        workflow, workflow_hash = self._read_workflow(workflow_path)
        if profile.workflow_sha256 != workflow_hash:
            raise ProfileChangedError("Job workflow profile snapshot hash does not match")
        self._assert_pure_ref2av(workflow)
        return ResolvedH3Profile(
            profile_id=profile.id,
            workflow=workflow,
            mapping=profile.mapping,
            workflow_sha256=workflow_hash,
            source="builtin" if profile.id == _BUILTIN_PROFILE_ID else "custom",
        )

    def _resolve_builtin(self) -> ResolvedH3Profile:
        path = Path(settings.workflows_dir) / "h3_ref2va.api.json"
        workflow, workflow_hash = self._read_workflow(path)
        return ResolvedH3Profile(
            profile_id=_BUILTIN_PROFILE_ID,
            workflow=workflow,
            mapping=_OFFICIAL_MAPPING,
            workflow_sha256=workflow_hash,
            source="builtin",
        )

    def _resolve_custom(self, profile_id: str) -> ResolvedH3Profile:
        profile_id = self._require_profile_id(profile_id)
        profile_path = self.profile_path(profile_id)
        workflow_path = self.workflow_path(profile_id)
        profile_data = self._read_json(profile_path)
        try:
            profile = H3WorkflowProfile.model_validate(profile_data)
        except ValidationError as exc:
            raise ProfileStorageError("Stored profile metadata is invalid") from exc
        if profile.id != profile_id:
            raise ProfileStorageError("Stored profile ID does not match its directory")
        if profile.status not in {"tested", "active"}:
            raise ProfileStorageError(
                "Custom profile must be tested or active before selection"
            )
        workflow, workflow_hash = self._read_workflow(workflow_path)
        if profile.workflow_sha256 != workflow_hash:
            raise ProfileChangedError("Stored workflow differs from the profile hash")
        self._assert_pure_ref2av(workflow)
        return ResolvedH3Profile(
            profile_id=profile.id,
            workflow=workflow,
            mapping=profile.mapping,
            workflow_sha256=workflow_hash,
            source="custom",
        )

    def _fallback(self, code: str, message: str) -> ResolvedH3Profile:
        builtin = self._resolve_builtin()
        return ResolvedH3Profile(
            profile_id=builtin.profile_id,
            workflow=builtin.workflow,
            mapping=builtin.mapping,
            workflow_sha256=builtin.workflow_sha256,
            source=builtin.source,
            warning=ProfileWarning(code=code, message=message),
        )

    def _profile_dir(self, profile_id: str) -> Path:
        return self.profiles_dir / self._require_profile_id(profile_id)

    def _import_dir(self, import_id: str) -> Path:
        if not isinstance(import_id, str) or not _IMPORT_ID_RE.fullmatch(import_id):
            raise ProfileStorageError("Invalid workflow import ID")
        return self.imports_dir / import_id

    @staticmethod
    def _require_profile_id(profile_id: object) -> str:
        if not isinstance(profile_id, str) or not _PROFILE_ID_RE.fullmatch(profile_id):
            raise ProfileStorageError("Invalid workflow profile ID")
        return profile_id

    @staticmethod
    def _sha256(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    @staticmethod
    def _json_bytes(value: dict[str, Any]) -> bytes:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def _read_workflow(self, path: Path) -> tuple[dict[str, Any], str]:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ProfileStorageError(f"Could not read workflow file: {path.name}") from exc
        return self._parse_json(raw, path.name), self._sha256(raw)

    @staticmethod
    def _assert_pure_ref2av(workflow: dict[str, Any]) -> None:
        h3_nodes = 0
        for node in workflow.values():
            if not isinstance(node, dict):
                continue
            class_type = node.get("class_type")
            if class_type == _H3_REF2AV_NODE:
                h3_nodes += 1
            if class_type == _H3_I2V_NODE:
                raise ProfileStorageError(
                    "Custom graph must remain a pure Ref2AV workflow (no I2V node)"
                )
            inputs = node.get("inputs")
            if isinstance(inputs, dict) and {"ref_frame", "last_frame"} & inputs.keys():
                raise ProfileStorageError(
                    "Custom graph must remain a pure Ref2AV workflow "
                    "(no ref_frame or last_frame input)"
                )
        if h3_nodes != 1:
            raise ProfileStorageError(
                "Custom graph must remain a pure Ref2AV workflow "
                "with exactly one MiniMaxH3ReferenceToVideo node"
            )

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ProfileStorageError(f"Could not read profile file: {path.name}") from exc
        return self._parse_json(raw, path.name)

    @staticmethod
    def _parse_json(raw: bytes, name: str) -> dict[str, Any]:
        try:
            value = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProfileStorageError(f"Invalid JSON in {name}") from exc
        if not isinstance(value, dict):
            raise ProfileStorageError(f"JSON object required in {name}")
        return value

    def _atomic_write_json(self, path: Path, value: dict[str, Any]) -> None:
        self._atomic_write_bytes(path, self._json_bytes(value))

    @staticmethod
    def _atomic_write_bytes(path: Path, value: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
            ) as temporary:
                temp_name = temporary.name
                temporary.write(value)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temp_name, path)
        except OSError as exc:
            raise ProfileStorageError(f"Could not atomically write {path.name}") from exc
        finally:
            if temp_name:
                try:
                    Path(temp_name).unlink(missing_ok=True)
                except OSError:
                    pass


def resolve_active_h3_profile() -> ResolvedH3Profile:
    """Resolve the active H3 profile for a newly submitted local H3 job."""
    return H3ProfileStore().resolve_active()


def snapshot_profile_for_job(job: JobRecord) -> ResolvedH3Profile:
    """Capture the active H3 profile before a local job enters the queue."""
    return H3ProfileStore().snapshot_for_job(job)


def load_job_profile_snapshot(job_id: str) -> ResolvedH3Profile:
    """Resolve a job's captured H3 profile without consulting the active pointer."""
    return H3ProfileStore().load_job_snapshot(job_id)
