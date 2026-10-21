"""Versioned data contracts for custom image workflow profiles.

This mirrors the H3 profile design (see ``workflow_profiles/h3``): the
user's ComfyUI graph stays opaque and workflow-owned (models, samplers,
LoRAs and internal routing are never overwritten), while Director Studio
owns only a *boundary mapping* between its semantic contract for an asset
kind (see ``contracts.py``) and concrete node inputs in the graph.

Phase 1 kinds are ``prop`` and ``layout`` — the pipelines whose semantic
boundary is narrow (one prompt, ordered reference images, one primary
output). Actor and scene follow once this mechanism is proven.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImageTextBinding(_StrictModel):
    """One semantic text role filled into one graph node input.

    A role may bind several nodes (e.g. a prop graph whose front/side
    samplers each carry their own text encode): every binding receives
    the same caller-supplied text at fill time.
    """

    role: Literal["prompt", "negative"]
    node_id: StrictStr = Field(min_length=1)
    input_name: StrictStr = Field(min_length=1)


class ImageRefBinding(_StrictModel):
    """The ``index``-th semantic reference image, in caller order."""

    index: int = Field(ge=0, le=15)
    node_id: StrictStr = Field(min_length=1)
    input_name: StrictStr = Field(default="image", min_length=1)


class ImageSeedBinding(_StrictModel):
    """One sampler seed input; a run applies one seed to every binding."""

    node_id: StrictStr = Field(min_length=1)
    input_name: StrictStr = Field(default="seed", min_length=1)


class ImageSizeBinding(_StrictModel):
    """Optional latent size inputs, when the graph exposes them."""

    node_id: StrictStr = Field(min_length=1)
    width_input: StrictStr = Field(default="width", min_length=1)
    height_input: StrictStr = Field(default="height", min_length=1)


class ImageOutputBinding(_StrictModel):
    """One semantic output slot collected from one terminal node."""

    role: StrictStr = Field(min_length=1)
    node_id: StrictStr = Field(min_length=1)


class ImageBoundaryMapping(_StrictModel):
    """The complete Director Studio boundary around an opaque image graph."""

    texts: list[ImageTextBinding] = Field(default_factory=list, max_length=16)
    images: list[ImageRefBinding] = Field(default_factory=list, max_length=16)
    seeds: list[ImageSeedBinding] = Field(default_factory=list, max_length=32)
    size: ImageSizeBinding | None = None
    outputs: list[ImageOutputBinding] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_unique_targets(self) -> "ImageBoundaryMapping":
        text_targets = [(b.node_id, b.input_name) for b in self.texts]
        if len(text_targets) != len(set(text_targets)):
            raise ValueError("text bindings must not target the same node input twice")
        image_indexes = [b.index for b in self.images]
        if len(image_indexes) != len(set(image_indexes)):
            raise ValueError("reference image indexes must be unique")
        output_roles = [b.role for b in self.outputs]
        if len(output_roles) != len(set(output_roles)):
            raise ValueError("output roles must be unique")
        return self


class ImageWorkflowProfile(_StrictModel):
    """A persisted profile record, separate from the workflow bytes."""

    id: StrictStr = Field(pattern=r"[a-z0-9][a-z0-9-]{0,63}")
    kind: StrictStr = Field(min_length=1)
    contract_version: Literal[1] = 1
    workflow_sha256: StrictStr = Field(pattern=r"[0-9a-f]{64}")
    mapping: ImageBoundaryMapping
    status: Literal["draft", "mapped", "validated", "tested", "active", "broken"]
    # Named graph variant (e.g. "text" vs "reference" for actor, phase 2).
    # Phase 1 profiles use a single graph; the field keeps the record
    # shape stable so variants do not need a schema migration later.
    variant: StrictStr = Field(default="default", min_length=1)
    # Semantic inputs that must be present for this profile to apply.
    # Variant selection (phase 2) picks the first active profile whose
    # requirements are satisfied by the caller's inputs.
    requires_inputs: list[StrictStr] = Field(default_factory=list, max_length=8)


class ImageAnalysisIssue(_StrictModel):
    """One deterministic reason a workflow cannot be mapped or used."""

    code: StrictStr = Field(min_length=1)
    message: StrictStr = Field(min_length=1)
    node_id: StrictStr | None = None
    input_name: StrictStr | None = None


class ImageNodeCandidate(_StrictModel):
    """A graph node eligible for one semantic boundary role."""

    role: StrictStr = Field(min_length=1)
    node_id: StrictStr = Field(min_length=1)
    class_type: StrictStr = Field(min_length=1)
    input_name: StrictStr = ""
    title: StrictStr = ""
