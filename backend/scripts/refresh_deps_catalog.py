"""Refresh backend/app/core/deps/catalog.json from Hugging Face / ModelScope / GitHub.

Dev tool (network): fills size, sha256 (LFS oid), pinned revision, gated flag and licence for
every model, and checks node-pack refs. Hand-edited fields (names, sources, workflows) are kept.

    python backend/scripts/refresh_deps_catalog.py [--offline-ok]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys

import httpx

CATALOG = Path(__file__).resolve().parents[1] / "app" / "core" / "deps" / "catalog.json"


def hf_repo(client: httpx.Client, repo: str) -> dict:
    r = client.get(f"https://huggingface.co/api/models/{repo}")
    r.raise_for_status()
    return r.json()


def hf_paths(client: httpx.Client, repo: str, rev: str, paths: list[str]) -> dict[str, dict]:
    r = client.post(f"https://huggingface.co/api/models/{repo}/paths-info/{rev}", data={"paths": paths})
    r.raise_for_status()
    return {item["path"]: item for item in r.json()}


def modelscope_has(client: httpx.Client, repo: str, path: str) -> bool | None:
    try:
        r = client.get(f"https://modelscope.cn/api/v1/models/{repo}/repo/files",
                       params={"Revision": "master", "Recursive": "true", "Root": str(Path(path).parent).replace(".", "")})
        files = (r.json().get("Data") or {}).get("Files") or []
        return any(f.get("Path") == path for f in files)
    except Exception:  # noqa: BLE001 - mirror is best effort
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline-ok", action="store_true", help="keep old values when a host is unreachable")
    args = ap.parse_args()
    cat = json.loads(CATALOG.read_text(encoding="utf-8"))
    today = dt.date.today().isoformat()
    problems: list[str] = []
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "DirectorStudio-catalog"}) as client:
        repos: dict[str, dict] = {}
        for model in cat["models"]:
            hf = next((s for s in model["sources"] if s["kind"] == "hf"), None)
            if not hf:
                continue
            try:
                info = repos.get(hf["repo"]) or repos.setdefault(hf["repo"], hf_repo(client, hf["repo"]))
                hf["revision"] = info["sha"]
                meta = hf_paths(client, hf["repo"], info["sha"], [hf["path"]]).get(hf["path"])
                if not meta:
                    problems.append(f"{model['file']}: not found in {hf['repo']}")
                    model["verified"] = None
                    continue
                model["size"] = int(meta["size"])
                model["sha256"] = (meta.get("lfs") or {}).get("oid")
                model["gated"] = bool(info.get("gated"))
                card = info.get("cardData") or {}
                licence = card.get("license") or model.get("license") or "unknown"
                if licence == "other" and card.get("license_name"):
                    licence = str(card["license_name"])
                model["license"] = licence
                link = str(card.get("license_link") or "")
                if link and not link.startswith("http"):
                    link = f"https://huggingface.co/{hf['repo']}/blob/{info['sha']}/{link.lstrip('/')}"
                model["license_url"] = link or f"https://huggingface.co/{hf['repo']}"
                model["verified"] = today
            except httpx.HTTPError as exc:
                problems.append(f"{model['file']}: {exc}")
                if not args.offline_ok:
                    raise
            for src in model["sources"]:
                if src["kind"] == "modelscope":
                    src["verified"] = modelscope_has(client, src["repo"], src["path"])
        for pack in cat["node_packs"]:
            if not pack.get("repo") or not pack.get("commit"):
                continue
            slug = pack["repo"].removeprefix("https://github.com/").removesuffix(".git")
            r = client.get(f"https://api.github.com/repos/{slug}/commits/{pack['commit']}")
            pack["verified"] = today if r.status_code == 200 else None
            if r.status_code != 200:
                problems.append(f"{pack['id']}: commit {pack['commit']} -> HTTP {r.status_code}")
    CATALOG.write_text(json.dumps(cat, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for p in problems:
        print("WARN", p, file=sys.stderr)
    print(f"{len(cat['models'])} models, {len(cat['node_packs'])} node packs; {len(problems)} problems")
    return 0


if __name__ == "__main__":
    sys.exit(main())
