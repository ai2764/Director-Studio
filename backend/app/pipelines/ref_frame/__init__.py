from .pipeline import RefFramePipeline
from ..registry import register_pipeline

REF_FRAME_PIPELINE = register_pipeline(RefFramePipeline())

__all__ = ["REF_FRAME_PIPELINE", "RefFramePipeline"]
