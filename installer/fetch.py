"""Download + verify + extract helpers for the DS installer (stdlib + optional zstandard)."""

from __future__ import annotations

import fnmatch
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
from typing import Iterable, Sequence
import urllib.request
import zipfile

DEFAULT_CACHE = Path(os.environ.get("DS_INSTALLER_CACHE", Path.home() / ".cache" / "ds-installer"))


class FetchError(RuntimeError):
    pass


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cached_download(url: str, sha256: str, *, cache: Path = DEFAULT_CACHE, name: str | None = None) -> Path:
    """Download ``url`` into the cache (once) and verify its sha256."""
    cache.mkdir(parents=True, exist_ok=True)
    filename = name or urllib.request.url2pathname(url.rsplit("/", 1)[-1]).replace("%2B", "+")
    target = cache / filename
    if target.is_file() and sha256_of(target) == sha256:
        return target
    tmp = target.with_name(target.name + ".part")
    print(f"[fetch] downloading {url}", flush=True)
    request = urllib.request.Request(url, headers={"User-Agent": "director-studio-installer"})
    with urllib.request.urlopen(request, timeout=60) as response, open(tmp, "wb") as out:
        shutil.copyfileobj(response, out, 1 << 20)
    actual = sha256_of(tmp)
    if actual != sha256:
        tmp.unlink(missing_ok=True)
        raise FetchError(f"sha256 mismatch for {url}: expected {sha256}, got {actual}")
    os.replace(tmp, target)
    return target


def _safe_relative(name: str, strip: int) -> PurePosixPath | None:
    parts = [p for p in PurePosixPath(name.replace("\\", "/")).parts if p not in ("", ".")]
    if any(p == ".." for p in parts) or name.startswith("/"):
        raise FetchError(f"unsafe archive member: {name}")
    parts = parts[strip:]
    return PurePosixPath(*parts) if parts else None


def _selected(rel: PurePosixPath, include: Sequence[str] | None, exclude: Sequence[str]) -> bool:
    text = rel.as_posix()
    if any(fnmatch.fnmatch(text, pattern) or fnmatch.fnmatch(text + "/", pattern) for pattern in exclude):
        return False
    if include:
        return any(fnmatch.fnmatch(text, pattern) for pattern in include)
    return True


def _open_tar(archive: Path) -> tuple[tarfile.TarFile, object | None]:
    if archive.name.endswith(".zst"):
        try:
            import zstandard  # BSD-3-Clause; build-time only
        except ImportError as exc:  # pragma: no cover
            raise FetchError("python package 'zstandard' is required to unpack .tar.zst archives") from exc
        handle = open(archive, "rb")
        reader = zstandard.ZstdDecompressor().stream_reader(handle)
        return tarfile.open(fileobj=reader, mode="r|"), handle
    return tarfile.open(archive, mode="r:*"), None


def extract(
    archive: Path,
    destination: Path,
    *,
    strip_components: int = 0,
    include: Sequence[str] | None = None,
    exclude: Iterable[str] = (),
) -> list[str]:
    """Extract ``archive`` (tar.gz/xz/zst or zip) into ``destination`` safely.

    Members are path-checked (no absolute paths / ``..``), links must stay inside
    the destination, and include/exclude globs apply to the stripped path.
    Returns the extracted relative paths.
    """
    exclude = list(exclude)
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    written: list[str] = []
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                rel = _safe_relative(info.filename, strip_components)
                if rel is None or info.is_dir() or not _selected(rel, include, exclude):
                    continue
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                mode = (info.external_attr >> 16) & 0o777
                if mode:
                    os.chmod(target, mode)
                written.append(rel.as_posix())
        return written
    tar, handle = _open_tar(archive)
    try:
        for member in tar:
            rel = _safe_relative(member.name, strip_components)
            if rel is None or not _selected(rel, include, exclude):
                continue
            target = root / rel
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.issym() or member.islnk():
                link = member.linkname
                if member.islnk():
                    link_rel = _safe_relative(link, strip_components)
                    if link_rel is None:
                        continue
                    source = root / link_rel
                    if target.exists() or target.is_symlink():
                        target.unlink()
                    if source.exists():
                        os.link(source, target)
                    written.append(rel.as_posix())
                    continue
                resolved = (target.parent / link).resolve()
                if not str(resolved).startswith(str(root)):
                    raise FetchError(f"symlink escapes destination: {member.name} -> {link}")
                if target.exists() or target.is_symlink():
                    target.unlink()
                os.symlink(link, target)
                written.append(rel.as_posix())
                continue
            if not member.isfile():
                continue
            source = tar.extractfile(member)
            assert source is not None
            with open(target, "wb") as out:
                shutil.copyfileobj(source, out, 1 << 20)
            os.chmod(target, member.mode & 0o777 or 0o644)
            written.append(rel.as_posix())
    finally:
        tar.close()
        if handle is not None:
            handle.close()
    return written


def select_variant(spec: dict, variant: str | None) -> dict:
    """A platform entry may carry ``variants: {name|"*": entry}`` (e.g. CUDA vs CPU llama.cpp)."""
    variants = spec.get("variants")
    if not variants:
        return spec
    chosen = variants.get(variant or "") or variants.get("*")
    if chosen is None:
        raise FetchError(f"no build for variant {variant!r} (have {sorted(variants)})")
    return chosen


def install_component(
    spec: dict, destination: Path, *, cache: Path = DEFAULT_CACHE, variant: str | None = None,
) -> list[str]:
    """Fetch + extract one manifest platform entry into ``destination`` (replaced atomically).

    ``extras`` (each with url/sha256/strip_components/include) are extracted into the same
    folder, e.g. NVIDIA's cudart redistributables next to llama-server.exe.
    """
    spec = select_variant(spec, variant)
    archive = cached_download(spec["url"], spec["sha256"], cache=cache)
    exclude = list(spec.get("exclude", []))
    if variant and "exclude_by_variant" in spec:
        exclude += spec["exclude_by_variant"].get(variant, [])
    extras = [(cached_download(e["url"], e["sha256"], cache=cache), e) for e in spec.get("extras", [])]
    staging = Path(tempfile.mkdtemp(prefix=destination.name + ".staging-", dir=destination.parent
                                    if destination.parent.exists() else None))
    try:
        written = extract(archive, staging, strip_components=int(spec.get("strip_components", 0)),
                          include=spec.get("include"), exclude=exclude)
        for extra_archive, extra in extras:
            written += extract(extra_archive, staging, strip_components=int(extra.get("strip_components", 0)),
                               include=extra.get("include"), exclude=extra.get("exclude", []))
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return written
