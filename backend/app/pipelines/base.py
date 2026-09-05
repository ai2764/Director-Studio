from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from ..core.schemas import ComfyImageRef, JobRecord, LibraryAsset


class _PipelineCommon:
    id: str
    asset_kind: str
    display_name: str
    description: str = ""
    enabled: bool = True

    @property
    def output_labels(self) -> dict[str, str]:
        return {}

    def meta_defaults(self) -> dict[str, Any]:
        """UI defaults / form schema hints for this pipeline."""
        return {
            "id": self.id,
            "asset_kind": self.asset_kind,
            "display_name": self.display_name,
            "description": self.description,
            "output_slots": [
                {"key": k, "label": v} for k, v in self.output_labels.items()
            ],
        }

    def save_to_library(
        self,
        job: JobRecord,
        *,
        name: str | None = None,
        notes: str | None = None,
        project_id: str | None = None,
    ) -> LibraryAsset:
        from ..core.library import save_asset_from_job

        return save_asset_from_job(
            job,
            name=name,
            notes=notes,
            file_keys=list(self.output_labels.keys()) or None,
            input_keys=self.library_input_keys(),
            meta=dict(job.params),
            project_id=project_id,
        )

    def library_input_keys(self) -> list[str]:
        """Input file stems under job/inputs to copy into the library."""
        return []

    def postprocess_job_outputs(self, job: JobRecord, saved: dict[str, Any]) -> None:
        """Optional local fixups after output files are saved."""
        return None


class Pipeline(_PipelineCommon, ABC):
    """
    One ComfyUI-backed generation capability.

    Add a new feature by subclassing Pipeline, implementing these methods,
    and calling register_pipeline() in the package's __init__.
    """

    # All local ComfyUI workflows use the MCP transport. External providers
    # (GPT Bridge, MiniMax cloud API) declare their own adapters separately.
    execution_adapter_id = "comfy_mcp"
    generation_kind: Literal["image", "video"] = "image"

    @abstractmethod
    def build_prompt(
        self,
        job: JobRecord,
        *,
        uploaded_images: dict[str, str],
    ) -> tuple[dict[str, Any], int]:
        """Return (comfy_api_prompt, resolved_seed)."""

    @abstractmethod
    def map_history_outputs(
        self,
        history: dict[str, Any],
        *,
        job: JobRecord | None = None,
    ) -> dict[str, ComfyImageRef]:
        """Map Comfy history node outputs → logical output keys (file stems)."""



@dataclass(frozen=True)
class ExternalPipelineResult:
    outputs: dict[str, tuple[str, bytes]]
    params_update: dict[str, Any] = field(default_factory=dict)


class ExternalPipeline(_PipelineCommon, ABC):
    """A durable job pipeline that does not use ComfyUI or the GPU owner lock."""

    replay_after_restart: bool = False
    execution_adapter_id = "external"
    execution_lane: str | None = None
    execution_lane_cooldown_sec: float = 0.0

    @abstractmethod
    async def run_external(
        self,
        job: JobRecord,
        *,
        inputs: dict[str, tuple[str, bytes]],
        cancel_event: asyncio.Event,
    ) -> ExternalPipelineResult:
        """Execute one externally visible operation and return output bytes."""
        raise NotImplementedError
