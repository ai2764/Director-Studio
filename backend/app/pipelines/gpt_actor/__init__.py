from .pipeline import GptActorPipeline
from ..registry import register_pipeline

GPT_ACTOR_PIPELINE = register_pipeline(GptActorPipeline())

__all__ = ["GPT_ACTOR_PIPELINE", "GptActorPipeline"]
