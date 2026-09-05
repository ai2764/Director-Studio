"""Feature pipelines (actor, costume, scene, …). Import to register."""

from . import actor as _actor  # noqa: F401
from . import ref_frame as _ref_frame  # noqa: F401
from . import gpt_ref_frame as _gpt_ref_frame  # noqa: F401
from . import gpt_actor as _gpt_actor  # noqa: F401
from . import h3_ref2va as _h3_ref2va  # noqa: F401
from . import prop as _prop  # noqa: F401
from . import scene as _scene  # noqa: F401
from .registry import all_pipelines, get_pipeline, register_pipeline

__all__ = ["all_pipelines", "get_pipeline", "register_pipeline"]
