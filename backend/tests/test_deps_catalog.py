"""The dependency catalog is the single source of truth: workflows, docs, doctor, installer agree."""

import json
from pathlib import Path
import re

from app.core.deps import catalog, docs
from app.core.doctor.requirements import LOADER_FOLDERS, NODE_PACK_HINTS, load_builtin

ROOT = Path(__file__).resolve().parents[2]


def test_docs_models_md_is_generated_from_the_catalog():
    assert (ROOT / "docs" / "models.md").read_text(encoding="utf-8") == docs.render(), \
        "run: cd backend && python -m app.core.deps.docs"


def test_every_builtin_workflow_matches_its_catalog_entry():
    builtin = load_builtin(ROOT / "backend" / "workflows")
    assert {w.name for w in builtin} == {w["file"] for w in catalog.workflows()}
    for wf in builtin:
        entry = catalog.workflow(wf.name)
        files = list(dict.fromkeys(f for *_, f in wf.models))
        assert entry["models"] == files, wf.name
        for _cls, _inp, folder, file in wf.models:
            assert catalog.model(file)["folder"] == folder, file
        packs = {catalog.pack_for_class(c)["id"] for c in wf.nodes if catalog.pack_for_class(c)}
        assert set(entry["node_packs"]) == packs, wf.name


def test_models_are_pinned_and_hashed():
    for m in catalog.models():
        hf = catalog.hf_source(m)
        assert hf and re.fullmatch(r"[0-9a-f]{40}", hf["revision"]), m["file"]
        assert re.fullmatch(r"[0-9a-f]{64}", m["sha256"]) and m["size"] > 0, m["file"]
        assert m["folder"] in {f for _, f in LOADER_FOLDERS.values()}
        assert m["license"] and m["license_url"].startswith("https://")
        urls = catalog.model_urls(m)
        assert urls[0] == f"https://huggingface.co/{hf['repo']}/resolve/{hf['revision']}/{hf['path']}"
        assert catalog.model_urls(m, hf_endpoint="https://hf-mirror.com/")[0].startswith("https://hf-mirror.com/")


def test_node_packs_are_pinned_and_consistent_with_installer_and_doctor():
    manifest = json.loads((ROOT / "installer" / "manifest.json").read_text(encoding="utf-8"))
    for p in catalog.node_packs():
        for cls in p["provides"]:
            assert NODE_PACK_HINTS[cls][0] == p["id"]
        if p["installable"]:
            assert p["repo"].startswith("https://github.com/") and re.fullmatch(r"[0-9a-f]{40}", p["commit"])
            assert p["unlicensed"] == (p["license"] is None)
    for entry in manifest["custom_nodes"]:
        assert catalog.pack(entry["name"])["commit"] == entry["commit"] and catalog.pack(entry["name"])["bundled"]
    for entry in manifest["unbundled_custom_nodes"]:
        p = catalog.pack(entry["name"])
        assert p["unlicensed"] and not p["ships_with"] and not p["bundled"] and p["commit"] == entry["commit_checked"]
