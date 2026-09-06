"""Public H3 workflow-profile contracts and profile store."""

from .errors import (
    ProfileChangedError,
    ProfileStateError,
    ProfileStorageError,
    ProfileWarning,
)
from .models import (
    H3AnalysisIssue,
    H3BoundaryMapping,
    H3InputMapping,
    H3OutputSelection,
    H3FixedDependency,
    H3NodeCandidate,
    H3ValidationIssue,
    H3WorkflowAnalysis,
    H3WorkflowProfile,
    ResolvedH3Profile,
    ValidationReport,
)
from .store import (
    H3ProfileStore,
    load_job_profile_snapshot,
    resolve_active_h3_profile,
    snapshot_profile_for_job,
)

__all__ = [
    "H3AnalysisIssue",
    "H3BoundaryMapping",
    "H3InputMapping",
    "H3FixedDependency",
    "H3NodeCandidate",
    "H3ProfileStore",
    "H3OutputSelection",
    "H3ValidationIssue",
    "H3WorkflowAnalysis",
    "H3WorkflowProfile",
    "ProfileChangedError",
    "ProfileStateError",
    "ProfileStorageError",
    "ProfileWarning",
    "ResolvedH3Profile",
    "ValidationReport",
    "load_job_profile_snapshot",
    "resolve_active_h3_profile",
    "snapshot_profile_for_job",
]
