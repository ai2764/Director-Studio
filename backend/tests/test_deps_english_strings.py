"""Every bilingual dependency/health-check string must have an English side
(the frontend shows English by default and never falls back to Chinese)."""
import re

from app.core.deps import catalog
from app.core.deps.manager import _pack_note
from app.core.doctor.requirements import FEATURES, NODE_PACK_HINTS

CJK = re.compile(r"[\u4e00-\u9fff]")


def test_catalog_has_english_for_every_chinese_field():
    for group in (catalog.node_packs(), catalog.workflows()):
        for entry in group:
            for key, value in entry.items():
                if key == "zh" or key.endswith("_zh"):
                    en = entry.get("en" if key == "zh" else key[:-3] + "_en")
                    assert not value or (en and not CJK.search(en)), (entry, key)


def test_missing_node_hints_and_workflow_titles_are_english():
    for _pack, _zh, en in NODE_PACK_HINTS.values():
        assert en and not CJK.search(en)
    for _zh, en in FEATURES.values():
        assert en and not CJK.search(en)


def test_pack_notes_for_external_comfy_are_english():
    for entry in catalog.node_packs():
        for mode in ("managed", "external-local", "remote"):
            note = _pack_note(entry, "missing", mode)
            if note.get("zh"):
                assert note.get("en") and not CJK.search(note["en"])
