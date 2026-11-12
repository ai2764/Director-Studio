"""Read-only bridge configuration checks; no running backend is required."""
import importlib.util
from pathlib import Path


def load_bridge(monkeypatch):
    folder = Path(__file__).resolve().parent
    monkeypatch.syspath_prepend(str(folder))
    spec = importlib.util.spec_from_file_location('director_mcp_bridge_test', folder / 'server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_host_allowlist_contains_only_local_addresses(monkeypatch):
    monkeypatch.delenv('DIRECTOR_MCP_ALLOWED_HOSTS', raising=False)
    bridge = load_bridge(monkeypatch)
    security = bridge.transport_security_settings()
    assert security.allowed_hosts == ['127.0.0.1:*', 'localhost:*']
    assert security.allowed_origins == []


def test_public_hosts_are_explicit_environment_configuration(monkeypatch):
    monkeypatch.setenv('DIRECTOR_MCP_ALLOWED_HOSTS', ' studio.example.test:8798, ,studio.example.test:8798 ')
    bridge = load_bridge(monkeypatch)
    assert bridge.transport_security_settings().allowed_hosts == [
        '127.0.0.1:*', 'localhost:*', 'studio.example.test:8798',
    ]
