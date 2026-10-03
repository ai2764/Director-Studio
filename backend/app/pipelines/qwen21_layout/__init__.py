from .pipeline import Qwen21LayoutPipeline
from ..registry import register_pipeline

QWEN21_LAYOUT_PIPELINE = register_pipeline(Qwen21LayoutPipeline())

__all__ = ["QWEN21_LAYOUT_PIPELINE", "Qwen21LayoutPipeline"]
