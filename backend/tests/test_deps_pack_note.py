from app.core.deps.manager import _pack_note

DS = {"id": "ds_nodes", "bundled": True, "note_zh": "随程序安装", "note_en": "installed with the app"}
CORE = {"id": "comfy-core", "note_zh": "升级", "note_en": "update ComfyUI"}


def test_managed_keeps_catalog_note():
    assert _pack_note(DS, "missing", "managed")["en"] == "installed with the app"


def test_remote_missing_ds_nodes_says_copy():
    note = _pack_note(DS, "missing", "remote")
    assert "comfy_nodes/ds_nodes" in note["en"] and "custom_nodes" in note["zh"]


def test_remote_installed_keeps_note():
    assert _pack_note(DS, "installed", "remote")["en"] == "installed with the app"


def test_core_note_hidden_when_installed():
    assert _pack_note(CORE, "installed", "remote") == {"zh": None, "en": None}
    assert _pack_note(CORE, "missing", "remote")["en"] == "update ComfyUI"
