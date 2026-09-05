from __future__ import annotations

from typing import TypeAlias

from .base import ExternalPipeline, Pipeline

PipelineType: TypeAlias = Pipeline | ExternalPipeline

_REGISTRY: dict[str, PipelineType] = {}


def register_pipeline(pipeline: PipelineType) -> PipelineType:
    if pipeline.id in _REGISTRY:
        raise ValueError(f"Pipeline already registered: {pipeline.id}")
    _REGISTRY[pipeline.id] = pipeline
    return pipeline


def get_pipeline(pipeline_id: str) -> PipelineType:
    try:
        return _REGISTRY[pipeline_id]
    except KeyError as e:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise KeyError(f"Unknown pipeline '{pipeline_id}'. Known: {known}") from e


def all_pipelines(*, enabled_only: bool = True) -> list[PipelineType]:
    items = list(_REGISTRY.values())
    if enabled_only:
        items = [p for p in items if p.enabled]
    return sorted(items, key=lambda p: p.id)
