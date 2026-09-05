from .pipeline import ActorPipeline
from ..registry import register_pipeline

ACTOR_PIPELINE = register_pipeline(ActorPipeline())

__all__ = ["ACTOR_PIPELINE", "ActorPipeline"]
