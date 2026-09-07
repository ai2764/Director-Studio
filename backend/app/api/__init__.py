from fastapi import APIRouter

from ..pipelines.actor.router import router as actor_router
from ..pipelines.h3_ref2va.router import router as h3_ref2va_router
from ..pipelines.prop.router import router as prop_router
from ..pipelines.scene.router import router as scene_router
from .director import router as director_router
from .files import router as files_router
from .health import router as health_router
from .h3_workflow_profiles import router as h3_workflow_profiles_router
from .json_production import router as json_production_router
from .library import router as library_router
from .pipelines import router as pipelines_router
from .projects import router as projects_router


def build_api_router() -> APIRouter:
    api = APIRouter(prefix="/api")
    api.include_router(health_router)
    api.include_router(pipelines_router)
    api.include_router(files_router)
    api.include_router(actor_router)
    api.include_router(scene_router)
    api.include_router(prop_router)
    api.include_router(h3_ref2va_router)
    api.include_router(h3_workflow_profiles_router)
    api.include_router(projects_router)
    api.include_router(json_production_router)
    api.include_router(director_router)
    api.include_router(library_router)
    return api
