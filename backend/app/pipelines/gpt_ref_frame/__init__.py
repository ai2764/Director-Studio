from .pipeline import GptRefFramePipeline
from ..registry import register_pipeline

GPT_REF_FRAME_PIPELINE = register_pipeline(GptRefFramePipeline())

__all__ = ["GPT_REF_FRAME_PIPELINE", "GptRefFramePipeline"]
