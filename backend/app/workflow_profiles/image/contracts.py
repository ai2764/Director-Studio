"""Studio-owned semantic contracts for image asset kinds.

The contract is the stable vocabulary the Director agent and the library
speak: which semantic inputs a job may supply and which output slots it
must (or may) receive. It never changes when a user swaps in a custom
workflow — only the boundary mapping between this vocabulary and the
user's graph changes (see ``models.py``). Callers (pipelines today, the
agent's tools through them) keep producing and consuming exactly these
roles; how a prompt text is authored (e.g. prop compiles name + notes
into one instruction) stays with the caller, not the profile.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextInputSpec:
    role: str  # "prompt" | "negative"
    required: bool


@dataclass(frozen=True)
class ImageInputSpec:
    role: str  # "reference_images"
    min_count: int
    max_count: int


@dataclass(frozen=True)
class OutputSlotSpec:
    role: str
    label: str
    required: bool


@dataclass(frozen=True)
class KindContract:
    kind: str
    asset_kind: str
    texts: tuple[TextInputSpec, ...]
    images: ImageInputSpec
    outputs: tuple[OutputSlotSpec, ...]
    seed: bool = True

    def output_slot(self, role: str) -> OutputSlotSpec | None:
        return next((s for s in self.outputs if s.role == role), None)

    def required_outputs(self) -> tuple[OutputSlotSpec, ...]:
        return tuple(s for s in self.outputs if s.required)


# Prop: one uploaded photo is prepped into a multi-view reference sheet.
# Fields today: prop_image (required), name, notes -> compiled prompt.
PROP_CONTRACT = KindContract(
    kind="prop",
    asset_kind="props",
    texts=(TextInputSpec("prompt", required=True), TextInputSpec("negative", required=False)),
    images=ImageInputSpec("reference_images", min_count=1, max_count=1),
    outputs=(OutputSlotSpec("master", "Prop Reference Sheet", required=True),),
)

# Layout (Qwen Image 2.1 / reference frame family): a blocking description
# plus optional ordered visual references -> one layout frame.
LAYOUT_CONTRACT = KindContract(
    kind="layout",
    asset_kind="layouts",
    texts=(TextInputSpec("prompt", required=True), TextInputSpec("negative", required=False)),
    images=ImageInputSpec("reference_images", min_count=0, max_count=3),
    outputs=(OutputSlotSpec("layout", "Layout Reference Frame", required=True),),
)

KIND_CONTRACTS: dict[str, KindContract] = {
    contract.kind: contract for contract in (PROP_CONTRACT, LAYOUT_CONTRACT)
}


def contract_for(kind: str) -> KindContract:
    try:
        return KIND_CONTRACTS[kind]
    except KeyError:
        raise ValueError(f"no image workflow contract for kind {kind!r}") from None
