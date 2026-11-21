from __future__ import annotations

import json
from typing import Any

from jsonschema import Draft202012Validator

from app.agents.director.tool_schema import DIRECTOR_TOOL_SCHEMAS, director_tool_schemas
from app.core.projects.models import Project, ProjectMode


def _find_schema_title_keys(node: Any, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(node, list):
        for i, item in enumerate(node):
            hits.extend(_find_schema_title_keys(item, f"{path}[{i}]"))
    elif isinstance(node, dict):
        for key, value in node.items():
            if key == "title" and isinstance(value, str):
                hits.append(f"{path}.title")
            elif key in {"properties", "$defs"} and isinstance(value, dict):
                for prop_name, prop_schema in value.items():
                    hits.extend(_find_schema_title_keys(prop_schema, f"{path}.{key}.{prop_name}"))
            else:
                hits.extend(_find_schema_title_keys(value, f"{path}.{key}"))
    return hits


def _project(mode: ProjectMode) -> Project:
    return Project(
        id=f"prj_{mode.value}",
        name=f"Schema test ({mode.value})",
        script_text="INT. ROOM - NIGHT",
        mode=mode,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def test_director_tool_schemas_strip_pydantic_title_metadata_but_keep_shot_title_fields():
    for tool in DIRECTOR_TOOL_SCHEMAS:
        name = tool["function"]["name"]
        params = tool["function"]["parameters"]
        Draft202012Validator.check_schema(params)
        assert _find_schema_title_keys(params, name) == []

    # Verify real object properties named "title" are preserved intact.
    by_name = {t["function"]["name"]: t["function"]["parameters"] for t in DIRECTOR_TOOL_SCHEMAS}
    assert "title" in by_name["revise_shot"]["properties"]
    assert "title" in by_name["save_storyboard"]["$defs"]["ShotDraft"]["properties"]
    assert "title" in by_name["append_shot"]["$defs"]["NewShotDraft"]["properties"]
    assert "title" in by_name["append_shots"]["$defs"]["BatchShotDraft"]["properties"]
    assert "title" in by_name["write_prompt"]["properties"]


def test_non_mv_projects_omit_music_segment_while_mv_projects_retain_it():
    film_tools = {
        t["function"]["name"]: t["function"]["parameters"]
        for t in director_tool_schemas(_project(ProjectMode.director), shots=[])
    }
    mv_tools = {
        t["function"]["name"]: t["function"]["parameters"]
        for t in director_tool_schemas(_project(ProjectMode.mv), shots=[])
    }

    for name in ("save_storyboard", "append_shot", "append_shots", "revise_shot"):
        Draft202012Validator.check_schema(film_tools[name])
        Draft202012Validator.check_schema(mv_tools[name])
        film_blob = json.dumps(film_tools[name], ensure_ascii=False)
        mv_blob = json.dumps(mv_tools[name], ensure_ascii=False)
        assert "music_segment" not in film_blob
        assert "ShotMusicSegment" not in film_blob
        assert "music_segment" in mv_blob
        assert "ShotMusicSegment" in mv_blob
