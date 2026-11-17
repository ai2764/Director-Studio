"""Catalog size changes must not change dispatch validation or backend semantics."""
import asyncio
import copy
import json
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.delenv('DIRECTOR_MCP_CATALOG_MODE', raising=False)
    folder = Path(__file__).resolve().parent
    monkeypatch.syspath_prepend(str(folder))
    spec = importlib.util.spec_from_file_location('lazy_bridge_test', folder / 'server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NATIVE = {'name': 'patch_shots', 'description': 'Patch existing shots. Preserve user intent and existing references.',
    'parameters': {'type': 'object', 'properties': {'shots': {'type': 'array', 'items': {'$ref': '#/$defs/Shot'}}},
        'required': ['shots'], 'additionalProperties': False,
        '$defs': {'Shot': {'type': 'object', 'properties': {'id': {'type': 'string'}}, 'required': ['id']}}}}


def install_backend(bridge, monkeypatch):
    calls = []
    async def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == '/agent/tools':
            return {'tools': [copy.deepcopy(NATIVE)]}
        return {'status': 'queued', 'task_id': 'at_test'}
    monkeypatch.setattr(bridge, 'request', request)
    return calls


def test_default_catalog_defers_native_details_and_h3_setup(bridge, monkeypatch):
    install_backend(bridge, monkeypatch)
    tools = {t.name: t for t in asyncio.run(bridge.mcp.list_tools())}
    assert {'patch_shots', 'get_tool_contract', 'h3_workflow', 'get_project_context'} <= tools.keys()
    assert 'import_h3_workflow' not in tools
    assert 'get_h3_workflow_contract' not in tools
    schema = tools['patch_shots'].input_schema
    assert schema['properties']['arguments'] == {'type': 'object'}
    assert '$defs' not in schema
    assert set(schema['required']) == {'project_id', 'request_id', 'expected_revision', 'objective', 'arguments'}
    assert 'get_tool_contract' in tools['patch_shots'].description


def test_exact_native_contract_keeps_refs_and_legacy_full_mode(bridge, monkeypatch):
    install_backend(bridge, monkeypatch)
    compact = asyncio.run(bridge.get_tool_contract('patch_shots'))
    monkeypatch.setenv('DIRECTOR_MCP_CATALOG_MODE', 'full')
    full = {t.name: t for t in asyncio.run(bridge.mcp.list_tools())}
    assert compact['inputSchema'] == full['patch_shots'].input_schema
    assert compact['inputSchema']['$defs'] == NATIVE['parameters']['$defs']
    assert compact['inputSchema']['properties']['arguments']['properties']['shots']['items']['$ref'] == '#/$defs/Shot'
    assert 'import_h3_workflow' in full
    assert 'h3_workflow' not in full


def test_compact_native_call_validates_full_schema_before_dispatch(bridge, monkeypatch):
    calls = install_backend(bridge, monkeypatch)
    args = {'project_id': 'p', 'request_id': 'one', 'expected_revision': 'r', 'objective': 'User asks to patch', 'arguments': {}}
    with pytest.raises(bridge.ToolError, match='get_tool_contract'):
        asyncio.run(bridge.mcp.call_tool('patch_shots', args))
    assert all(method == 'GET' for method, _, _ in calls)
    args['arguments'] = {'shots': [{'id': 's'}]}
    result = asyncio.run(bridge.mcp.call_tool('patch_shots', args))
    assert result.structured_content['task_id'] == 'at_test'
    assert calls[-1][1] == '/agent/projects/p/tools/patch_shots'
    assert calls[-1][2]['body']['request_id'] == 'one'


def test_h3_contract_and_gateway_work_offline_but_validate_before_write(bridge, monkeypatch):
    calls = []
    async def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == '/agent/tools':
            raise bridge.httpx.ConnectError('offline')
        return {'status': 'succeeded', 'result': {'import_id': 'imp_test'}}
    monkeypatch.setattr(bridge, 'request', request)
    names = {t.name for t in asyncio.run(bridge.mcp.list_tools())}
    assert 'h3_workflow' in names
    contract = asyncio.run(bridge.get_tool_contract('configure_h3_workflow'))
    assert 'expected_mapping_sha256' in contract['inputSchema']['required']
    with pytest.raises(bridge.ToolError, match='get_tool_contract'):
        asyncio.run(bridge.h3_workflow('configure_h3_workflow', {'import_id': 'imp_test'}))
    assert not any(method == 'POST' for method, _, _ in calls)
    result = asyncio.run(bridge.h3_workflow('import_h3_workflow', {
        'request_id': 'one', 'workflow': {'1': {}}, 'mapping': {}, 'display_name': 'Fixture'}))
    assert json.loads(result.content[0].text)['status'] == 'succeeded'
    assert calls[-1][2]['body']['arguments']['workflow'] == {'1': {}}
    with pytest.raises(bridge.ToolError):
        asyncio.run(bridge.h3_workflow('send_message', {}))
    with pytest.raises(bridge.ToolError):
        asyncio.run(bridge.get_tool_contract('does_not_exist'))


def test_project_context_compacts_only_tool_definitions(bridge, monkeypatch):
    original = {'revision': 'r', 'state': {'shots': []}, 'instructions': 'Keep all safety instructions',
        'available_tools': [{'type': 'function', 'function': copy.deepcopy(NATIVE)}]}
    async def request(*args, **kwargs):
        return copy.deepcopy(original)
    monkeypatch.setattr(bridge, 'request', request)
    compact = asyncio.run(bridge.get_project_context('p'))
    assert compact['state'] == original['state']
    assert compact['instructions'] == original['instructions']
    assert compact['revision'] == 'r'
    assert 'parameters' not in str(compact['available_tools'])
    assert compact['available_tools'][0]['name'] == 'patch_shots'
    assert asyncio.run(bridge.get_project_context('p', include_tool_schemas=True)) == original
    monkeypatch.setenv('DIRECTOR_MCP_CATALOG_MODE', 'full')
    assert asyncio.run(bridge.get_project_context('p')) == original
