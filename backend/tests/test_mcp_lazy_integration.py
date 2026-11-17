"""Exercise the MCP entry points against real isolated backend routes/stores."""
import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from app.main import create_app
from app.api import agent_tools
from app.agents.director.service import DirectorService, _script_hash
from app.core.projects.store import create_project, list_shots
from test_h3_direct_mapping import direct_fixture


@pytest.fixture
def connected_bridge(monkeypatch):
    folder = Path(__file__).resolve().parents[2] / 'mcp'
    monkeypatch.syspath_prepend(str(folder))
    monkeypatch.delenv('DIRECTOR_MCP_CATALOG_MODE', raising=False)
    spec = importlib.util.spec_from_file_location('integration_bridge', folder / 'server.py')
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    app = create_app()
    app.state.director_service = DirectorService(plan_provider=object())

    async def request(method, path, **kwargs):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.request(method, '/api' + path, params=kwargs.get('params'), json=kwargs.get('body'))
        if response.is_error:
            raise bridge.ToolError(f'HTTP {response.status_code}: {response.text}')
        return response.json()
    monkeypatch.setattr(bridge, 'request', request)
    return bridge


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['compact', 'full'])
async def test_discover_read_contract_append_dedupe_and_stale_revision(connected_bridge, monkeypatch, mode):
    bridge = connected_bridge
    monkeypatch.setenv('DIRECTOR_MCP_CATALOG_MODE', mode)
    project = create_project('MCP contract test', 'Moonlit courtyard.')
    context = await bridge.get_project_context(project.id, include_tool_schemas=False)
    assert 'append_shots' in {tool['name'] for tool in context['available_tools']}
    assert all('parameters' not in tool for tool in context['available_tools'])
    contract = await bridge.get_tool_contract('append_shots')
    assert 'shots' in contract['inputSchema']['properties']['arguments']['properties']
    body = {'project_id': project.id, 'request_id': 'append-one', 'expected_revision': context['revision'],
        'objective': 'Append an empty courtyard shot', 'arguments': {
            'expected_script_hash': _script_hash(project.script_text), 'expected_last_shot_id': None,
            'shots': [{'scene_id': 'Scene 1', 'title': 'Courtyard', 'script_beat': 'Moonlight fills the courtyard.',
                'shot_type': 'wide', 'camera_angle': 'eye level', 'camera_motion': 'locked off',
                'composition': 'Courtyard centered', 'duration_s': 5, 'actor_presence': 'none'}]}}
    receipt = (await bridge.mcp.call_tool('append_shots', body)).structured_content
    if agent_tools.running:
        await asyncio.gather(*list(agent_tools.running))
    assert (await bridge.get_agent_task(receipt['task_id']))['status'] == 'succeeded'
    replay = (await bridge.mcp.call_tool('append_shots', body)).structured_content
    assert replay['deduplicated'] is True
    assert len(list_shots(project.id)) == 1
    with pytest.raises(bridge.ToolError, match='409'):
        await bridge.mcp.call_tool('append_shots', {**body, 'request_id': 'append-two'})
    assert len(list_shots(project.id)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['compact', 'full'])
async def test_h3_gateway_import_inspect_replay_and_activation_gate(connected_bridge, monkeypatch, mode):
    bridge = connected_bridge
    monkeypatch.setenv('DIRECTOR_MCP_CATALOG_MODE', mode)
    schema = await bridge.get_tool_contract('import_h3_workflow')
    assert {'workflow', 'mapping', 'request_id'} <= set(schema['inputSchema']['required'])
    graph, mapping = direct_fixture()
    args = {'request_id': 'h3-one', 'workflow': graph, 'mapping': mapping}
    async def operation(name, args):
        result = await bridge.mcp.call_tool('h3_workflow', {'tool_name': name, 'arguments': args})
        return json.loads(result.content[0].text)
    receipt = await operation('import_h3_workflow', args)
    assert receipt['status'] == 'succeeded'
    import_id = receipt['result']['import_id']
    replay = await operation('import_h3_workflow', args)
    assert replay['result']['import_id'] == import_id
    inspected = await operation('inspect_h3_workflow', {'import_id': import_id})
    assert inspected['workflow'] == graph
    active = (await operation('list_h3_workflows', {}))['active']
    blocked = await operation('activate_h3_workflow', {'request_id': 'activate-one', 'import_id': import_id,
        'expected_active_profile_id': active['profile_id'],
        'expected_active_workflow_sha256': active['workflow_sha256']})
    assert blocked['status'] == 'failed'
    assert (await operation('list_h3_workflows', {}))['active'] == active
