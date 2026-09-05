from .pipeline import ScenePipeline
from ..registry import register_pipeline

SCENE_PIPELINE = register_pipeline(ScenePipeline())

__all__ = ["SCENE_PIPELINE", "ScenePipeline"]
