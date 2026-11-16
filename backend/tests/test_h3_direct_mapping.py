"""Direct profiles bind literal ports without an official H3 node."""
import copy

import pytest

from app.workflow_profiles.h3.models import H3BoundaryMapping, contract_version_for, ResolvedH3Profile
from app.workflow_profiles.h3.validator import validate_h3_contract
from app.workflow_profiles.h3.store import H3ProfileStore
from app.pipelines.h3_ref2va.workflow import fill_profile_graph


def direct_fixture():
    graph = {
        'text': {'class_type': 'TextEncoder', 'inputs': {'text': 'placeholder'}},
        'size': {'class_type': 'Dimensions', 'inputs': {'w': 864, 'h': 480}},
        'image': {'class_type': 'LoadImage', 'inputs': {'image': 'placeholder.png'}},
        'sample': {'class_type': 'H3NSampler', 'inputs': {
            'positive': ['text', 0], 'size': ['size', 0], 'image': ['image', 0],
            'count': 56, 'seed': 1}},
        'out': {'class_type': 'SaveVideo', 'inputs': {'video': ['sample', 0]}},
    }
    mapping = {'inputs': {'kind': 'direct',
        'prompt': [{'node_id': 'text', 'input_name': 'text'}],
        'width': [{'node_id': 'size', 'input_name': 'w'}],
        'height': [{'node_id': 'size', 'input_name': 'h'}],
        'frames': [{'node_id': 'sample', 'input_name': 'count', 'offset': 22}],
        'seed': [{'node_id': 'sample', 'input_name': 'seed'}],
        'pictures': [{'node_id': 'image', 'input_name': 'image'}],
    }, 'output': {'node_id': 'out'}}
    return graph, mapping


def profile_for(graph, mapping):
    return ResolvedH3Profile('test-direct', graph, H3BoundaryMapping.model_validate(mapping), '', 'custom')


def native_fixture(continuation=True):
    graph, mapping = direct_fixture()
    graph['cache'] = {'class_type': 'MiniMaxH3MotionContextSaveLatent', 'inputs': {
        'latent': ['sample', 0], 'filename_prefix': 'demo/clip', 'clip_index': 2}}
    native = {'save_prefix': {'node_id': 'cache', 'input_name': 'filename_prefix'},
        'save_index': {'node_id': 'cache', 'input_name': 'clip_index'}, 'save_output_node_id': 'cache'}
    if continuation:
        graph['load'] = {'class_type': 'MiniMaxH3MotionContextLoadLatent', 'inputs': {
            'latent_path': 'demo', 'clip_index': 1}}
        graph['sample']['inputs']['context'] = ['load', 0]
        native.update(load_path={'node_id': 'load', 'input_name': 'latent_path'},
            load_index={'node_id': 'load', 'input_name': 'clip_index'})
    mapping['native_latent_context'] = native
    return graph, mapping


def test_native_boundary_fills_both_roots_without_rewiring_or_demo_cache_reuse():
    graph, mapping = native_fixture()
    original = copy.deepcopy(graph)
    profile = profile_for(graph, mapping)
    assert validate_h3_contract(graph, profile.mapping).valid
    filled = fill_profile_graph(profile, {'frames': 56, 'images': ['one.png'],
        'native_latent_context': {'save_prefix': 'director-studio/new/native/clip', 'save_index': 1,
            'load_path': 'director-studio/source/native', 'load_index': 1}})
    assert filled['cache']['inputs']['filename_prefix'] == 'director-studio/new/native/clip'
    assert filled['cache']['inputs']['clip_index'] == 1
    assert filled['load']['inputs'] == {'latent_path': 'director-studio/source/native', 'clip_index': 1}
    assert filled['sample']['inputs']['context'] == ['load', 0]
    assert set(filled) == set(graph)
    assert graph == original


@pytest.mark.parametrize('damage', ['class', 'port', 'terminal', 'load_pair', 'mixed', 'unmapped_save', 'unmapped_load', 'missing_native'])
def test_native_boundary_rejects_invalid_cache_contracts(damage):
    graph, mapping = native_fixture()
    if damage == 'class': graph['cache']['class_type'] = 'SaveImage'
    if damage == 'port': mapping['native_latent_context']['save_index']['input_name'] = 'latent'
    if damage == 'terminal': graph['out']['inputs']['bad'] = ['cache', 0]
    if damage == 'load_pair': del mapping['native_latent_context']['load_path']
    if damage == 'mixed': mapping['context_video'] = {'node_id': 'load', 'input_name': 'latent_path'}
    if damage == 'unmapped_save': graph['hidden'] = copy.deepcopy(graph['cache'])
    if damage == 'unmapped_load':
        graph['hidden'] = copy.deepcopy(graph['load'])
        graph['sample']['inputs']['other_context'] = ['hidden', 0]
    if damage == 'missing_native': del mapping['native_latent_context']
    try:
        profile = profile_for(graph, mapping)
    except ValueError:
        return
    assert not validate_h3_contract(graph, profile.mapping).valid


