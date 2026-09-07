from pathlib import Path

from app.config import Settings


def test_data_dir_derives_all_persistent_subdirectories(tmp_path: Path) -> None:
    data_root = tmp_path / "shared-data"

    configured = Settings(_env_file=None, data_dir=data_root)

    assert configured.projects_dir == data_root / "projects"
    assert configured.library_root == data_root / "library"
    assert configured.library_dir == data_root / "library" / "actors"
    assert configured.jobs_dir == data_root / "jobs"
    assert configured.workflow_profiles_dir == data_root / "workflow_profiles"


def test_explicit_persistent_subdirectory_override_is_preserved(tmp_path: Path) -> None:
    data_root = tmp_path / "shared-data"
    custom_projects = tmp_path / "custom-projects"

    configured = Settings(
        _env_file=None,
        data_dir=data_root,
        projects_dir=custom_projects,
    )

    assert configured.projects_dir == custom_projects
    assert configured.library_root == data_root / "library"


def test_comfy_mcp_settings_can_be_configured_from_environment(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DS_H3_PROVIDER=local\n"
        "DS_COMFY_MCP_COMMAND=comfy-mcp-custom\n"
        "DS_COMFY_MCP_ARGS=--log-level WARNING\n"
        "DS_COMFY_MCP_COMFY_BIN=comfy-custom\n",
        encoding="utf-8",
    )

    configured = Settings(_env_file=env_file)

    assert configured.h3_provider == "local"
    assert configured.comfy_mcp_command == "comfy-mcp-custom"
    assert configured.comfy_mcp_args == "--log-level WARNING"
    assert configured.comfy_mcp_comfy_bin == "comfy-custom"
