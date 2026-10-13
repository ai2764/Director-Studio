"""Per-workflow dependency status + download jobs (models over HTTP, node packs via git/zip).

Everything is fetched from the original publisher (Hugging Face / ModelScope / GitHub) on the
user's request; DS redistributes nothing. Installs are only done for the managed (bundled)
ComfyUI; for an external or remote ComfyUI the status is read from its API and links are shown.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any
from urllib.parse import urlsplit
import zipfile

import httpx

from ...config import settings
from ..comfy.runtime import comfy_dir, models_dir, user_custom_nodes_dir
from . import catalog

log = logging.getLogger("director_studio.deps")

CHUNK = 1 << 20
MARKER = ".ds-pack.json"
ACTIVE = {"queued", "downloading", "verifying", "installing"}


def _pack_note(entry: dict[str, Any], state: str | None, mode: str) -> dict[str, str | None]:
    """Catalog note, adjusted to where the pack has to come from.

    The catalog notes describe the managed (built-in) ComfyUI. For the user's own local or
    remote ComfyUI, "installed with the app / preinstalled" is wrong when the pack is missing:
    DS cannot install there, the user has to put the pack into that ComfyUI's custom_nodes.
    An "update ComfyUI" note for an already-present core is noise.
    """
    note = {"zh": entry.get("note_zh"), "en": entry.get("note_en")}
    if state == "installed" and entry.get("id") == "comfy-core":
        return {"zh": None, "en": None}
    if mode != "managed" and state != "installed" and entry.get("bundled"):
        if entry.get("id") == "ds_nodes":
            return {"zh": "当前 ComfyUI 不是 DS 内置的：请把 DS 程序目录中的 comfy_nodes/ds_nodes 文件夹复制到"
                          "运行 ComfyUI 的电脑上的 ComfyUI/custom_nodes/，然后重启 ComfyUI（已装 Comfyroll 也可）。",
                    "en": "This ComfyUI is not DS's built-in one: copy the comfy_nodes/ds_nodes folder from the DS "
                          "install folder into ComfyUI/custom_nodes/ on the machine running ComfyUI, then restart "
                          "ComfyUI (Comfyroll also works)."}
        return {"zh": "当前 ComfyUI 不是 DS 内置的：请在运行 ComfyUI 的电脑上从“来源”安装此节点包并重启 ComfyUI。",
                "en": "This ComfyUI is not DS's built-in one: install this pack from its source on the machine "
                      "running ComfyUI, then restart ComfyUI."}
    return note


class DepsError(Exception):
    pass


LLM_GROUP = "llm:director"


def _key(kind: str, item_id: str) -> str:
    return f"{kind}:{item_id}"


def hf_endpoint() -> str:
    return (settings.deps_hf_endpoint or settings.comfy_hf_endpoint or catalog.HF_ENDPOINT).strip().rstrip("/")


def _auth_for(url: str) -> dict[str, str]:
    """The HF token goes only to Hugging Face or the configured HF mirror - never to other hosts."""
    token = (settings.hf_token or "").strip()
    if not token:
        return {}
    host = urlsplit(url).hostname or ""
    allowed = {"huggingface.co", urlsplit(hf_endpoint()).hostname or ""}
    return {"Authorization": f"Bearer {token}"} if host in allowed else {}


def _redact(text: str) -> str:
    token = (settings.hf_token or "").strip()
    return text.replace(token, "***") if token else text


class Job:
    def __init__(self, kind: str, item_id: str, total: int | None) -> None:
        self.kind, self.id = kind, item_id
        self.state = "queued"
        self.done = 0
        self.total = total
        self.error: str | None = None
        self.started = time.time()
        self.task: asyncio.Task | None = None
        self.source: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": self.id, "state": self.state, "done": self.done, "total": self.total,
                "error": self.error, "source": self.source}


def install_mode() -> str:
    """managed: DS can install into its own ComfyUI; otherwise status-only."""
    if settings.comfy_mode == "managed":
        return "managed"
    return "remote" if settings.comfy_mode == "remote" else "external"


def comfy_python() -> Path | None:
    from ..comfy import launch_options as lo

    directory = comfy_dir(settings)
    return lo.find_comfy_python(directory, settings.comfy_python) if directory else None


def bundled_pack_dir(pack_id: str) -> Path | None:
    directory = comfy_dir(settings)
    candidate = directory / "custom_nodes" / pack_id if directory else None
    return candidate if candidate and candidate.is_dir() else None


class DepsManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self._sem: asyncio.Semaphore | None = None
        self._sem_size = 0
        self.restart_needed = False

    # ---- status ------------------------------------------------------------------
    def _semaphore(self) -> asyncio.Semaphore:
        size = max(1, min(4, int(settings.deps_concurrency or 2)))
        if self._sem is None or self._sem_size != size:
            self._sem, self._sem_size = asyncio.Semaphore(size), size
        return self._sem

    def model_path(self, entry: dict[str, Any]) -> Path:
        if entry.get("llm"):
            from ..llm.llama_swap_runtime import llm_models_dir

            return llm_models_dir(settings) / entry["file"]
        return models_dir(settings) / entry["folder"] / entry["file"]

    def pack_path(self, entry: dict[str, Any]) -> Path:
        return user_custom_nodes_dir(settings) / entry["id"]

    def _model_state(self, entry: dict[str, Any], remote_models: dict[str, set[str]] | None) -> dict[str, Any]:
        if remote_models is not None and not entry.get("llm"):  # GGUF LLMs always live on this machine
            files = remote_models.get(entry["folder"])
            state = "unknown" if files is None else ("installed" if entry["file"] in files else "missing")
            return {"state": state, "path": None, "partial": 0}
        path = self.model_path(entry)
        part = path.with_name(path.name + ".part")
        if path.is_file():
            size_ok = not entry.get("size") or path.stat().st_size == entry["size"]
            return {"state": "installed" if size_ok else "size_mismatch", "path": str(path), "partial": 0}
        return {"state": "missing", "path": str(path), "partial": part.stat().st_size if part.is_file() else 0}

    def _pack_state(self, entry: dict[str, Any], object_info: dict[str, Any] | None) -> dict[str, Any]:
        registered = bool(object_info) and all(c in object_info for c in entry["provides"][:1])
        if entry.get("ships_with") in {"ds", "comfyui"}:
            state = "installed" if (registered or object_info is None) else "missing"
            return {"state": state, "path": None, "registered": registered}
        path = self.pack_path(entry)
        on_disk = (path / MARKER).is_file() or (path / "__init__.py").is_file()
        bundled = bundled_pack_dir(entry["id"]) if install_mode() == "managed" else None
        if registered or bundled:
            # preinstalled by the installer (runtime/ComfyUI/custom_nodes) counts as installed
            state = "installed"
        elif on_disk:
            state = "installed" if object_info is None else "restart_needed"
        else:
            state = "missing"
        return {"state": state, "path": str(path) if install_mode() != "remote" else None,
                "registered": registered, "bundled": bool(bundled)}

    async def _comfy_view(self) -> tuple[dict[str, Any] | None, dict[str, set[str]] | None]:
        """object_info (None when ComfyUI is unreachable) and, for non-managed modes, model lists."""
        base = settings.comfy_base_url.rstrip("/")
        object_info: dict[str, Any] | None = None
        remote_models: dict[str, set[str]] | None = None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=3.0), trust_env=False) as client:
                response = await client.get(f"{base}/object_info")
                response.raise_for_status()
                object_info = response.json()
                if install_mode() != "managed":
                    remote_models = {}
                    for folder in sorted({m["folder"] for m in catalog.models() if not m.get("llm")}):
                        r = await client.get(f"{base}/models/{folder}")
                        if r.status_code == 200 and isinstance(r.json(), list):
                            remote_models[folder] = {str(x) for x in r.json()}
        except (httpx.HTTPError, ValueError):
            if install_mode() != "managed":
                remote_models = {}
        return object_info, remote_models

    async def status(self) -> dict[str, Any]:
        object_info, remote_models = await self._comfy_view()
        mode = install_mode()
        models = {m["file"]: m for m in catalog.models()}
        workflows = []
        for wf in catalog.workflows():
            items = []
            for pack_id in wf["node_packs"]:
                entry = catalog.pack(pack_id)
                st = self._pack_state(entry, object_info)
                job = self.jobs.get(_key("pack", pack_id))
                items.append({"kind": "pack", "id": pack_id, "name": entry["name"], "license": entry.get("license"),
                              "unlicensed": entry.get("unlicensed", False), "installable": entry.get("installable", False),
                              "preinstalled": bool(entry.get("bundled")),
                              "repo": entry.get("repo"), "ref": entry.get("ref"), "size": None,
                              "note": _pack_note(entry, st.get("state"), mode),
                              **st, **({"job": job.as_dict()} if job else {})})
            for file in wf["models"]:
                entry = models[file]
                st = self._model_state(entry, remote_models)
                job = self.jobs.get(_key("model", file))
                hf = catalog.hf_source(entry)
                items.append({"kind": "model", "id": file, "name": file, "folder": entry["folder"],
                              "size": entry.get("size"), "sha256": entry.get("sha256"), "license": entry.get("license"),
                              "license_url": entry.get("license_url"), "gated": entry.get("gated", False),
                              "community": entry.get("community", False), "installable": True,
                              "page": catalog.page_url(entry), "url": catalog.model_urls(entry)[0] if hf else None,
                              **st, **({"job": job.as_dict()} if job else {})})
            missing = [i for i in items if i["state"] in {"missing", "size_mismatch"}]
            workflows.append({"file": wf["file"], "zh": wf["zh"], "en": wf["en"],
                              "ready": not missing and all(i["state"] == "installed" for i in items),
                              "missing_bytes": sum((i.get("size") or 0) - (i.get("partial") or 0) for i in missing),
                              "items": items})
        workflows.append(self._llm_group())
        return {"mode": mode, "can_install": mode == "managed", "comfy_reachable": object_info is not None,
                "hf_endpoint": hf_endpoint(), "hf_token_set": bool((settings.hf_token or "").strip()),
                "models_dir": str(models_dir(settings)), "custom_nodes_dir": str(user_custom_nodes_dir(settings)),
                "restart_needed": self.restart_needed, "concurrency": self._sem_size or settings.deps_concurrency,
                "workflows": workflows, "jobs": [j.as_dict() for j in self.jobs.values()]}

    def _llm_group(self) -> dict[str, Any]:
        """Recommended GGUF director LLMs (built-in llama.cpp), shown like a workflow group."""
        items = []
        for entry in catalog.llm_models():
            st = self._model_state(entry, None)
            job = self.jobs.get(_key("model", entry["file"]))
            items.append({"kind": "model", "id": entry["file"], "name": entry["file"], "folder": "llm",
                          "size": entry.get("size"), "sha256": entry.get("sha256"), "license": entry.get("license"),
                          "license_url": entry.get("license_url"), "gated": entry.get("gated", False),
                          "community": entry.get("community", False), "installable": True,
                          "page": catalog.page_url(entry), "url": catalog.model_urls(entry)[0],
                          "note": {"zh": entry.get("note_zh"), "en": entry.get("note_en")},
                          "llm": entry.get("llm"), "optional": True,
                          **st, **({"job": job.as_dict()} if job else {})})
        installed = [i for i in items if i["state"] == "installed"]
        return {"file": LLM_GROUP, "zh": "导演大模型（内置 llama.cpp）",
                "en": "Director LLM (built-in llama.cpp)", "kind": "llm",
                "ready": bool(installed), "missing_bytes": 0 if installed else min((i.get("size") or 0) for i in items),
                "items": items}

    # ---- jobs ----------------------------------------------------------------------
    def start(self, kind: str, item_id: str) -> Job:
        llm_entry = catalog.model(item_id) if kind == "model" else None
        if install_mode() != "managed" and not (llm_entry and llm_entry.get("llm")):
            raise DepsError("installs are only available for the managed (built-in) ComfyUI")
        key = _key(kind, item_id)
        existing = self.jobs.get(key)
        if existing and existing.state in ACTIVE:
            return existing
        if kind == "model":
            entry = catalog.model(item_id)
            if entry is None:
                raise DepsError(f"unknown model {item_id}")
            job = Job(kind, item_id, entry.get("size"))
            coro = self._run_model(job, entry)
        elif kind == "pack":
            entry = catalog.pack(item_id)
            if entry is None or not entry.get("installable"):
                raise DepsError(f"node pack {item_id} cannot be installed by DS")
            job = Job(kind, item_id, None)
            coro = self._run_pack(job, entry)
        else:
            raise DepsError(f"unknown kind {kind}")
        self.jobs[key] = job
        job.task = asyncio.create_task(self._guarded(job, coro))
        return job

    async def start_workflow(self, file: str) -> list[Job]:
        snapshot = await self.status()
        wf = next((w for w in snapshot["workflows"] if w["file"] == file), None)
        if wf is None:
            raise DepsError(f"unknown workflow {file}")
        if wf.get("kind") == "llm":  # fetch the default (first) recommended model only
            return [] if wf["ready"] else [self.start("model", wf["items"][0]["id"])]
        return [self.start(i["kind"], i["id"]) for i in wf["items"]
                if i["installable"] and i["state"] in {"missing", "size_mismatch"}]

    def cancel(self, kind: str, item_id: str) -> bool:
        job = self.jobs.get(_key(kind, item_id))
        if not job or job.state not in ACTIVE or job.task is None:
            return False
        job.task.cancel()
        return True

    async def _guarded(self, job: Job, coro) -> None:
        try:
            async with self._semaphore():
                await coro
            job.state = "done"
        except asyncio.CancelledError:
            job.state = "cancelled"
            coro.close()
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            job.state = "failed"
            job.error = _redact(f"{type(exc).__name__}: {exc}")[:500]
            log.warning("deps: %s %s failed: %s", job.kind, job.id, job.error)

    # ---- models --------------------------------------------------------------------
    async def _run_model(self, job: Job, entry: dict[str, Any]) -> None:
        target = self.model_path(entry)
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        errors: list[str] = []
        for url in catalog.model_urls(entry, hf_endpoint=hf_endpoint()):
            job.source = urlsplit(url).hostname
            try:
                await download_file(url, part, job, expected_size=entry.get("size"), headers=_auth_for(url))
                break
            except asyncio.CancelledError:
                raise
            except _GatedError as exc:
                raise DepsError(str(exc)) from None
            except (httpx.HTTPError, DepsError, OSError) as exc:
                errors.append(f"{job.source}: {_redact(str(exc))[:200]}")
                log.info("deps: %s from %s failed, trying next source", entry["file"], job.source)
        else:
            raise DepsError("; ".join(errors) or "no download source")
        job.state = "verifying"
        digest = await asyncio.to_thread(sha256_file, part)
        if entry.get("sha256") and digest != entry["sha256"]:
            part.unlink(missing_ok=True)
            raise DepsError(f"sha256 mismatch ({digest[:12]}… != {entry['sha256'][:12]}…); partial file removed")
        os.replace(part, target)
        log.info("deps: installed model %s -> %s", entry["file"], target)
        if entry.get("llm"):
            from ..llm.llama_swap_runtime import llama_swap_runtime

            llama_swap_runtime.refresh_config()  # llama-swap hot-reloads the new model

    # ---- node packs ----------------------------------------------------------------
    async def _run_pack(self, job: Job, entry: dict[str, Any]) -> None:
        dest_root = user_custom_nodes_dir(settings)
        dest_root.mkdir(parents=True, exist_ok=True)
        target = dest_root / entry["id"]
        if (target / MARKER).is_file():
            marker = json.loads((target / MARKER).read_text(encoding="utf-8"))
            if marker.get("commit") == entry["commit"]:
                return
        tmp = dest_root / f".{entry['id']}.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        job.state = "downloading"
        try:
            job.source = await asyncio.to_thread(fetch_pack, entry, tmp)
            job.state = "installing"
            await asyncio.to_thread(install_requirements, entry, tmp)
            (tmp / MARKER).write_text(json.dumps({"id": entry["id"], "repo": entry["repo"], "commit": entry["commit"],
                                                  "license": entry.get("license"), "installed_at": time.time()}),
                                      encoding="utf-8")
            if target.exists():
                old = dest_root / f".{entry['id']}.old"
                shutil.rmtree(old, ignore_errors=True)
                target.rename(old)
                shutil.rmtree(old, ignore_errors=True)
            tmp.rename(target)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.restart_needed = True
        log.info("deps: installed node pack %s@%s -> %s", entry["id"], entry["commit"][:12], target)


class _GatedError(DepsError):
    pass


async def download_file(url: str, part: Path, job: Job, *, expected_size: int | None,
                        headers: dict[str, str]) -> None:
    """Resumable download into ``part`` (HTTP Range); raises on HTTP errors or a short file."""
    have = part.stat().st_size if part.is_file() else 0
    if expected_size and have > expected_size:
        part.unlink()
        have = 0
    job.state = "downloading"
    job.done = have
    req_headers = {"User-Agent": "DirectorStudio", **headers}
    if have:
        req_headers["Range"] = f"bytes={have}-"
    if expected_size and have == expected_size:
        return
    timeout = httpx.Timeout(60.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("GET", url, headers=req_headers) as response:
            hf_hosts = {"huggingface.co", urlsplit(hf_endpoint()).hostname or ""}
            if response.status_code in (401, 403) and urlsplit(url).hostname in hf_hosts:
                raise _GatedError("this model is gated: accept its licence on Hugging Face and set a "
                                  "Hugging Face token in Settings → Workflow dependencies")
            if response.status_code == 416 and expected_size and have == expected_size:
                return
            response.raise_for_status()
            mode = "ab"
            if have and response.status_code != 206:  # server ignored Range: start over
                mode, have = "wb", 0
                job.done = 0
            if job.total is None:
                length = response.headers.get("content-length")
                job.total = (have + int(length)) if length and length.isdigit() else None
            with open(part, mode) as fh:
                async for chunk in response.aiter_bytes():  # no re-chunking: keep every received byte for resume
                    fh.write(chunk)
                    job.done += len(chunk)
    size = part.stat().st_size
    if expected_size and size != expected_size:
        raise DepsError(f"incomplete download ({size} of {expected_size} bytes)")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK * 4), b""):
            h.update(block)
    return h.hexdigest()


def _run(cmd: list[str], cwd: Path | None = None, timeout: float = 600) -> subprocess.CompletedProcess:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, creationflags=flags,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def fetch_pack(entry: dict[str, Any], dest: Path) -> str:
    """Pinned checkout of a node pack into ``dest``: git (shallow fetch of the commit), else GitHub zip."""
    git = shutil.which("git")
    if git:
        dest.mkdir(parents=True)
        steps = [[git, "init", "-q"], [git, "remote", "add", "origin", entry["repo"]],
                 [git, "fetch", "-q", "--depth", "1", "origin", entry["commit"]],
                 [git, "-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD"]]
        for step in steps:
            result = _run(step, cwd=dest)
            if result.returncode != 0:
                log.info("deps: git failed (%s); falling back to zip", result.stderr.strip()[:200])
                shutil.rmtree(dest, ignore_errors=True)
                break
        else:
            return "git"
    slug = entry["repo"].removeprefix("https://github.com/").removesuffix(".git")
    url = f"https://codeload.github.com/{slug}/zip/{entry['commit']}"
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0), follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        root = archive.namelist()[0].split("/")[0]
        stage = dest.with_name(dest.name + ".zip")
        shutil.rmtree(stage, ignore_errors=True)
        archive.extractall(stage)
        (stage / root).rename(dest)
        shutil.rmtree(stage, ignore_errors=True)
    return "zip"


def install_requirements(entry: dict[str, Any], pack_dir: Path) -> None:
    files = [pack_dir / name for name in entry.get("requirements") or [] if (pack_dir / name).is_file()]
    if not files:
        return
    python = comfy_python()
    if python is None:
        raise DepsError("ComfyUI Python not found: cannot install the node pack's requirements")
    uv = shutil.which("uv")
    for req in files:
        cmd = ([uv, "pip", "install", "--python", str(python), "-r", str(req)] if uv
               else [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(req)])
        result = _run(cmd, timeout=1800)
        if result.returncode != 0 and uv:  # bundled venvs without uv metadata: retry with pip
            result = _run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(req)],
                          timeout=1800)
        if result.returncode != 0:
            raise DepsError(f"requirements install failed: {(result.stderr or result.stdout).strip()[-400:]}")


deps_manager = DepsManager()
