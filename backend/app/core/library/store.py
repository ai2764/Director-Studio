from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..jobs.store import job_dir, save_job
from ..paths import (
    asset_write_dir,
    find_asset_dir,
    iter_asset_dirs,
)
from ..schemas import JobRecord, JobStatus, LibraryAsset
from .audio import normalize_voice_reference


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_asset_id(kind: str) -> str:
    # short kind prefix for readability: actors -> act, costumes -> cos
    prefixes = {
        "actors": "act",
        "costumes": "cos",
        "scenes": "scn",
        "props": "prp",
        "layouts": "lay",
        "voices": "voi",
    }
    prefix = prefixes.get(kind, kind[:3])
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def asset_dir(kind: str, asset_id: str, *, project_id: str | None = None) -> Path:
    """
    Directory for an asset.

    - If project_id given: canonical write path under that project.
    - Else: locate existing folder (project or global), or default global write path.
    """
    if project_id:
        return asset_write_dir(kind, asset_id, project_id=project_id)
    found = find_asset_dir(kind, asset_id)
    if found is not None:
        return found
    return asset_write_dir(kind, asset_id, project_id=None)


def save_asset_from_job(
    job: JobRecord,
    *,
    name: str | None = None,
    notes: str | None = None,
    file_keys: list[str] | None = None,
    input_keys: list[str] | None = None,
    meta: dict[str, Any] | None = None,
    project_id: str | None = None,
) -> LibraryAsset:
    if job.status != JobStatus.succeeded:
        raise ValueError("Can only save succeeded jobs")

    kind = job.asset_kind
    asset_id = new_asset_id(kind)

    # Prefer explicit project_id, then job.project_id, then params fallback
    resolved_project = (
        project_id
        or job.project_id
        or (job.params or {}).get("project_id")
        or None
    )
    if isinstance(resolved_project, str):
        resolved_project = resolved_project.strip() or None

    adir = asset_write_dir(kind, asset_id, project_id=resolved_project)
    adir.mkdir(parents=True, exist_ok=True)

    files: dict[str, str | None] = {}
    jout = job_dir(job.id, project_id=job.project_id) / "outputs"
    jin = job_dir(job.id, project_id=job.project_id) / "inputs"

    keys = file_keys or list(job.outputs.keys())
    for key in keys:
        srcs = list(jout.glob(f"{key}.*")) if jout.exists() else []
        if srcs:
            dest = adir / srcs[0].name
            shutil.copy2(srcs[0], dest)
            files[key] = dest.name

    for kind_in in input_keys or []:
        srcs = list(jin.glob(f"{kind_in}.*")) if jin.exists() else []
        if srcs:
            dest_name = f"input_{srcs[0].name}"
            dest = adir / dest_name
            shutil.copy2(srcs[0], dest)
            files[f"input_{kind_in}"] = dest_name

    asset = LibraryAsset(
        id=asset_id,
        kind=kind,
        name=(name or job.name).strip(),
        notes=notes if notes is not None else job.notes,
        pipeline_id=job.pipeline_id,
        job_id=job.id,
        seed=job.seed,
        created_at=_now(),
        files=files,
        meta=meta if meta is not None else dict(job.params),
        project_id=resolved_project,
    )
    asset.urls = _asset_urls(asset)
    _write_asset(asset)

    job.library_asset_id = asset_id
    if not job.project_id and resolved_project:
        job.project_id = resolved_project
    save_job(job)
    return asset


