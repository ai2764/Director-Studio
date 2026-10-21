"""Custom image workflow profiles: semantic contract + boundary mapping.

Phase 1 covers the ``prop`` and ``layout`` kinds. See ``contracts.py``
for the Studio-owned vocabulary and ``models.py`` for the persisted
profile records.
"""

from .contracts import KIND_CONTRACTS, KindContract, contract_for
from .inspector import ImageInspection, inspect_image_workflow
from .models import (
    ImageAnalysisIssue,
    ImageBoundaryMapping,
    ImageNodeCandidate,
    ImageWorkflowProfile,
)
from .validator import validate_mapping

__all__ = [
    "KIND_CONTRACTS",
    "KindContract",
    "contract_for",
    "ImageInspection",
    "inspect_image_workflow",
    "ImageAnalysisIssue",
    "ImageBoundaryMapping",
    "ImageNodeCandidate",
    "ImageWorkflowProfile",
    "validate_mapping",
]
