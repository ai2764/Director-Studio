from app.agents.director.tool_handlers.layout import _revision_source_refs, _resolve_layout_shot
from app.core.projects.layouts import LayoutReference, LayoutSourceRef
from app.core.projects.models import Shot


def test_revision_keeps_original_actor_and_scene_sources_across_redraws():
    original = LayoutReference(
        id="lref_original",
        source_refs=[
            LayoutSourceRef(role="actor", asset_id="act_mia"),
            LayoutSourceRef(role="scene", asset_id="scn_hotel"),
        ],
    )
    first_redraw = LayoutReference(
        id="lref_redraw", revision_of=original.id,
    )
    assert _revision_source_refs([original, first_redraw], first_redraw) == original.source_refs


def test_layout_target_resolution_keeps_explicit_shot_choice_and_rejects_ambiguity():
    target = Shot(id="sht_target", project_id="prj_one", scene_id="one", title="One",
                  script_beat="One", duration_s=3,
                  layout_refs=[LayoutReference(id="lref_target", asset_id="lay_one")])
    neighbor = target.model_copy(update={"id":"sht_neighbor", "layout_refs":[]})
    assert _resolve_layout_shot([target, neighbor], {"layout_ref_id":"lref_target"}) == target
    assert _resolve_layout_shot([target, neighbor], {"layout_ref_id":"lref_unknown"}) is None
    assert _resolve_layout_shot([target, neighbor], {
        "layout_ref_id":"lref_target", "shot_id":neighbor.id,
    }) == neighbor
    duplicate = neighbor.model_copy(update={"layout_refs":target.layout_refs})
    assert _resolve_layout_shot([target, duplicate], {"layout_ref_id":"lref_target"}) is None