def test_native_fill_never_uses_frozen_cache_when_lineage_missing():
    graph, mapping = native_fixture()
    profile = profile_for(graph, mapping)
    with pytest.raises(ValueError, match='native'):
        fill_profile_graph(profile, {'frames': 56, 'images': ['one.png']})


def test_absent_native_boundary_preserves_existing_mapping_hash():
    graph, mapping = direct_fixture()
    parsed = H3BoundaryMapping.model_validate(mapping)
    assert 'native_latent_context' not in parsed.model_dump(mode='json')


def test_native_continuation_routes_to_explicit_bootstrap_without_source(monkeypatch):
    from app.config import settings
    graph, mapping = native_fixture()
    graph_start, mapping_start = native_fixture(False)
    active = profile_for(graph, mapping)
    bootstrap = profile_for(graph_start, mapping_start)
    store = H3ProfileStore()
    monkeypatch.setattr(store, 'resolve_active', lambda: active)
    monkeypatch.setattr(store, 'resolve_profile', lambda _id: bootstrap)
    monkeypatch.setattr(settings, 'h3_standalone_profile_id', 'bootstrap')
    assert store.resolve_for_submission(has_context=False) is bootstrap
    assert store.resolve_for_submission(has_context=True) is active
    monkeypatch.setattr(settings, 'h3_standalone_profile_id', '')
    with pytest.raises(RuntimeError):
        store.resolve_for_submission(has_context=False)


def test_native_job_lineage_uses_only_verified_local_source(monkeypatch):
    from types import SimpleNamespace
    from app.config import settings
    from app.core.schemas import JobStatus
    from app.pipelines.h3_ref2va.video_context import prepare_native_latent_context
    graph, mapping = native_fixture()
    source = SimpleNamespace(id='source', pipeline_id='h3_ref2va', status=JobStatus.succeeded,
        params={'h3_provider': 'local', 'width': 864, 'height': 480,
            'native_latent_context': {'save_prefix': 'director-studio/source/native/clip',
                'save_index': 1, 'verified': True, 'comfy_base_url': settings.comfy_base_url,
                'width': 864, 'height': 480}})
    monkeypatch.setattr('app.core.jobs.store.load_job', lambda _id: source)
    job = SimpleNamespace(id='next', params={'native_source_job_id': 'source', 'width': 864, 'height': 480})
    lineage = prepare_native_latent_context(job, profile_for(graph, mapping))
    assert lineage['save_prefix'] == 'director-studio/next/native/clip'
    assert lineage['load_path'] == 'director-studio/source/native'
    assert lineage['load_index'] == 1
    assert lineage['source_job_id'] == 'source'
    assert lineage['verified'] is False
    for damage in ('status', 'verified', 'host', 'size', 'path'):
        broken = copy.deepcopy(source)
        if damage == 'status': broken.status = JobStatus.failed
        if damage == 'verified': broken.params['native_latent_context']['verified'] = False
        if damage == 'host': broken.params['native_latent_context']['comfy_base_url'] = 'http://elsewhere'
        if damage == 'size': broken.params['native_latent_context']['width'] = 1280
        if damage == 'path': broken.params['native_latent_context']['save_prefix'] = 'demo/clip'
        monkeypatch.setattr('app.core.jobs.store.load_job', lambda _id, value=broken: value)
        with pytest.raises(ValueError):
            prepare_native_latent_context(job, profile_for(graph, mapping))


@pytest.mark.asyncio
async def test_native_cache_verification_uses_head_before_marking_verified(monkeypatch):
    from types import SimpleNamespace
    from app.pipelines.h3_ref2va.pipeline import H3Ref2VaPipeline
    from app.config import settings
    import httpx
    calls = []
    status = 404
    real_client = httpx.AsyncClient
    async def handle(request):
        calls.append(request)
        return httpx.Response(status)
    def client(**kwargs):
        assert kwargs['trust_env'] is False
        return real_client(transport=httpx.MockTransport(handle), **kwargs)
    monkeypatch.setattr(httpx, 'AsyncClient', client)
    job = SimpleNamespace(id='next', params={'native_latent_context': {
        'save_prefix': 'director-studio/next/native/clip', 'save_index': 1,
        'comfy_base_url': settings.comfy_base_url, 'verified': False}})
    with pytest.raises((ValueError, httpx.HTTPError)):
        await H3Ref2VaPipeline().verify_job_outputs(job)
    assert job.params['native_latent_context']['verified'] is False
    status = 200
    await H3Ref2VaPipeline().verify_job_outputs(job)
    assert job.params['native_latent_context']['verified'] is True
    assert calls[-1].method == 'HEAD'
    assert calls[-1].url.params['filename'] == 'clip_00001.safetensors'
    assert calls[-1].url.params['subfolder'] == 'director-studio/next/native'


