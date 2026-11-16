"""A context consumer need not export another cache or rewrite its graph."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.workflow_profiles.h3 import H3BoundaryMapping, H3ProfileStore
from app.workflow_profiles.h3.validator import validate_h3_contract
from app.pipelines.h3_ref2va.workflow import fill_profile_graph
from app.pipelines.h3_ref2va.pipeline import H3Ref2VaPipeline
from test_h3_direct_mapping import native_fixture, profile_for
from test_agent_h3_workflows import call


def input_only_fixture():
    graph, mapping = native_fixture()
    del graph['cache']
    for name in ('save_prefix', 'save_index', 'save_output_node_id'):
        del mapping['native_latent_context'][name]
    return graph, mapping


def test_native_input_only_preserves_loader_without_requiring_save_node():
    graph, mapping = input_only_fixture()
    profile = profile_for(graph, mapping)
    assert validate_h3_contract(graph, profile.mapping).valid
    filled = fill_profile_graph(profile, {'frames': 56, 'images': ['ref.png'],
        'native_latent_context': {'load_path': 'director-studio/source/native', 'load_index': 1}})
    assert set(filled) == set(graph)
    assert filled['load']['inputs']['latent_path'] == 'director-studio/source/native'
    assert filled['sample']['inputs']['context'] == ['load', 0]


@pytest.mark.asyncio
async def test_input_only_job_does_not_claim_a_verified_output_cache(monkeypatch):
    from app.pipelines.h3_ref2va import video_context as module
    graph, mapping = input_only_fixture()
    source = SimpleNamespace(id='source', params={'native_latent_context': {
        'save_prefix': 'director-studio/source/native/clip'}})
    monkeypatch.setattr(module, 'native_source_job', lambda *args, **kwargs: source)
    job = SimpleNamespace(id='consumer', params={'native_source_job_id': 'source', 'width': 864, 'height': 480})
    cache = module.prepare_native_latent_context(job, profile_for(graph, mapping))
    assert cache.get('save_prefix') is None
    def forbidden(*args, **kwargs):
        raise AssertionError('Input-only workflows do not have an output cache to probe')
    monkeypatch.setattr('httpx.AsyncClient', forbidden)
    await H3Ref2VaPipeline().verify_job_outputs(job)
    assert cache.get('verified') is not True


def test_configure_existing_import_replays_and_invalidates_old_validation():
    graph, mapping = input_only_fixture()
    old = {**mapping, 'native_latent_context': None}
    store = H3ProfileStore()
    import_id = store.create_import(graph)
    store.save_import_output(import_id, 'out')
    store.save_import_mapping(import_id, H3BoundaryMapping.model_validate(old))
    workflow_sha, mapping_sha = store.import_identity(import_id)
    store.record_validation_success(import_id, workflow_sha256=workflow_sha, mapping_sha256=mapping_sha,
        report={'valid': True}, comfy_payload={'valid': True})
    with TestClient(create_app()) as client:
        args = dict(import_id=import_id, mapping=mapping,
            expected_workflow_sha256=workflow_sha, expected_mapping_sha256=mapping_sha)
        first = call(client, 'configure', 'configure', **args)
        assert first['status'] == 'succeeded', first
        assert first['result']['import_id'] == import_id
        assert first['result']['lifecycle']['status'] == 'mapped'
        assert store.load_import_workflow(import_id) == graph
        assert store.load_import_mapping(import_id).native_latent_context.load_path.node_id == 'load'
        assert call(client, 'configure', 'configure', **args)['result'] == first['result']
        stale = call(client, 'configure', 'stale', **args)
        assert stale['status'] == 'failed'
        assert stale['error']['code'] == 'profile_changed'


def test_native_requires_a_complete_save_group_or_a_load_pair():
    graph, mapping = input_only_fixture()
    mapping['native_latent_context']['save_prefix'] = {'node_id': 'cache', 'input_name': 'filename_prefix'}
    with pytest.raises(ValueError, match='save'):
        H3BoundaryMapping.model_validate(mapping)
