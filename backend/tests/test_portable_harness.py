from __future__ import annotations

from pathlib import Path

import pytest

from app.portable_harness import (
    HarnessStartupError,
    PortableHarnessSettings,
    bundled_harness_paths,
    read_portable_harness_settings,
    start_managed_harness,
)


EXPECTED_HEALTH = {
    "ok": True,
    "service": "director-studio-harness",
    "protocol": 1,
    "capabilities": ["native-sessions-v1", "context-envelope-v2"],
}


class FakeProcess:
    def __init__(self, returncode: int | None = None) -> None:
        self.returncode = returncode
        self.terminated = False
        self.killed = False
        self.waited = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        assert timeout is not None
        return int(self.returncode or 0)


def _runtime_tree(root: Path) -> None:
    node = root / "runtime" / "node" / "node.exe"
    entry = root / "harness" / "dist" / "server.js"
    node.parent.mkdir(parents=True)
    entry.parent.mkdir(parents=True)
    node.write_bytes(b"node")
    entry.write_text("// server", encoding="utf-8")


def test_environment_overrides_dotenv(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DS_DIRECTOR_AGENT_RUNTIME=harness\nDS_HARNESS_MANAGED=true\n",
        encoding="utf-8",
    )

    result = read_portable_harness_settings(
        env_file,
        {"DS_DIRECTOR_AGENT_RUNTIME": "legacy"},
    )

    assert result == PortableHarnessSettings(runtime="legacy", managed=True)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("YES", True), ("1", True), ("false", False), ("No", False), ("0", False)],
)
def test_managed_setting_accepts_explicit_boolean_values(
    tmp_path: Path,
    raw: str,
    expected: bool,
) -> None:
    result = read_portable_harness_settings(
        tmp_path / "missing.env",
        {"DS_HARNESS_MANAGED": raw},
    )

    assert result == PortableHarnessSettings(runtime="harness", managed=expected)


def test_invalid_managed_setting_is_a_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(HarnessStartupError, match="configuration"):
        read_portable_harness_settings(
            tmp_path / ".env",
            {"DS_HARNESS_MANAGED": "sometimes"},
        )


def test_bundled_paths_follow_install_and_data_roots(tmp_path: Path) -> None:
    result = bundled_harness_paths(tmp_path, tmp_path / "data")

    assert result.node == tmp_path / "runtime" / "node" / "node.exe"
    assert result.entry == tmp_path / "harness" / "dist" / "server.js"
    assert result.root == tmp_path / "harness"
    assert result.session_root == tmp_path / "data" / "harness-sessions"
    assert result.log_path == tmp_path / "data" / "logs" / "harness-sidecar.log"


def test_start_uses_private_node_and_allowlisted_environment(tmp_path: Path) -> None:
    _runtime_tree(tmp_path)
    process = FakeProcess()
    captured: dict = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return process

    handle = start_managed_harness(
        tmp_path,
        tmp_path / "data",
        popen=fake_popen,
        health_probe=lambda _url, _token: EXPECTED_HEALTH,
        environ={
            "SystemRoot": r"C:\Windows",
            "PATH": r"C:\system-node",
            "NODE_OPTIONS": "--require bad.js",
            "DS_LLM_API_KEY": "secret",
        },
        port_factory=lambda: 19001,
        token_factory=lambda: "a" * 64,
    )
    try:
        paths = bundled_harness_paths(tmp_path, tmp_path / "data")
        assert captured["command"] == [str(paths.node), str(paths.entry)]
        assert captured["cwd"] == paths.root
        assert handle.base_url == "http://127.0.0.1:19001"
        assert handle.token == "a" * 64
        assert captured["env"]["SystemRoot"] == r"C:\Windows"
        assert captured["env"]["DS_HARNESS_PORT"] == "19001"
        assert captured["env"]["DS_HARNESS_PARENT_PID"] == str(__import__("os").getpid())
        assert captured["env"]["DS_HARNESS_SESSION_ROOT"] == str(paths.session_root)
        assert "PATH" not in captured["env"]
        assert "NODE_OPTIONS" not in captured["env"]
        assert "DS_LLM_API_KEY" not in captured["env"]
    finally:
        handle.stop()

    assert process.terminated is True
    assert process.waited is True


@pytest.mark.parametrize(
    ("missing", "stage"),
    [("node", "runtime"), ("entry", "runtime")],
)
def test_start_rejects_missing_runtime_files(
    tmp_path: Path,
    missing: str,
    stage: str,
) -> None:
    _runtime_tree(tmp_path)
    paths = bundled_harness_paths(tmp_path, tmp_path / "data")
    (paths.node if missing == "node" else paths.entry).unlink()

    with pytest.raises(HarnessStartupError, match=stage):
        start_managed_harness(tmp_path, tmp_path / "data")


def test_wrong_health_identity_stops_the_owned_child(tmp_path: Path) -> None:
    _runtime_tree(tmp_path)
    process = FakeProcess()
    wrong = {**EXPECTED_HEALTH, "capabilities": ["native-sessions-v1"]}

    with pytest.raises(HarnessStartupError, match="capabilities") as raised:
        start_managed_harness(
            tmp_path,
            tmp_path / "data",
            popen=lambda *_args, **_kwargs: process,
            health_probe=lambda _url, _token: wrong,
        )

    assert process.terminated is True
    assert "harness-sidecar.log" in str(raised.value)


def test_early_child_exit_is_reported_and_log_is_closed(tmp_path: Path) -> None:
    _runtime_tree(tmp_path)
    process = FakeProcess(returncode=7)

    with pytest.raises(HarnessStartupError, match="exit code 7"):
        start_managed_harness(
            tmp_path,
            tmp_path / "data",
            popen=lambda *_args, **_kwargs: process,
            health_probe=lambda _url, _token: None,
        )


def test_readiness_timeout_stops_the_owned_child(tmp_path: Path) -> None:
    _runtime_tree(tmp_path)
    process = FakeProcess()

    with pytest.raises(HarnessStartupError, match="readiness"):
        start_managed_harness(
            tmp_path,
            tmp_path / "data",
            startup_timeout=0,
            popen=lambda *_args, **_kwargs: process,
            health_probe=lambda _url, _token: None,
        )

    assert process.terminated is True
