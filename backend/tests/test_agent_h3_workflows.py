"""Agent setup skips discovery and retains durable, retry-safe receipts."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.api import h3_workflow_profiles as existing
from app.workflow_profiles.h3.store import H3ProfileStore
from test_h3_direct_mapping import direct_fixture


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def call(client, action, request_id, **arguments):
    response = client.post('/api/agent/h3-workflows/operations', json={
        'action': action, 'request_id': request_id, 'arguments': arguments})
    assert response.status_code == 200, response.text
    return response.json()


def test_native_import_validation_and_source_test_preserve_cache_lineage(client, monkeypatch):
    from test_h3_direct_mapping import native_fixture
    from app.config import settings
    from app.core.jobs.store import create_job, save_job, load_job
    from app.core.schemas import JobStatus
    from app.pipelines.h3_ref2va.pipeline import H3Ref2VaPipeline
    graph, mapping = native_fixture()
    seen = []
    class Validator:
        async def validate_workflow(self, filled):
            seen.append(filled)
            return {'valid': True, 'error_count': 0}
        async def aclose(self): pass
    monkeypatch.setattr(existing, 'ComfyMcpClient', Validator)
    monkeypatch.setattr(existing, '_resolve_picture_asset', lambda _id: ('ref.png', b'fixture'))
    async def prepare_only(job, images):
        assert 'context_video' not in images
        H3Ref2VaPipeline().prepare_job_submission(job)
        save_job(job)
    monkeypatch.setattr(existing, 'start_pipeline_job', prepare_only)
    imported = call(client, 'import', 'native-import', workflow=graph, mapping=mapping)
    assert imported['status'] == 'succeeded', imported
    import_id = imported['result']['import_id']
    validation = call(client, 'validate', 'native-validate', import_id=import_id)
    assert validation['status'] == 'succeeded', validation
    assert seen[0]['cache']['inputs']['filename_prefix'] == 'contract/validation/native/clip'
    assert seen[0]['load']['inputs']['latent_path'] == 'contract/source/native'
    rejected = call(client, 'test', 'native-missing-source', import_id=import_id, picture_asset_ids=['ref'])
    assert rejected['status'] == 'failed', rejected
    source = create_job(pipeline_id='h3_ref2va', asset_kind='productions', name='source', params={})
    source.status = JobStatus.succeeded
    source.params = {'h3_provider': 'local', 'native_latent_context': {
        'save_prefix': f'director-studio/{source.id}/native/clip', 'save_index': 1,
        'verified': True, 'width': 1344, 'height': 768, 'comfy_base_url': settings.comfy_base_url}}
    save_job(source)
    receipt = call(client, 'test', 'native-test', import_id=import_id,
        picture_asset_ids=['ref'], native_source_job_id=source.id)
    assert receipt['status'] == 'succeeded', receipt
    job = load_job(receipt['result']['job_id'])
    assert (job.params['width'], job.params['height']) == (1344, 768)
    assert job.params['native_latent_context']['source_job_id'] == source.id
    assert job.params['native_latent_context']['save_prefix'] == f'director-studio/{job.id}/native/clip'


def test_native_bootstrap_test_does_not_require_source(client, monkeypatch):
    from test_h3_direct_mapping import native_fixture
    from app.core.jobs.store import load_job, save_job
    from app.pipelines.h3_ref2va.pipeline import H3Ref2VaPipeline
    graph, mapping = native_fixture(False)
    class Validator:
        async def validate_workflow(self, graph): return {'valid': True}
        async def aclose(self): pass
    monkeypatch.setattr(existing, 'ComfyMcpClient', Validator)
    monkeypatch.setattr(existing, '_resolve_picture_asset', lambda _id: ('ref.png', b'fixture'))
    async def prepare_only(job, images):
        H3Ref2VaPipeline().prepare_job_submission(job)
        save_job(job)
    monkeypatch.setattr(existing, 'start_pipeline_job', prepare_only)
    imported = call(client, 'import', 'bootstrap', workflow=graph, mapping=mapping)
    import_id = imported['result']['import_id']
    assert call(client, 'validate', 'bootstrap-v', import_id=import_id)['status'] == 'succeeded'
    receipt = call(client, 'test', 'bootstrap-t', import_id=import_id, picture_asset_ids=['ref'])
    assert receipt['status'] == 'succeeded', receipt
    job = load_job(receipt['result']['job_id'])
    assert job.params['native_latent_context']['save_index'] == 1
    assert job.params['native_latent_context']['verified'] is False


def test_direct_import_skips_discovery_and_replays_receipt(client, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Agent imports must not run official-node discovery')
    monkeypatch.setattr(existing, 'inspect_h3_workflow', forbidden)
    graph, mapping = direct_fixture()
    first = call(client, 'import', 'one', workflow=graph, mapping=mapping, display_name='H3N')
    assert first['status'] == 'succeeded', first
    second = call(client, 'import', 'one', workflow=graph, mapping=mapping, display_name='H3N')
    assert first['result'] == second['result']
    import_id = first['result']['import_id']
    read = client.get(f'/api/agent/h3-workflows/imports/{import_id}')
    assert read.status_code == 200
    assert read.json()['workflow'] == graph
    assert read.json()['contract_version'] == 4
    assert len(list(H3ProfileStore().imports_dir.iterdir())) == 1


def test_request_id_cannot_be_reused_for_different_mapping(client):
    graph, mapping = direct_fixture()
    assert call(client, 'import', 'same', workflow=graph, mapping=mapping)['status'] == 'succeeded'
    mapping['inputs']['frames'][0]['offset'] = 0
    response = client.post('/api/agent/h3-workflows/operations', json={
        'action': 'import', 'request_id': 'same', 'arguments': {'workflow': graph, 'mapping': mapping}})
    assert response.status_code == 409


def test_invalid_mapping_exposes_actionable_issues_and_no_import(client):
    graph, mapping = direct_fixture()
    mapping['inputs']['prompt'][0]['node_id'] = 'missing'
    receipt = call(client, 'import', 'invalid', workflow=graph, mapping=mapping)
    assert receipt['status'] == 'failed'
    assert receipt['error']['details']['issues']
    assert not H3ProfileStore().imports_dir.exists()


def test_contract_is_discoverable_and_list_has_active_identity(client):
    schema = client.get('/api/agent/h3-workflows/contract')
    assert schema.status_code == 200
    assert 'H3DirectInputMapping' in schema.json()['mapping_schema']['$defs']
    active = client.get('/api/agent/h3-workflows').json()['active']
    assert active['profile_id']
    assert len(active['workflow_sha256']) == 64


def test_activate_rejects_stale_active_identity_before_switch(client):
    graph, mapping = direct_fixture()
    imported = call(client, 'import', 'draft', workflow=graph, mapping=mapping)['result']
    before = client.get('/api/agent/h3-workflows').json()['active']
    receipt = call(client, 'activate', 'stale', import_id=imported['import_id'],
        expected_active_profile_id='wrong', expected_active_workflow_sha256='0'*64)
    assert receipt['status'] == 'failed'
    assert receipt['error']['code'] == 'profile_changed'
    assert client.get('/api/agent/h3-workflows').json()['active'] == before


def test_test_retry_calls_existing_test_once_and_returns_job_id(client, monkeypatch):
    calls = []
    async def test(import_id, body):
        calls.append(body)
        return {'job_id': 'job_setup', 'status': 'queued', 'import_id': import_id}
    monkeypatch.setattr(existing, 'test_h3_import', test)
    graph, mapping = direct_fixture()
    import_id = call(client, 'import', 'draft', workflow=graph, mapping=mapping)['result']['import_id']
    for _ in range(2):
        receipt = call(client, 'test', 'test-once', import_id=import_id, picture_asset_ids=['asset_1'])
        assert receipt['result']['job_id'] == 'job_setup'
    assert len(calls) == 1


def test_activation_without_test_evidence_is_rejected(client):
    graph, mapping = direct_fixture()
    import_id = call(client, 'import', 'draft', workflow=graph, mapping=mapping)['result']['import_id']
    active = client.get('/api/agent/h3-workflows').json()['active']
    receipt = call(client, 'activate', 'activate', import_id=import_id,
        expected_active_profile_id=active['profile_id'], expected_active_workflow_sha256=active['workflow_sha256'])
    assert receipt['status'] == 'failed'


def test_validation_and_test_snapshot_support_multiple_direct_file_slots(client, monkeypatch):
    from app.core.jobs.store import load_job, save_job
    from app.pipelines.h3_ref2va.pipeline import H3Ref2VaPipeline
    graph, mapping = direct_fixture()
    graph['image2'] = {'class_type': 'LoadImage', 'inputs': {'image': 'old2.png'}}
    graph['sample']['inputs']['image2'] = ['image2', 0]
    mapping['inputs']['pictures'].append({'node_id': 'image2', 'input_name': 'image'})
    seen = []
    class Validator:
        async def validate_workflow(self, filled):
            seen.append(filled)
            return {'valid': True, 'error_count': 0}
        async def aclose(self): pass
    monkeypatch.setattr(existing, 'ComfyMcpClient', Validator)
    monkeypatch.setattr(existing, '_resolve_picture_asset', lambda asset_id: (asset_id + '.png', b'fixture'))
    async def prepare_only(job, images):
        H3Ref2VaPipeline().prepare_job_submission(job)
        save_job(job)
    monkeypatch.setattr(existing, 'start_pipeline_job', prepare_only)
    import_id = call(client, 'import', 'multi', workflow=graph, mapping=mapping)['result']['import_id']
    validation = call(client, 'validate', 'validate', import_id=import_id)
    assert validation['status'] == 'succeeded', validation
    assert seen[0]['image']['inputs']['image'] != seen[0]['image2']['inputs']['image']
    receipt = call(client, 'test', 'test', import_id=import_id, picture_asset_ids=['asset_1', 'asset_2'])
    assert receipt['status'] == 'succeeded', receipt
    job = load_job(receipt['result']['job_id'])
    assert job.params['h3_contract_version'] == 4
    assert job.params['image_keys'] == ['picture_1', 'picture_2']
    assert H3ProfileStore().load_job_snapshot(job.id).mapping.inputs.kind == 'direct'


def test_direct_success_evidence_allows_activation_and_future_job_snapshot(client, monkeypatch):
    from test_h3_profile_test_run import _record_durable_success
    from app.core.jobs.store import create_job
    from app.pipelines.h3_ref2va.workflow import fill_profile_graph
    graph, mapping = direct_fixture()
    import_id = call(client, 'import', 'draft', workflow=graph, mapping=mapping)['result']['import_id']
    store = H3ProfileStore()
    workflow_hash, mapping_hash = store.import_identity(import_id)
    store.record_validation_success(import_id, workflow_sha256=workflow_hash, mapping_sha256=mapping_hash,
        report={'valid': True, 'issues': []}, comfy_payload={'valid': True})
    _record_durable_success(store, import_id)
    before = client.get('/api/agent/h3-workflows').json()['active']
    activated = call(client, 'activate', 'activate', import_id=import_id,
        expected_active_profile_id=before['profile_id'], expected_active_workflow_sha256=before['workflow_sha256'])
    assert activated['status'] == 'succeeded', activated
    assert activated['result']['active']['contract_version'] == 4
    job = create_job(pipeline_id='h3_ref2va', asset_kind='productions', name='production', params={})
    snapshot = store.snapshot_for_job(job)
    assert snapshot.profile_id == activated['result']['active']['profile_id']
    graph = fill_profile_graph(snapshot, {'frames': 56, 'images': ['production.png'], 'seed': 99})
    assert graph['image']['inputs']['image'] == 'production.png'
    assert graph['sample']['inputs']['seed'] == 99
    active = client.get('/api/agent/h3-workflows').json()['active']
    restored = call(client, 'select', 'restore', profile_id='builtin-official-h3',
        expected_active_profile_id=active['profile_id'], expected_active_workflow_sha256=active['workflow_sha256'])
    assert restored['status'] == 'succeeded', restored
    assert store.load_job_snapshot(job.id).profile_id == snapshot.profile_id