def test_direct_split_ports_fill_without_official_node_or_topology_changes():
    graph, mapping = direct_fixture()
    original = copy.deepcopy(graph)
    profile = profile_for(graph, mapping)
    assert contract_version_for(profile.mapping) == 4
    assert validate_h3_contract(graph, profile.mapping).valid
    filled = fill_profile_graph(profile, {'prompt': 'Test', 'width': 1280, 'height': 720,
        'frames': 56, 'seed': 42, 'images': ['uploaded.png'], 'audios': []})
    assert filled['text']['inputs']['text'] == 'Test'
    assert filled['size']['inputs'] == {'w': 1280, 'h': 720}
    assert filled['sample']['inputs']['count'] == 78
    assert filled['sample']['inputs']['seed'] == 42
    assert filled['image']['inputs']['image'] == 'uploaded.png'
    assert set(filled) == set(graph)
    assert graph == original


@pytest.mark.parametrize('damage', ['missing', 'linked', 'unreachable', 'duplicate', 'wrong_type'])
def test_direct_mapping_rejects_invalid_ports(damage):
    graph, mapping = direct_fixture()
    if damage == 'missing': mapping['inputs']['prompt'][0]['input_name'] = 'missing'
    if damage == 'linked': mapping['inputs']['prompt'][0] = {'node_id': 'sample', 'input_name': 'positive'}
    if damage == 'unreachable':
        graph['unused'] = {'class_type': 'TextEncoder', 'inputs': {'text': ''}}
        mapping['inputs']['prompt'][0]['node_id'] = 'unused'
    if damage == 'duplicate': mapping['inputs']['height'] = mapping['inputs']['width']
    if damage == 'wrong_type': graph['size']['inputs']['w'] = '864'
    report = validate_h3_contract(graph, H3BoundaryMapping.model_validate(mapping))
    assert not report.valid
    assert report.issues


def test_direct_media_arity_never_reuses_placeholder_files():
    graph, mapping = direct_fixture()
    profile = profile_for(graph, mapping)
    for images in ([], ['one.png', 'two.png']):
        with pytest.raises(ValueError, match='Picture'):
            fill_profile_graph(profile, {'frames': 56, 'images': images})


def test_direct_video_file_binding_does_not_require_motion_context_nodes():
    graph, mapping = direct_fixture()
    graph['video'] = {'class_type': 'CustomVideoLoader', 'inputs': {'path': 'old.mp4'}}
    graph['sample']['inputs']['context'] = ['video', 0]
    mapping['context_video'] = {'node_id': 'video', 'input_name': 'path'}
    profile = profile_for(graph, mapping)
    assert validate_h3_contract(graph, profile.mapping).valid
    filled = fill_profile_graph(profile, {'frames': 56, 'images': ['new.png'], 'context_video': 'new.mp4'})
    assert filled['video']['inputs']['path'] == 'new.mp4'
    assert filled['sample']['inputs']['count'] == 78


def test_direct_mapping_round_trips_in_store_and_keeps_legacy_mapping_bytes():
    graph, mapping = direct_fixture()
    store = H3ProfileStore()
    import_id = store.create_import(graph)
    store.save_import_output(import_id, 'out')
    parsed = H3BoundaryMapping.model_validate(mapping)
    store.save_import_mapping(import_id, parsed)
    assert store.load_import_mapping(import_id) == parsed
    legacy = store.resolve_builtin().mapping.model_dump(mode='json')
    assert 'kind' not in legacy['inputs']


@pytest.mark.parametrize('params', [{'width': -1}, {'height': 0}, {'seed': -1}])
def test_direct_numeric_sources_keep_application_constraints(params):
    graph, mapping = direct_fixture()
    with pytest.raises(ValueError):
        fill_profile_graph(profile_for(graph, mapping), {'frames': 56, 'images': ['one.png'], **params})


def test_direct_seed_preserves_all_64_bits():
    graph, mapping = direct_fixture()
    seed = 2**64 - 1
    filled = fill_profile_graph(profile_for(graph, mapping), {'frames': 56, 'images': ['one.png'], 'seed': seed})
    assert filled['sample']['inputs']['seed'] == seed


def test_direct_context_submission_does_not_apply_official_overlap_budget(monkeypatch):
    from types import SimpleNamespace
    from app.config import settings
    from app.core.projects import video_context as source
    from app.core.h3.frames import MAX_FRAMES
    from app.pipelines.h3_ref2va.video_context import context_runtime_options
    graph, mapping = direct_fixture()
    mapping['context_video'] = {'node_id': 'video', 'input_name': 'file'}
    runtime = context_runtime_options(SimpleNamespace(context_frames=None, carry_audio=False),
        profile_for(graph, mapping))
    assert runtime['workflow_owns_context'] is True
    monkeypatch.setattr(settings, 'video_context_enabled', True)
    resolved = SimpleNamespace(filename='context.mp4', data=b'video',
        provenance={'context_frames': 22, 'workflow_owns_context': runtime['workflow_owns_context']})
    monkeypatch.setattr(source, 'resolve_video_context', lambda *args, **kwargs: resolved)
    result = source.submission_video_context(SimpleNamespace(), width=864, height=480, delivered_frames=MAX_FRAMES)
    assert result['data'] == b'video'
