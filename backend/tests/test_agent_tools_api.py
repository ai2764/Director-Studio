import asyncio
import httpx
import pytest
from app.main import create_app
from app.agents.director.service import DirectorService, _script_hash
from app.core.projects.store import create_project, load_project, list_shots
from app.api import agent_tools

@pytest.mark.asyncio
async def test_native_append_modes_dedupe_and_stale_state():
    app = create_app()
    app.state.director_service = DirectorService(plan_provider=object())
    project = create_project('Native', 'An empty moonlit courtyard.')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        ctx = (await client.get(f'/api/agent/projects/{project.id}/context')).json()
        draft = {'scene_id': 'Scene 1', 'title': 'Courtyard', 'script_beat': 'Moonlight falls on an empty courtyard.',
                 'shot_type': 'wide', 'camera_angle': 'eye level', 'camera_motion': 'locked off',
                 'composition': 'empty courtyard centered', 'duration_s': 5, 'actor_presence': 'none'}
        body = {'request_id': 'one', 'expected_revision': ctx['revision'], 'objective': 'Append an empty courtyard shot',
                'arguments': {'expected_script_hash': _script_hash(project.script_text), 'expected_last_shot_id': None, 'shots': [draft]}}
        url = f'/api/agent/projects/{project.id}/tools/append_shots'
        r = await client.post(url, json=body)
        assert r.status_code == 202, r.text
        receipt = r.json()
        if agent_tools.running: await asyncio.gather(*list(agent_tools.running))
        result = (await client.get('/api/agent/tasks/' + receipt['task_id'])).json()
        assert result['status'] == 'succeeded', result
        retry = await client.post(url, json=body)
        assert retry.json()['task_id'] == receipt['task_id'] and retry.json()['deduplicated']
        assert len(list_shots(project.id)) == 1
        assert load_project(project.id).authoring_mode == 'director_agent'
        assert load_project(project.id).script_text == project.script_text
        body['request_id'] = 'two'
        assert (await client.post(url, json=body)).status_code == 409
        body['request_id'] = 'one'
        body['objective'] = 'different input'
        assert (await client.post(url, json=body)).status_code == 409
        assert agent_tools.task_path(receipt['task_id']).exists()
        assert 'before' not in result

@pytest.mark.asyncio
async def test_native_schema_and_unavailable_tool_are_checked_before_execution():
    app = create_app()
    app.state.director_service = DirectorService(plan_provider=object())
    p = create_project('Native', 'Original script')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
        ctx = (await c.get(f'/api/agent/projects/{p.id}/context')).json()
        body = {'request_id': 'one', 'expected_revision': ctx['revision'], 'objective': 'Read status', 'arguments': {}}
        assert (await c.post(f'/api/agent/projects/{p.id}/tools/not_a_tool', json=body)).status_code == 409
        assert (await c.post(f'/api/agent/projects/{p.id}/tools/set_script', json=body)).status_code == 422
        assert load_project(p.id).script_text == 'Original script'

@pytest.mark.asyncio
async def test_create_project_request_is_durable_and_deduplicated():
    from app.core.projects.store import list_projects
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
        body = {'request_id': 'create-one', 'name': 'New project', 'script_text': 'Exact original'}
        first = (await c.post('/api/agent/projects', json=body)).json()
        second = (await c.post('/api/agent/projects', json=body)).json()
        assert first['status'] == 'succeeded'
        assert first['result']['project']['id'] == second['result']['project']['id']
        assert len(list_projects()) == 1
        body['name'] = 'Different'
        assert (await c.post('/api/agent/projects', json=body)).status_code == 409

@pytest.mark.asyncio
async def test_import_asset_bytes_scope_dedupe_and_validation():
    import io
    from PIL import Image
    from app.core.library.store import list_assets, asset_dir
    p = create_project('Image import', 'Script stays unchanged')
    other = create_project('Other', '')
    buffer = io.BytesIO()
    Image.new('RGB', (12, 8), 'blue').save(buffer, format='PNG')
    image = buffer.getvalue()
    data = {'project_id': p.id, 'request_id': 'image-one', 'kind': 'actors',
            'name': 'Identity sheet', 'notes': 'User-supplied identity, retain face.',
            'file_key': 'fullbody_threeview'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url='http://test') as c:
        async def upload(fields, content=image):
            return await c.post('/api/agent/assets/import', data=fields, files={'file': ('identity.png', content, 'image/png')})
        first = await upload(data)
        assert first.status_code == 200, first.text
        saved = first.json()['result']['asset']
        assert saved['project_id'] == p.id and saved['files'] == {'fullbody_threeview': 'fullbody_threeview.png'}
        assert (asset_dir('actors', saved['id']) / 'fullbody_threeview.png').read_bytes() == image
        again = await upload(data)
        assert again.json()['result']['asset']['id'] == saved['id']
        assert len(list_assets('actors', project_id=p.id)) == 1
        assert list_assets('actors', project_id=other.id) == []
        assert (await upload({**data, 'name': 'Changed'})).status_code == 409
        assert (await upload({**data, 'request_id': 'invalid'}, b'not an image')).status_code == 422
        assert (await upload({**data, 'file_key': '../escape'})).status_code == 422
        assert (await upload({**data, 'project_id': 'prj_missing'})).status_code == 404
        assert load_project(p.id).script_text == 'Script stays unchanged'
