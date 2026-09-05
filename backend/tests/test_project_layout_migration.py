from app.core.projects.layouts import LayoutProvider, LayoutReference


def test_legacy_layout_defaults_to_comfy_provider():
    layout = LayoutReference.model_validate(
        {
            "id": "legacy-layout",
            "source_refs": [],
        }
    )

    assert layout.provider == LayoutProvider.comfy
