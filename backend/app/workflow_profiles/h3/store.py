"""Safe persistent storage and resolution for H3 workflow profiles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from app.config import settings

from .errors import (
    ProfileChangedError,
    ProfileStateError,
    ProfileStorageError,
    ProfileWarning,
)
from .models import H3BoundaryMapping, H3WorkflowProfile, ResolvedH3Profile

if TYPE_CHECKING:
    from app.core.schemas import JobRecord

_PROFILE_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
_IMPORT_ID_RE = re.compile(r"imp-[a-f0-9]{32}\Z")
_BUILTIN_PROFILE_ID = "builtin-official-h3"
_WORKFLOW_FILE = "workflow.api.json"
_PROFILE_FILE = "profile.json"
_ACTIVE_FILE = "active.json"
_MAPPING_FILE = "mapping.json"
_VALIDATION_FILE = "validation.json"
_TEST_FILE = "test.json"
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

    def import_workflow_sha256(self, import_id: str) -> str:
        """Return the digest of the currently stored import bytes."""
        _workflow, workflow_sha256 = self._read_workflow(
            self.import_workflow_path(import_id)
        )
        return workflow_sha256

    def load_import_workflow(self, import_id: str) -> dict[str, Any]:
        """Load an import by opaque ID, never by a caller-provided path."""
        workflow, _workflow_sha256 = self._read_workflow(
            self.import_workflow_path(import_id)
        )
        return workflow

    def save_import_mapping(
        self,
        import_id: str,
        mapping: H3BoundaryMapping,
    ) -> None:
        """Persist an accepted mapping and invalidate mapping-specific evidence."""
        directory = self._require_existing_import(import_id)
        self._atomic_write_json(
            directory / _MAPPING_FILE,
            mapping.model_dump(mode="json"),
        )
        for stale_name in (_VALIDATION_FILE, _TEST_FILE):
            try:
                (directory / stale_name).unlink(missing_ok=True)
            except OSError as exc:
                raise ProfileStorageError(
                    "Could not invalidate stale workflow profile evidence"
                ) from exc

    def load_import_mapping(self, import_id: str) -> H3BoundaryMapping | None:
        """Load the selected mapping, or return none before one is accepted."""
        path = self._require_existing_import(import_id) / _MAPPING_FILE
        if not path.exists():
            return None
        try:
            return H3BoundaryMapping.model_validate(self._read_json(path))
        except ValidationError as exc:
            raise ProfileStorageError("Stored import mapping is invalid") from exc

    def record_validation_success(
        self,
        import_id: str,
        *,
        workflow_sha256: str,
        mapping_sha256: str,
        report: dict[str, Any],
        comfy_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist successful contract and live-Comfy validation evidence."""
        current_workflow_sha256, current_mapping_sha256 = self.import_identity(
            import_id
        )
        if (
            current_workflow_sha256 != workflow_sha256
            or current_mapping_sha256 != mapping_sha256
        ):
            raise ProfileChangedError(
                "Imported workflow or mapping changed during validation"
            )
        record = {
            "valid": True,
            "contract_version": 1,
            "workflow_sha256": workflow_sha256,
            "mapping_sha256": mapping_sha256,
            "validated_at": datetime.now(UTC).isoformat(),
            "report": report,
            "comfy": comfy_payload,
        }
        self._atomic_write_json(
            self._require_existing_import(import_id) / _VALIDATION_FILE,
            record,
        )
        return record

    def record_test_success(
        self,
        import_id: str,
        *,
        workflow_sha256: str,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist trusted test-run evidence for Task 6's job completion hook."""
        directory = self._require_existing_import(import_id)
        mapping = self.load_import_mapping(import_id)
        if mapping is None:
            raise ProfileStateError(
                "mapping_required",
                "A workflow mapping is required before recording a test",
                details={"import_id": import_id},
            )
        record = {
            "status": "succeeded",
            "workflow_sha256": str(workflow_sha256),
            "mapping_sha256": self._mapping_sha256(mapping),
            "job_id": job_id,
            "tested_at": datetime.now(UTC).isoformat(),
        }
        self._atomic_write_json(directory / _TEST_FILE, record)
        return record

    def activate_import(self, import_id: str) -> H3WorkflowProfile:
        """Install and select an import only with same-identity validation/test proof."""
        directory = self._require_existing_import(import_id)
        workflow, workflow_sha256 = self._read_workflow(directory / _WORKFLOW_FILE)
        mapping = self.load_import_mapping(import_id)
        if mapping is None:
            raise ProfileStateError(
                "mapping_required",
                "A workflow mapping is required before activation",
                details={"import_id": import_id},
            )
        mapping_sha256 = self._mapping_sha256(mapping)
        validation = self._optional_record(directory / _VALIDATION_FILE)
        if validation is None or validation.get("valid") is not True:
            raise ProfileStateError(
                "validation_required",
                "Successful validation is required before activation",
                details={"import_id": import_id},
            )
        if validation.get("contract_version") != 1:
            raise ProfileStateError(
                "unsupported_contract",
                "The validated workflow contract version is unsupported",
                details={"import_id": import_id},
            )
        if (
            validation.get("workflow_sha256") != workflow_sha256
            or validation.get("mapping_sha256") != mapping_sha256
        ):
            raise ProfileChangedError(
                "Imported workflow or mapping changed after validation"
            )

        test_record = self._optional_record(directory / _TEST_FILE)
        if test_record is None or test_record.get("status") != "succeeded":
            raise ProfileStateError(
                "test_required",
                "A successful test for this workflow is required before activation",
                details={"import_id": import_id},
            )
        if (
            test_record.get("workflow_sha256") != workflow_sha256
            or test_record.get("mapping_sha256") != mapping_sha256
        ):
            raise ProfileChangedError(
                "Imported workflow or mapping changed after its successful test"
            )
        if not isinstance(test_record.get("job_id"), str) or not test_record["job_id"]:
            raise ProfileStateError(
                "test_required",
                "A successful test job is required before activation",
                details={"import_id": import_id},
            )

        from .validator import validate_h3_contract

        contract = validate_h3_contract(workflow, mapping)
        if not contract.valid:
            raise ProfileStateError(
                "contract_validation_failed",
                "The workflow no longer satisfies the H3 contract",
                details={
                    "issues": [
                        issue.model_dump(mode="json") for issue in contract.issues
                    ]
                },
            )

        profile_id = f"custom-{workflow_sha256[:32]}-{mapping_sha256[:16]}"
        profile = H3WorkflowProfile(
            id=profile_id,
            workflow_sha256=workflow_sha256,
            mapping=mapping,
            status="active",
        )
        self.install_profile(profile, workflow)
        self._atomic_write_json(
            self._profile_dir(profile_id) / _VALIDATION_FILE,
            {**validation, "test": test_record},
        )
        self.select_profile(profile_id)
        return profile

    def list_installed_profiles(self) -> list[H3WorkflowProfile]:
        """Return valid installed custom profile metadata in stable ID order."""
        if not self.profiles_dir.exists():
            return []
        profiles: list[H3WorkflowProfile] = []
        for directory in sorted(
            self.profiles_dir.iterdir(), key=lambda path: path.name
        ):
            if not directory.is_dir() or directory.is_symlink():
                continue
            try:
                profile_id = self._require_profile_id(directory.name)
                profile = H3WorkflowProfile.model_validate(
                    self._read_json(self.profile_path(profile_id))
                )
            except (ProfileStorageError, ValidationError):
                continue
            profiles.append(profile)
        return profiles

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

    def _require_existing_import(self, import_id: str) -> Path:
        directory = self._import_dir(import_id)
        if not directory.is_dir() or directory.is_symlink():
            raise ProfileStorageError("Workflow import was not found")
        return directory

    def import_identity(self, import_id: str) -> tuple[str, str]:
        """Return the current workflow and mapping hashes for evidence binding."""
        workflow_sha256 = self.import_workflow_sha256(import_id)
        mapping = self.load_import_mapping(import_id)
        if mapping is None:
            raise ProfileStateError(
                "mapping_required",
                "A workflow mapping is required",
                details={"import_id": import_id},
            )
        return workflow_sha256, self._mapping_sha256(mapping)

    @classmethod
    def _mapping_sha256(cls, mapping: H3BoundaryMapping) -> str:
        return cls._sha256(cls._json_bytes(mapping.model_dump(mode="json")))

    def _optional_record(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        return self._read_json(path)

    def install_profile(
        self, profile: H3WorkflowProfile, workflow: dict[str, Any]
    ) -> None:
        """Persist one already-validated custom profile using only its safe ID."""
        profile_id = self._require_profile_id(profile.id)
        if not isinstance(workflow, dict):
            raise ProfileStorageError("Profile workflow must be a JSON object")
        self._assert_pure_ref2av(workflow)
        workflow_bytes = self._json_bytes(workflow)
        actual_hash = self._sha256(workflow_bytes)
        if profile.workflow_sha256 != actual_hash:
            raise ProfileChangedError(
                "Profile workflow hash does not match supplied workflow"
            )
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
            if not isinstance(expected_hash, str) or not re.fullmatch(
                r"[0-9a-f]{64}", expected_hash
            ):
                raise ProfileStorageError(
                    "Active profile pointer has an invalid workflow hash"
                )
            resolved = (
                self._resolve_builtin()
                if profile_id == _BUILTIN_PROFILE_ID
                else self._resolve_custom(profile_id)
            )
            if resolved.workflow_sha256 != expected_hash:
                raise ProfileChangedError(
                    "Active profile hash no longer matches its pointer"
                )
            return resolved
        except ProfileChangedError as exc:
            return self._fallback("profile_changed", str(exc))
        except (ProfileStorageError, ValidationError, OSError, TypeError) as exc:
            return self._fallback("profile_unavailable", str(exc))

    def resolve_builtin(self) -> ResolvedH3Profile:
        """Resolve the packaged profile for setup/status responses."""
        return self._resolve_builtin()

    def snapshot_for_job(self, job: JobRecord) -> ResolvedH3Profile:
        """Atomically capture the currently resolved profile for one local H3 job."""
        from app.core.jobs.store import job_dir

        params = job.params or {}
        snapshot_dir = job_dir(job.id, project_id=job.project_id) / _JOB_SNAPSHOT_DIR
        identity_keys = {
            "h3_profile_id",
            "h3_profile_sha256",
            "h3_contract_version",
        }
        if snapshot_dir.exists() or identity_keys.intersection(params):
            snapshot = self.load_job_snapshot(job.id)
            if (
                params.get("h3_profile_id") != snapshot.profile_id
                or params.get("h3_profile_sha256") != snapshot.workflow_sha256
                or params.get("h3_contract_version") != 1
            ):
                raise ProfileChangedError(
                    "Job profile snapshot identity does not match its job record"
                )
            return snapshot

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
        from app.core.jobs.store import job_dir

        snapshot_dir = job_dir(job_id) / _JOB_SNAPSHOT_DIR
        profile_path = snapshot_dir / _PROFILE_FILE
        workflow_path = snapshot_dir / _WORKFLOW_FILE
        profile_data = self._read_json(profile_path)
        try:
            profile = H3WorkflowProfile.model_validate(profile_data)
        except ValidationError as exc:
            raise ProfileStorageError(
                "Job profile snapshot metadata is invalid"
            ) from exc
        workflow, workflow_hash = self._read_workflow(workflow_path)
        if profile.workflow_sha256 != workflow_hash:
            raise ProfileChangedError(
                "Job workflow profile snapshot hash does not match"
            )
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
            raise ProfileStorageError(
                f"Could not read workflow file: {path.name}"
            ) from exc
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
            raise ProfileStorageError(
                f"Could not read profile file: {path.name}"
            ) from exc
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
            raise ProfileStorageError(
                f"Could not atomically write {path.name}"
            ) from exc
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
