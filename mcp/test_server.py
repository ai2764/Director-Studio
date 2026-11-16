"""Read-only bridge configuration checks; no running backend is required."""
import importlib.util
import asyncio
import pytest
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


def test_h3_tools_are_discoverable_without_a_live_backend(monkeypatch):
    bridge = load_bridge(monkeypatch)
    async def request(*args, **kwargs):
        raise bridge.httpx.ConnectError('offline')
    monkeypatch.setattr(bridge, 'request', request)
    names = {t.name for t in asyncio.run(bridge.mcp.list_tools())}
    assert {'get_h3_workflow_contract', 'list_h3_workflows', 'list_h3_native_sources', 'import_h3_workflow', 'configure_h3_workflow',
        'inspect_h3_workflow', 'validate_h3_workflow', 'test_h3_workflow',
        'select_h3_test_output', 'activate_h3_workflow', 'select_h3_workflow'} <= names


def test_h3_mcp_import_transmits_json_and_mapping_not_a_local_path(monkeypatch):
    bridge = load_bridge(monkeypatch)
    calls = []
    async def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {'status': 'succeeded', 'result': {'import_id': 'imp_test'}}
    monkeypatch.setattr(bridge, 'request', request)
    result = asyncio.run(bridge.import_h3_workflow('id-one', {'1': {'class_type': 'Example'}},
        {'inputs': {'kind': 'direct'}}, 'H3N'))
    assert result['result']['import_id'] == 'imp_test'
    assert calls[0][1] == '/agent/h3-workflows/operations'
    assert calls[0][2]['body']['arguments']['mapping']['inputs']['kind'] == 'direct'
    assert calls[0][2]['body']['request_id'] == 'id-one'


def test_h3_mcp_failed_receipt_remains_structured(monkeypatch):
    bridge = load_bridge(monkeypatch)
    async def request(*args, **kwargs):
        return {'status': 'failed', 'error': {'code': 'profile_changed', 'details': {'reason': 'stale'}}}
    monkeypatch.setattr(bridge, 'request', request)
    result = asyncio.run(bridge.activate_h3_workflow('id', 'imp_test', 'old', '0'*64))
    assert result['status'] == 'failed'
    assert result['error']['code'] == 'profile_changed'


def test_h3_mcp_configuration_updates_same_import_with_expected_hashes(monkeypatch):
    bridge = load_bridge(monkeypatch)
    calls = []
    async def request(method, path, **kwargs):
        calls.append(kwargs['body'])
        return {'status': 'succeeded', 'result': {'import_id': 'imp_existing'}}
    monkeypatch.setattr(bridge, 'request', request)
    mapping = {'inputs': {'kind': 'direct'}}
    result = asyncio.run(bridge.configure_h3_workflow('config-1', 'imp_existing', mapping, 'a'*64, 'b'*64))
    assert result['result']['import_id'] == 'imp_existing'
    assert calls[0] == {'action': 'configure', 'request_id': 'config-1', 'arguments': {
        'import_id': 'imp_existing', 'mapping': mapping,
        'expected_workflow_sha256': 'a'*64, 'expected_mapping_sha256': 'b'*64}}


def test_h3_native_source_discovery_is_read_only_and_global_by_default(monkeypatch):
    bridge = load_bridge(monkeypatch)
    calls = []
    async def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {'sources': [{'job_id': 'job_source'}]}
    monkeypatch.setattr(bridge, 'request', request)
    result = asyncio.run(bridge.list_h3_native_sources())
    assert result['sources'][0]['job_id'] == 'job_source'
    assert calls == [('GET', '/agent/h3-workflows/native-sources', {'params': {'limit': 20}, 'timeout': 180})]