def _write_asset(asset: LibraryAsset) -> None:
    adir = asset_write_dir(asset.kind, asset.id, project_id=asset.project_id)
    adir.mkdir(parents=True, exist_ok=True)
    asset.urls = _asset_urls(asset)
    # Always include project_id key even when null (clear ownership in JSON)
    payload = asset.model_dump(mode="json")
    (adir / "asset.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_asset(asset: LibraryAsset) -> LibraryAsset:
    """Public persist helper (e.g. after meta tweaks on import / insert)."""
    _write_asset(asset)
    return load_asset(asset.kind, asset.id) or asset


def assign_asset_project(
    kind: str,
    asset_id: str,
    project_id: str | None,
) -> LibraryAsset:
    """Set or clear project ownership; move files into project-rooted library."""
    asset = load_asset(kind, asset_id)
    if asset is None:
        raise ValueError(f"asset not found: {kind}/{asset_id}")
    pid = (project_id or "").strip() or None
    old_dir = find_asset_dir(kind, asset_id)
    asset = asset.model_copy(update={"project_id": pid})
    new_dir = asset_write_dir(kind, asset_id, project_id=pid)
    if old_dir is not None and old_dir.resolve() != new_dir.resolve():
        new_dir.parent.mkdir(parents=True, exist_ok=True)
        if new_dir.exists():
            shutil.rmtree(new_dir)
        shutil.move(str(old_dir), str(new_dir))
    _write_asset(asset)
    return asset


def delete_asset(kind: str, asset_id: str) -> None:
    """Permanently remove a library asset directory (files + asset.json)."""
    adir = find_asset_dir(kind, asset_id)
    if adir is None or not adir.is_dir():
        raise ValueError(f"asset not found: {kind}/{asset_id}")
    shutil.rmtree(adir)


def _asset_urls(asset: LibraryAsset) -> dict[str, str]:
    """Stable API URLs (resolver searches project + global)."""
    urls: dict[str, str] = {}
    for field, val in asset.files.items():
        if val:
            urls[field] = f"/api/files/library/{asset.kind}/{asset.id}/{val}"
    return urls


def load_asset(kind: str, asset_id: str) -> LibraryAsset | None:
    adir = find_asset_dir(kind, asset_id)
    if adir is None:
        return None
    path = adir / "asset.json"
    # Backward compat: actor.json from v0.1
    if not path.exists():
        legacy = adir / "actor.json"
        if legacy.exists():
            return _load_legacy_actor(legacy, asset_id)
        return None
    asset = LibraryAsset.model_validate_json(path.read_text(encoding="utf-8"))
    asset.urls = _asset_urls(asset)
    return asset


def _load_legacy_actor(path: Path, asset_id: str) -> LibraryAsset:
    raw = json.loads(path.read_text(encoding="utf-8"))
    files = raw.get("files") or {}
    if isinstance(files, dict) and "master" in files or any(
        k in files for k in ("master", "asset_sheet")
    ):
        file_map = {k: v for k, v in files.items() if v}
    else:
        file_map = {}
    asset = LibraryAsset(
        id=raw.get("id") or asset_id,
        kind="actors",
        name=raw.get("name") or asset_id,
        notes=raw.get("notes") or "",
        pipeline_id="actor",
        job_id=raw.get("job_id") or "",
        seed=raw.get("seed"),
        created_at=raw.get("created_at") or _now(),
        files=file_map if isinstance(file_map, dict) else {},
        meta={
            "mode": raw.get("mode"),
            "description": raw.get("description") or "",
            "extract_outfit": raw.get("extract_outfit") or False,
        },
    )
    if not asset.files and isinstance(raw.get("files"), dict):
        asset.files = {k: v for k, v in raw["files"].items() if v}
    asset.urls = _asset_urls(asset)
    return asset


_IMPORT_KINDS = frozenset({"actors", "costumes", "scenes", "props", "layouts"})
_VOICE_SUFFIXES = frozenset({".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"})


def create_external_asset(
    *,
    kind: str,
    name: str,
    notes: str = "",
    project_id: str | None = None,
    image_bytes: bytes,
    image_filename: str,
    file_key: str | None = None,
    source_filename: str | None = None,
) -> LibraryAsset:
    """Import a single image as a library asset without pipeline job metadata.

    External packs often only have a filename + short notes — that is enough for
    the Director agent to cast refs. Full casting/set meta is optional.
    """
    kind = (kind or "").strip().lower()
    if kind not in _IMPORT_KINDS:
        raise ValueError(
            f"kind must be one of {sorted(_IMPORT_KINDS)}, got {kind!r}"
        )
    label = (name or "").strip() or (source_filename or image_filename or "external")
    pid = (project_id or "").strip() or None
    asset_id = new_asset_id(kind)

    raw_name = (image_filename or source_filename or "image.png").strip()
    suffix = Path(raw_name).suffix.lower() or ".png"
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        suffix = ".png"
    key = (file_key or "").strip() or ("layout" if kind == "layouts" else "master")
    # Stable short names so UI /api/files/.../master.png previews work
    dest_name = f"layout{suffix}" if kind == "layouts" and key == "layout" else f"{key}{suffix}"

    adir = asset_write_dir(kind, asset_id, project_id=pid)
    adir.mkdir(parents=True, exist_ok=True)
    (adir / dest_name).write_bytes(image_bytes)

    src = (source_filename or image_filename or dest_name).strip()
    asset = LibraryAsset(
        id=asset_id,
        kind=kind,
        name=label,
        notes=(notes or "").strip(),
        pipeline_id="external",
        job_id="",
        seed=None,
        created_at=_now(),
        files={key: dest_name},
        meta={
            "source": "external_import",
            "source_filename": src,
            "description": (notes or "").strip(),
            "external": True,
        },
        project_id=pid,
    )
    _write_asset(asset)
    return load_asset(kind, asset_id) or asset


def create_external_voice_asset(
    *,
    name: str,
    notes: str = "",
    project_id: str | None = None,
    audio_bytes: bytes,
    audio_filename: str,
    source_filename: str | None = None,
) -> LibraryAsset:
    label = (name or "").strip()
    if not label:
        raise ValueError("name is required for Voice assets")
    pid = (project_id or "").strip() or None
    asset_id = new_asset_id("voices")
    suffix = Path(audio_filename or "voice.wav").suffix.lower()
    if suffix not in _VOICE_SUFFIXES:
        raise ValueError("unsupported audio file type")

    adir = asset_write_dir("voices", asset_id, project_id=pid)
    source_name = f"source{suffix}"
    reference_name = "reference.wav"
    try:
        adir.mkdir(parents=True, exist_ok=False)
        source_path = adir / source_name
        source_path.write_bytes(audio_bytes)
        metadata = normalize_voice_reference(source_path, adir / reference_name)
        description = (notes or "").strip()
        asset = LibraryAsset(
            id=asset_id,
            kind="voices",
            name=label,
            notes=description,
            pipeline_id="external",
            job_id="",
            seed=None,
            created_at=_now(),
            files={"source": source_name, "reference": reference_name},
            meta={
                "source": "external_import",
                "source_filename": source_filename or audio_filename,
                "description": description,
                "duration_s": metadata.duration_s,
                "source_format": metadata.source_format,
                "source_sample_rate": metadata.source_sample_rate,
                "source_channels": metadata.source_channels,
                "reference_sample_rate": metadata.reference_sample_rate,
                "reference_channels": metadata.reference_channels,
                "h3_ready": True,
                "external": True,
            },
            project_id=pid,
        )
        _write_asset(asset)
        return load_asset("voices", asset_id) or asset
    except Exception:
        if adir.exists():
            shutil.rmtree(adir)
        raise


def list_assets(
    kind: str,
    *,
    project_id: str | None = None,
    include_unassigned: bool = False,
) -> list[LibraryAsset]:
    """
    List assets of a kind.

    When ``project_id`` is set, return assets under that project's library/
    (and optionally unassigned global assets if ``include_unassigned``).
    """
    items: list[LibraryAsset] = []
    seen: set[str] = set()

    if project_id is not None:
        # Project-rooted folder first
        from ..paths import project_library_kind_dir

        kdir = project_library_kind_dir(project_id, kind)
        if kdir.is_dir():
            for p in sorted(kdir.iterdir(), reverse=True):
                if not p.is_dir():
                    continue
                asset = load_asset(kind, p.name)
                if not asset:
                    continue
                items.append(asset)
                seen.add(asset.id)
        # Also any global-pool assets tagged with this project_id (pre-move)
        for ad in iter_asset_dirs(kind):
            if ad.name in seen:
                continue
            asset = load_asset(kind, ad.name)
            if not asset:
                continue
            ap = asset.project_id or None
            if ap == project_id:
                items.append(asset)
                seen.add(asset.id)
            elif include_unassigned and ap is None:
                items.append(asset)
                seen.add(asset.id)
        return items

    # No filter: everything
    for ad in iter_asset_dirs(kind):
        asset = load_asset(kind, ad.name)
        if asset and asset.id not in seen:
            items.append(asset)
            seen.add(asset.id)
    return items
