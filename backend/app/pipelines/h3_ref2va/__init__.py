from .pipeline import H3Ref2VaPipeline
from ..registry import register_pipeline

H3_REF2VA_PIPELINE = register_pipeline(H3Ref2VaPipeline())

__all__ = ["H3_REF2VA_PIPELINE", "H3Ref2VaPipeline"]
