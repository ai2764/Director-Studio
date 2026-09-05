from .pipeline import PropPipeline
from ..registry import register_pipeline

PROP_PIPELINE = register_pipeline(PropPipeline())

__all__ = ["PROP_PIPELINE", "PropPipeline"]
