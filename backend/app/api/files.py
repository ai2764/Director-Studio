from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..core.storage_files import resolve_data_file

router = APIRouter(tags=["files"])


@router.get("/files/{file_path:path}")
async def get_file(file_path: str) -> FileResponse:
    full = resolve_data_file(file_path)
    if not full:
        raise HTTPException(404, "File not found")
    return FileResponse(full)
