"""Native cache discovery includes unassigned setup jobs and checks live files."""
import pytest
import httpx
from fastapi.testclient import TestClient

from app.config import settings
from app.main import create_app
from app.core.jobs.store import create_job, save_job
from app.core.schemas import JobStatus
from app.pipelines.h3_ref2va.schemas import H3Ref2VaJobResponse
from app.pipelines.h3_ref2va.video_context import native_source_job


def source_job(*, verified=True, host=None):
    job = create_job(pipeline_id='h3_ref2va', asset_kind='productions', name='native source', params={})
    job.status = JobStatus.succeeded
    job.params = {'h3_provider': 'local', 'native_latent_context': {
        'verified': verified, 'save_prefix': f'director-studio/{job.id}/native/clip', 'save_index': 1,
        'source_job_id': None, 'width': 864, 'height': 480,
        'comfy_base_url': host or settings.comfy_base_url, 'private_unrelated_value': 'not part of cache contract'}}
    save_job(job)
    return job


def test_job_response_exposes_cache_proof_without_unrelated_params():
    job = source_job()
    response = H3Ref2VaJobResponse.from_job(job).model_dump(mode='json')
    cache = response['native_latent_context']
    assert cache['verified'] is True
    assert cache['save_prefix'] == f'director-studio/{job.id}/native/clip'
    assert (cache['width'], cache['height']) == (864, 480)
    assert 'private_unrelated_value' not in cache


def test_discovery_returns_only_live_verified_sources_including_unassigned(monkeypatch):
    live = source_job()
    deleted = source_job()
    source_job(verified=False)
    source_job(host='http://127.0.0.1:9999')
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200 if live.id in str(request.url) else 404, headers={'content-length': '1024'}, request=request))
    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient',
        lambda **kwargs: original_client(transport=transport, **kwargs))
    with TestClient(create_app()) as client:
        result = client.get('/api/agent/h3-workflows/native-sources')
        assert result.status_code == 200, result.text
        sources = result.json()['sources']
        assert [source['job_id'] for source in sources] == [live.id]
        assert sources[0]['project_id'] is None
        assert sources[0]['cache_available'] is True
        assert sources[0]['native_latent_context']['verified'] is True


def test_missing_source_error_tells_agent_how_to_discover_sources():
    with pytest.raises(ValueError, match='list_h3_native_sources'):
        native_source_job(None)
