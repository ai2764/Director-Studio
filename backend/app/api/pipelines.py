from fastapi import APIRouter

from ..core.schemas import PipelineInfo
from ..pipelines import all_pipelines

router = APIRouter(tags=["pipelines"])


@router.get("/pipelines", response_model=list[PipelineInfo])
async def list_pipelines() -> list[PipelineInfo]:
    """Discover registered feature pipelines (actor, costume, …)."""
    return [
        PipelineInfo(
            id=p.id,
            asset_kind=p.asset_kind,
            display_name=p.display_name,
            description=p.description,
            enabled=p.enabled,
        )
        for p in all_pipelines(enabled_only=False)
    ]
