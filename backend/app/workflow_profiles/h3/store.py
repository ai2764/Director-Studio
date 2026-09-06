"""Safe persistent storage and resolution for H3 workflow profiles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
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
from .models import (
    H3BoundaryMapping,
    H3InputMapping,
    H3OutputSelection,
    H3WorkflowProfile,
    ResolvedH3Profile,
)

if TYPE_CHECKING:
    from app.core.schemas import JobRecord

_PROFILE_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
_IMPORT_ID_RE = re.compile(r"imp-[a-f0-9]{32}\Z")
_BUILTIN_PROFILE_ID = "builtin-official-h3"
_WORKFLOW_FILE = "workflow.api.json"
_PROFILE_FILE = "profile.json"
_ACTIVE_FILE = "active.json"
_MAPPING_FILE = "mapping.json"
_OUTPUT_FILE = "output.json"
_VALIDATION_FILE = "validation.json"
_TEST_FILE = "test.json"
_IMPORT_FILE = "import.json"
_JOB_SNAPSHOT_DIR = "workflow_profile"
_H3_REF2AV_NODE = "MiniMaxH3ReferenceToVideo"
_H3_I2V_NODE = "MiniMaxH3ImageToVideo"

_OFFICIAL_MAPPING = H3BoundaryMapping(
    inputs=H3InputMapping(
        h3_node_id="136",
        prompt_input="prompt",
        width_input="width",
        height_input="height",
        frames_input="length",
        picture_input_pattern="ref_images.ref_image_{index}",
        audio_input_pattern="ref_audios.ref_audio_{index}",
        seed_node_id="129",
        seed_input="noise_seed",
    ),
    output=H3OutputSelection(node_id="92"),
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
        return self._safe_path(self._profile_dir(profile_id) / _WORKFLOW_FILE)

    def profile_path(self, profile_id: str) -> Path:
        return self._safe_path(self._profile_dir(profile_id) / _PROFILE_FILE)

    def import_workflow_path(self, import_id: str) -> Path:
        return self._safe_path(self._import_dir(import_id) / _WORKFLOW_FILE)

    def import_workflow_sha256(self, import_id: str) -> str:
        """Return the digest of the currently stored import bytes."""
        _workflow, workflow_sha256 = self.load_import_workflow_snapshot(import_id)
        return workflow_sha256

    def load_import_workflow(self, import_id: str) -> dict[str, Any]:
        """Load an import by opaque ID, never by a caller-provided path."""
        workflow, _workflow_sha256 = self.load_import_workflow_snapshot(import_id)
        return workflow

    def load_import_workflow_snapshot(
        self, import_id: str
    ) -> tuple[dict[str, Any], str]:
        """Load graph and digest from the same immutable byte snapshot."""
        return self._read_workflow(self.import_workflow_path(import_id))

    def save_import_output(self, import_id: str, node_id: str) -> None:
        """Persist an output choice and invalidate dependent mapping evidence."""
        if not isinstance(node_id, str) or not node_id:
            raise ProfileStorageError("A workflow output node ID is required")
        directory = self._require_existing_import(import_id)
        current = self.load_import_output(import_id)
        self._atomic_write_json(directory / _OUTPUT_FILE, {"node_id": node_id})
        if current == node_id:
            return
        for name in (_MAPPING_FILE, _VALIDATION_FILE, _TEST_FILE):
            try:
                self._safe_path(directory / name, write=True).unlink(missing_ok=True)
            except OSError as exc:
                raise ProfileStorageError(
                    "Could not invalidate stale workflow boundary evidence"
                ) from exc

    def load_import_output(self, import_id: str) -> str | None:
        """Load the confirmed output node for an import, if selected."""
        path = self._safe_path(self._require_existing_import(import_id) / _OUTPUT_FILE)
        if not path.exists():
            return None
        record = self._read_json(path)
        node_id = record.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            raise ProfileStorageError("Stored workflow output selection is invalid")
        return node_id

    def save_import_mapping(
        self,
        import_id: str,
        mapping: H3BoundaryMapping,
    ) -> None:
        """Persist an accepted mapping and invalidate mapping-specific evidence."""
        directory = self._require_existing_import(import_id)
        stale_paths = [
            self._safe_path(directory / name, write=True)
            for name in (_VALIDATION_FILE, _TEST_FILE)
        ]
        self._atomic_write_json(
            directory / _MAPPING_FILE,
            mapping.model_dump(mode="json"),
        )
        for stale_path in stale_paths:
            try:
                self._safe_path(stale_path, write=True).unlink(missing_ok=True)
            except OSError as exc:
                raise ProfileStorageError(
                    "Could not invalidate stale workflow profile evidence"
                ) from exc

    def load_import_mapping(self, import_id: str) -> H3BoundaryMapping | None:
        """Load the selected mapping, or return none before one is accepted."""
        path = self._safe_path(self._require_existing_import(import_id) / _MAPPING_FILE)
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
            "contract_version": 2,
            "workflow_sha256": workflow_sha256,
            "mapping_sha256": mapping_sha256,
            "validated_at": datetime.now(UTC).isoformat(),
            "display_name": self._display_name(
                self._optional_record(
                    self._require_existing_import(import_id) / _IMPORT_FILE
                )
            ),
            "report": report,
            "comfy": comfy_payload,
        }
        self._atomic_write_json(
            self._require_existing_import(import_id) / _VALIDATION_FILE,
            record,
        )
        return record

    def testable_import_identity(self, import_id: str) -> tuple[str, str]:
        """Return one validated import identity suitable for test submission."""
        workflow_sha256, mapping_sha256 = self.import_identity(import_id)
        self._require_current_validation(
            self._require_existing_import(import_id),
            import_id=import_id,
            workflow_sha256=workflow_sha256,
            mapping_sha256=mapping_sha256,
        )
        return workflow_sha256, mapping_sha256

    def record_test_success(
        self,
        import_id: str,
        *,
        workflow_sha256: str,
        mapping_sha256: str,
        job_id: str,
    ) -> dict[str, Any]:
        """Persist evidence only for an already durable successful test job."""
        directory = self._require_existing_import(import_id)
        current_workflow_sha256, current_mapping_sha256 = self.import_identity(
            import_id
        )
        if (
            current_workflow_sha256 != workflow_sha256
            or current_mapping_sha256 != mapping_sha256
        ):
            raise ProfileChangedError(
                "Imported workflow or mapping changed during test execution"
            )
        self._require_successful_test_job(
            import_id=import_id,
            workflow_sha256=workflow_sha256,
            mapping_sha256=mapping_sha256,
            job_id=job_id,
        )
        if self.import_identity(import_id) != (
            workflow_sha256,
            mapping_sha256,
        ):
            raise ProfileChangedError(
                "Imported workflow or mapping changed during test execution"
            )
        record = {
            "status": "succeeded",
            "contract_version": 2,
            "workflow_sha256": workflow_sha256,
            "mapping_sha256": mapping_sha256,
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
        mapping_sha256 = self.mapping_sha256(mapping)
        validation = self._require_current_validation(
            directory,
            import_id=import_id,
            workflow_sha256=workflow_sha256,
            mapping_sha256=mapping_sha256,
        )

        test_record = self._optional_record(directory / _TEST_FILE)
        if test_record is None or test_record.get("status") != "succeeded":
            raise ProfileStateError(
                "test_required",
                "A successful test for this workflow is required before activation",
                details={"import_id": import_id},
            )
        if test_record.get("contract_version") != 2:
            raise ProfileStateError(
                "unsupported_contract",
                "The tested workflow contract version is unsupported",
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
        self._require_successful_test_job(
            import_id=import_id,
            workflow_sha256=workflow_sha256,
            mapping_sha256=mapping_sha256,
            job_id=test_record["job_id"],
        )
        if self.import_identity(import_id) != (
            workflow_sha256,
            mapping_sha256,
        ):
            raise ProfileChangedError(
                "Imported workflow or mapping changed during activation"
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
        self.install_profile(
            profile,
            workflow,
            validation_record=validation,
            test_record=test_record,
        )
        if self.import_identity(import_id) != (
            workflow_sha256,
            mapping_sha256,
        ):
            raise ProfileChangedError(
                "Imported workflow or mapping changed during activation"
            )
        self.select_profile(profile_id)
        return profile

    def list_installed_profiles(self) -> list[H3WorkflowProfile]:
        """Return valid installed custom profile metadata in stable ID order."""
        try:
            directories = list(self._safe_path(self.profiles_dir).iterdir())
        except (ProfileStorageError, OSError):
            return []
        profiles: list[H3WorkflowProfile] = []
        for directory in sorted(directories, key=lambda path: path.name):
            try:
                if not self._safe_path(directory).is_dir():
                    continue
                profile_id = self._require_profile_id(directory.name)
                profile = H3WorkflowProfile.model_validate(
                    self._read_json(self.profile_path(profile_id))
                )
            except (ProfileStorageError, ValidationError):
                continue
            profiles.append(profile)
        return profiles

    def create_import(
        self, workflow: dict[str, Any], *, display_name: str = "Custom H3 workflow"
    ) -> str:
        """Persist an API workflow under a generated opaque import identifier."""
        if not isinstance(workflow, dict):
            raise ProfileStorageError("Imported workflow must be a JSON object")
        self._mkdir(self.imports_dir)
        for _ in range(10):
            import_id = f"imp-{secrets.token_hex(16)}"
            import_dir = self._import_dir(import_id)
            try:
                self._safe_path(import_dir, write=True).mkdir()
            except FileExistsError:
                continue
            self._atomic_write_json(import_dir / _WORKFLOW_FILE, workflow)
            self._atomic_write_json(
                import_dir / _IMPORT_FILE,
                {
                    "display_name": self._display_name({"display_name": display_name}),
                },
            )
            return import_id
        raise ProfileStorageError("Could not allocate a unique workflow import ID")

    def _require_existing_import(self, import_id: str) -> Path:
        directory = self._import_dir(import_id)
        if not self._safe_path(directory).is_dir():
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
        return workflow_sha256, self.mapping_sha256(mapping)

    def import_lifecycle(self, import_id: str) -> dict[str, Any]:
        """Expose bounded current evidence for reloadable setup, never file paths."""
        workflow_sha256 = self.import_workflow_sha256(import_id)
        mapping = self.load_import_mapping(import_id)
        mapping_sha256 = self.mapping_sha256(mapping) if mapping else None
        result: dict[str, Any] = {
            "status": "mapped" if mapping else "draft",
            "workflow_sha256": workflow_sha256,
            "mapping_sha256": mapping_sha256,
            "validated_at": None,
            "test_job_id": None,
        }
        if mapping_sha256 is None:
            return result
        directory = self._require_existing_import(import_id)
        try:
            validation = self._require_current_validation(
                directory,
                import_id=import_id,
                workflow_sha256=workflow_sha256,
                mapping_sha256=mapping_sha256,
            )
        except ProfileStorageError:
            return result
        result.update(status="validated", validated_at=validation.get("validated_at"))
        try:
            test = self._optional_record(directory / _TEST_FILE)
            if not test or (
                test.get("status") != "succeeded"
                or test.get("contract_version") != 2
                or test.get("workflow_sha256") != workflow_sha256
                or test.get("mapping_sha256") != mapping_sha256
            ):
                return result
            self._require_successful_test_job(
                import_id=import_id,
                workflow_sha256=workflow_sha256,
                mapping_sha256=mapping_sha256,
                job_id=test.get("job_id"),
            )
            if self.import_identity(import_id) != (workflow_sha256, mapping_sha256):
                return {**result, "status": "mapped", "validated_at": None}
        except ProfileStorageError:
            return result
        result.update(status="tested", test_job_id=test["job_id"])
        return result

    @classmethod
    def mapping_sha256(cls, mapping: H3BoundaryMapping) -> str:
        """Hash one exact mapping snapshot using the store's canonical JSON."""
        return cls._sha256(cls._json_bytes(mapping.model_dump(mode="json")))

    @classmethod
    def boundary_sha256(cls, mapping: H3BoundaryMapping) -> str:
        """Hash the submitted-graph boundary, excluding post-test artifact choice."""
        payload = mapping.model_dump(mode="json")
        payload["output"]["artifact_index"] = None
        return cls._sha256(cls._json_bytes(payload))

    @classmethod
    def _profile_sha256(cls, profile: H3WorkflowProfile) -> str:
        return cls._sha256(cls._json_bytes(profile.model_dump(mode="json")))

    def _optional_record(self, path: Path) -> dict[str, Any] | None:
        if not self._safe_path(path).exists():
            return None
        return self._read_json(path)

    def _require_current_validation(
        self,
        directory: Path,
        *,
        import_id: str,
        workflow_sha256: str,
        mapping_sha256: str,
    ) -> dict[str, Any]:
        validation = self._optional_record(directory / _VALIDATION_FILE)
        if validation is None or validation.get("valid") is not True:
            raise ProfileStateError(
                "validation_required",
                "Successful validation is required before testing or activation",
                details={"import_id": import_id},
            )
        if validation.get("contract_version") != 2:
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
        report = validation.get("report")
        comfy = validation.get("comfy")
        if (
            not isinstance(report, dict)
            or report.get("valid") is not True
            or not isinstance(comfy, dict)
            or comfy.get("valid") is not True
        ):
            raise ProfileStateError(
                "validation_required",
                "Successful contract and Comfy validation is required",
                details={"import_id": import_id},
            )
        return validation

    def _require_successful_test_job(
        self,
        *,
        import_id: str,
        workflow_sha256: str,
        mapping_sha256: str,
        job_id: str,
    ) -> JobRecord:
        """Verify activation evidence against the authoritative durable job."""
        from app.core.jobs.store import job_dir
        from app.core.schemas import JobRecord, JobStatus

        if not isinstance(job_id, str) or not re.fullmatch(r"job_[a-f0-9]{12}", job_id):
            raise ProfileStateError(
                "test_required",
                "The referenced H3 profile test job is invalid",
                details={"import_id": import_id},
            )
        job_record_path = job_dir(job_id) / "job.json"
        try:
            job = JobRecord.model_validate(self._read_json(job_record_path))
        except ValidationError as exc:
            raise ProfileStorageError("Stored H3 profile test job is invalid") from exc
        if job is None or job.status != JobStatus.succeeded:
            raise ProfileStateError(
                "test_required",
                "The referenced H3 profile test job did not succeed",
                details={"import_id": import_id, "job_id": job_id},
            )
        params = job.params or {}
        if (
            job.id != job_id
            or job.pipeline_id != "h3_ref2va"
            or params.get("h3_profile_test") is not True
            or params.get("h3_profile_import_id") != import_id
            or params.get("h3_contract_version") != 2
            or job.project_id is not None
            or job.library_asset_id is not None
            or "shot_id" in params
            or "project_id" in params
        ):
            raise ProfileStateError(
                "test_required",
                "The referenced job is not an isolated H3 profile test job",
                details={"import_id": import_id, "job_id": job_id},
            )
        if (
            params.get("h3_profile_id") != import_id
            or params.get("h3_profile_sha256") != workflow_sha256
            or params.get("h3_profile_test_workflow_sha256") != workflow_sha256
            or params.get("h3_profile_test_mapping_sha256") != mapping_sha256
        ):
            raise ProfileChangedError(
                "H3 profile test job identity does not match its evidence"
            )
        try:
            snapshot = self.load_job_snapshot(job_id)
        except ProfileStorageError as exc:
            raise ProfileStateError(
                "test_required",
                "The referenced H3 profile test job snapshot is unavailable",
                details={"import_id": import_id, "job_id": job_id},
            ) from exc
        if (
            snapshot.profile_id != import_id
            or snapshot.workflow_sha256 != workflow_sha256
            or self.mapping_sha256(snapshot.mapping) != mapping_sha256
        ):
            raise ProfileChangedError(
                "H3 profile test job snapshot does not match its evidence"
            )

        video = (job.outputs or {}).get("video")
        filename = video.filename if video is not None else None
        if (
            video is None
            or not isinstance(filename, str)
            or Path(filename).name != filename
            or Path(filename).suffix.lower() not in {".mp4", ".webm", ".mov", ".mkv"}
        ):
            raise ProfileStateError(
                "test_required",
                "The referenced H3 profile test job has no mapped video output",
                details={"import_id": import_id, "job_id": job_id},
            )
        output_path = self._safe_path(job_dir(job_id) / "outputs" / filename)
        expected_url = f"/api/files/jobs/{job_id}/outputs/{filename}"
        if (
            not output_path.is_file()
            or output_path.stat().st_size <= 0
            or not video.path
            or Path(video.path).resolve() != output_path
            or video.url != expected_url
        ):
            raise ProfileStateError(
                "test_required",
                "The referenced H3 profile test job video is unavailable",
                details={"import_id": import_id, "job_id": job_id},
            )
        return job

    def install_profile(
        self,
        profile: H3WorkflowProfile,
        workflow: dict[str, Any],
        *,
        validation_record: dict[str, Any] | None = None,
        test_record: dict[str, Any] | None = None,
    ) -> None:
        """Persist one already-validated custom profile using only its safe ID."""
        profile_id = self._require_profile_id(profile.id)
        if not isinstance(workflow, dict):
            raise ProfileStorageError("Profile workflow must be a JSON object")
        self._assert_profile_boundary(workflow, profile.mapping)
        workflow_bytes = self._json_bytes(workflow)
        actual_hash = self._sha256(workflow_bytes)
        if profile.workflow_sha256 != actual_hash:
            raise ProfileChangedError(
                "Profile workflow hash does not match supplied workflow"
            )
        directory = self._profile_dir(profile_id)
        self._mkdir(directory)
        self._atomic_write_bytes(directory / _WORKFLOW_FILE, workflow_bytes)
        self._atomic_write_json(
            directory / _PROFILE_FILE,
            profile.model_dump(mode="json"),
        )
        if (validation_record is None) != (test_record is None):
            raise ProfileStorageError(
                "Installed validation and test evidence must be supplied together"
            )
        if validation_record is not None and test_record is not None:
            self._atomic_write_json(
                directory / _VALIDATION_FILE,
                {
                    **validation_record,
                    "test": test_record,
                    "profile_sha256": self._profile_sha256(profile),
                },
            )

    def select_profile(self, profile_id: str) -> None:
        """Atomically point future jobs at the requested verified profile."""
        profile_id = self._require_profile_id(profile_id)
        if profile_id == _BUILTIN_PROFILE_ID:
            resolved = self._resolve_builtin()
        else:
            resolved = self._resolve_custom(profile_id)
        self._mkdir(self.root)
        self._atomic_write_json(
            self.active_path,
            {
                "profile_id": resolved.profile_id,
                "workflow_sha256": resolved.workflow_sha256,
            },
        )

    def resolve_active(self) -> ResolvedH3Profile:
        """Resolve a valid active profile, otherwise safely use the official graph."""
        try:
            if not self._safe_path(self.active_path).exists():
                return self._resolve_builtin()
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

    def resolve_profile(self, profile_id: str) -> ResolvedH3Profile:
        """Read verified installed profile details without changing selection."""
        profile_id = self._require_profile_id(profile_id)
        return (
            self._resolve_builtin()
            if profile_id == _BUILTIN_PROFILE_ID
            else self._resolve_custom(profile_id)
        )

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
        if self._safe_path(snapshot_dir).exists() or identity_keys.intersection(params):
            snapshot = self.load_job_snapshot(job.id)
            if (
                params.get("h3_profile_id") != snapshot.profile_id
                or params.get("h3_profile_sha256") != snapshot.workflow_sha256
                or params.get("h3_contract_version") != 2
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
                profile_bytes = self._read_bytes(profile_path)
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
            workflow_bytes = self._read_bytes(workflow_path)
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
                "h3_contract_version": 2,
            }
        )
        return resolved

    def snapshot_import_for_job(self, job: JobRecord) -> ResolvedH3Profile:
        """Capture one validated, unactivated import for its isolated test job."""
        from app.core.jobs.store import job_dir

        params = job.params or {}
        import_id = params.get("h3_profile_import_id")
        if not isinstance(import_id, str):
            raise ProfileStorageError("H3 profile test job has no import ID")
        expected_workflow_sha256 = params.get("h3_profile_test_workflow_sha256")
        expected_mapping_sha256 = params.get("h3_profile_test_mapping_sha256")
        if not isinstance(expected_workflow_sha256, str) or not isinstance(
            expected_mapping_sha256, str
        ):
            raise ProfileStorageError("H3 profile test job has no captured identity")

        snapshot_dir = job_dir(job.id, project_id=job.project_id) / _JOB_SNAPSHOT_DIR
        identity_keys = {
            "h3_profile_id",
            "h3_profile_sha256",
            "h3_contract_version",
        }
        if self._safe_path(snapshot_dir).exists() or identity_keys.intersection(params):
            snapshot = self.load_job_snapshot(job.id)
            if (
                params.get("h3_profile_id") != import_id
                or params.get("h3_profile_sha256") != expected_workflow_sha256
                or params.get("h3_contract_version") != 2
                or snapshot.profile_id != import_id
                or snapshot.workflow_sha256 != expected_workflow_sha256
                or self.mapping_sha256(snapshot.mapping) != expected_mapping_sha256
            ):
                raise ProfileChangedError(
                    "Test job profile snapshot identity does not match its job record"
                )
            return snapshot

        directory = self._require_existing_import(import_id)
        workflow, workflow_sha256 = self._read_workflow(directory / _WORKFLOW_FILE)
        mapping = self.load_import_mapping(import_id)
        if mapping is None:
            raise ProfileStateError(
                "mapping_required",
                "A workflow mapping is required before testing",
                details={"import_id": import_id},
            )
        mapping_sha256 = self.mapping_sha256(mapping)
        if (
            workflow_sha256 != expected_workflow_sha256
            or mapping_sha256 != expected_mapping_sha256
        ):
            raise ProfileChangedError(
                "Imported workflow or mapping changed before test submission"
            )
        self._require_current_validation(
            directory,
            import_id=import_id,
            workflow_sha256=workflow_sha256,
            mapping_sha256=mapping_sha256,
        )
        self._assert_profile_boundary(workflow, mapping)
        profile = H3WorkflowProfile(
            id=import_id,
            workflow_sha256=workflow_sha256,
            mapping=mapping,
            status="validated",
        )

        if self.import_identity(import_id) != (workflow_sha256, mapping_sha256):
            raise ProfileChangedError(
                "Imported workflow or mapping changed before test snapshot"
            )
        self._atomic_write_bytes(
            snapshot_dir / _WORKFLOW_FILE,
            self._json_bytes(workflow),
        )
        self._atomic_write_json(
            snapshot_dir / _PROFILE_FILE,
            profile.model_dump(mode="json"),
        )
        job.params = dict(params)
        job.params.update(
            {
                "h3_profile_id": import_id,
                "h3_profile_sha256": workflow_sha256,
                "h3_contract_version": 2,
            }
        )
        return ResolvedH3Profile(
            profile_id=import_id,
            workflow=workflow,
            mapping=mapping,
            workflow_sha256=workflow_sha256,
            source="custom",
        )

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
        self._assert_profile_boundary(workflow, profile.mapping)
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
            display_name="Built-in Official H3",
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
        mapping_hash = self.mapping_sha256(profile.mapping)
        evidence = self._optional_record(
            self._profile_dir(profile_id) / _VALIDATION_FILE
        )
        if evidence is None:
            raise ProfileStorageError(
                "Custom profile requires durable validation and test evidence"
            )
        test_record = evidence.get("test")
        if (
            evidence.get("valid") is not True
            or evidence.get("contract_version") != 2
            or not isinstance(evidence.get("report"), dict)
            or evidence["report"].get("valid") is not True
            or not isinstance(evidence.get("comfy"), dict)
            or evidence["comfy"].get("valid") is not True
            or not isinstance(test_record, dict)
            or test_record.get("status") != "succeeded"
            or not isinstance(test_record.get("job_id"), str)
            or not test_record["job_id"]
        ):
            raise ProfileStorageError(
                "Custom profile requires durable validation and test evidence"
            )
        if (
            evidence.get("workflow_sha256") != workflow_hash
            or evidence.get("mapping_sha256") != mapping_hash
            or test_record.get("workflow_sha256") != workflow_hash
            or test_record.get("mapping_sha256") != mapping_hash
            or evidence.get("profile_sha256") != self._profile_sha256(profile)
        ):
            raise ProfileChangedError(
                "Stored profile differs from its validation and test evidence"
            )
        self._assert_profile_boundary(workflow, profile.mapping)
        return ResolvedH3Profile(
            profile_id=profile.id,
            workflow=workflow,
            mapping=profile.mapping,
            workflow_sha256=workflow_hash,
            source="custom",
            display_name=self._display_name(evidence),
            validated_at=evidence.get("validated_at")
            if isinstance(evidence.get("validated_at"), str)
            else None,
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
            display_name=builtin.display_name,
        )

    @staticmethod
    def _display_name(metadata: dict[str, Any] | None) -> str:
        value = (metadata or {}).get("display_name")
        if not isinstance(value, str):
            return "Custom H3 workflow"
        return (
            re.sub(r"[\x00-\x1f\x7f\s]+", " ", value).strip()[:120]
            or "Custom H3 workflow"
        )

    def _profile_dir(self, profile_id: str) -> Path:
        return self._safe_path(self.profiles_dir / self._require_profile_id(profile_id))

    def _import_dir(self, import_id: str) -> Path:
        if not isinstance(import_id, str) or not _IMPORT_ID_RE.fullmatch(import_id):
            raise ProfileStorageError("Invalid workflow import ID")
        return self._safe_path(self.imports_dir / import_id)

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
            raw = self._read_bytes(path)
        except OSError as exc:
            raise ProfileStorageError(
                f"Could not read workflow file: {path.name}"
            ) from exc
        return self._parse_json(raw, path.name), self._sha256(raw)

    @staticmethod
    def _assert_profile_boundary(
        workflow: dict[str, Any], mapping: H3BoundaryMapping
    ) -> None:
        from .validator import validate_h3_contract

        report = validate_h3_contract(workflow, mapping)
        if not report.valid:
            detail = "; ".join(issue.message for issue in report.issues)
            raise ProfileStorageError(
                f"Custom graph does not satisfy its confirmed H3 boundary: {detail}"
            )

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            raw = self._read_bytes(path)
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

    def _safe_path(self, path: Path, *, write: bool = False) -> Path:
        """Check containment and every existing component without following links."""
        path = Path(os.path.abspath(path))
        roots = [self.root, settings.jobs_dir, settings.projects_dir]
        if not write:
            roots.append(settings.workflows_dir)
        allowed = [Path(os.path.abspath(root)) for root in roots]
        if not any(path.is_relative_to(root) for root in allowed):
            raise ProfileStorageError(
                "Workflow profile path is outside its storage roots"
            )
        try:
            for component in (*reversed(path.parents), path):
                try:
                    info = os.lstat(component)
                except FileNotFoundError:
                    continue
                if (
                    stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0)
                    & stat.FILE_ATTRIBUTE_REPARSE_POINT
                    or (stat.S_ISREG(info.st_mode) and info.st_nlink > 1)
                ):
                    raise ProfileStorageError(
                        "Workflow profile paths cannot contain links or reparse points"
                    )
            if not any(path.resolve().is_relative_to(root) for root in allowed):
                raise ProfileStorageError(
                    "Workflow profile path escapes its storage root"
                )
        except (OSError, RuntimeError) as exc:
            raise ProfileStorageError(
                "Could not verify workflow profile storage path"
            ) from exc
        return path

    def _read_bytes(self, path: Path) -> bytes:
        path = self._safe_path(path)
        raw = path.read_bytes()
        self._safe_path(path)
        return raw

    def _mkdir(self, path: Path) -> None:
        self._safe_path(path, write=True).mkdir(parents=True, exist_ok=True)
        self._safe_path(path, write=True)

    def _atomic_write_bytes(self, path: Path, value: bytes) -> None:
        path = self._safe_path(path, write=True)
        self._mkdir(path.parent)
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
            ) as temporary:
                temp_name = temporary.name
                temporary.write(value)
                temporary.flush()
                os.fsync(temporary.fileno())
            self._safe_path(Path(temp_name), write=True)
            self._safe_path(path, write=True)
            os.replace(temp_name, path)
        except OSError as exc:
            raise ProfileStorageError(
                f"Could not atomically write {path.name}"
            ) from exc
        finally:
            if temp_name:
                try:
                    self._safe_path(Path(temp_name), write=True).unlink(missing_ok=True)
                except (OSError, ProfileStorageError):
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
