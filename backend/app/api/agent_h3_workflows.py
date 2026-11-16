"""Direct agent setup, independent of human official-node discovery."""
from __future__ import annotations

import asyncio
import hashlib
import json
import httpx
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import agent_tools as receipts
from . import h3_workflow_profiles as existing
from ..workflow_profiles.h3 import H3BoundaryMapping, H3ProfileStore, ProfileStorageError, ProfileChangedError, contract_version_for
from ..workflow_profiles.h3.models import H3DirectInputMapping
from ..workflow_profiles.h3.validator import validate_h3_contract

router = APIRouter(prefix='/agent/h3-workflows', tags=['agent-h3-workflows'])
admission = asyncio.Lock()


class WorkflowOperation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=128)
    action: Literal['import', 'configure', 'validate', 'test', 'select_test_output', 'activate', 'select']
    arguments: dict = Field(default_factory=dict)


@router.get('/contract')
def contract():
    return {'contract_version': 4, 'contract_revision': '4.3-native-source-discovery',
        'mapping_schema': H3BoundaryMapping.model_json_schema(),
        'native_context_examples': {
            'input_only': {'native_latent_context': {
                'load_path': {'node_id': '203', 'input_name': 'latent_path'},
                'load_index': {'node_id': '203', 'input_name': 'clip_index'}}},
            'input_and_output': {'native_latent_context': {
                'load_path': {'node_id': '203', 'input_name': 'latent_path'},
                'load_index': {'node_id': '203', 'input_name': 'clip_index'},
                'save_prefix': {'node_id': '205', 'input_name': 'filename_prefix'},
                'save_index': {'node_id': '205', 'input_name': 'clip_index'},
                'save_output_node_id': '205'}}},
        'instructions': [
            'Submit a ComfyUI API graph and mapping.inputs.kind=direct. No official H3 node is required.',
            'Bind existing literal input ports. Prompt and numeric inputs may fan out; numeric result=value*scale+offset.',
            'Picture and Audio arrays are ordered FILE-NAME slots, not tensor sockets. Each generation must supply exactly that many files.',
            'The workflow owns loaders, conditioning, sampling, context windows, overlap trimming and final video output.',
            'Optional mapping.native_latent_context is direct-only: save_prefix/save_index literal ports and save_output_node_id declare a terminal MiniMaxH3MotionContextSaveLatent; optional paired load_path/load_index target MiniMaxH3MotionContextLoadLatent. Omit both load ports for bootstrap.',
            'Input-only consumers may declare just load_path/load_index and omit the entire save group. Do not delete their cache loader or insert a save node just to satisfy validation. Such jobs do not export a verified cache and cannot supply native_source_job_id to another job.',
            'To update an existing import, use configure_h3_workflow with the complete mapping and expected workflow/mapping hashes from inspect_h3_workflow.lifecycle. This keeps the same import_id and graph, and invalidates old validation/test evidence. Revalidate afterward with a new request_id.',
            'Native tests use native_source_job_id from a succeeded local DS H3 job with verified cache, same Comfy host and dimensions; caller cache paths are never accepted. DS assigns a unique director-studio/<jobid>/native/clip output prefix and index 1, verifies it with HEAD, and skips video reencoding.',
            'Before generating a bootstrap, call list_h3_native_sources with no project_id to discover available sources across ALL projects and unassigned setup-test jobs. The tool checks canonical eligibility and current file existence with HEAD. get_video_job/list_video_jobs now expose native_latent_context metadata; verified records alone do not prove the file still exists.',
            'Activate/select may set standalone_profile_id to a tested source-free profile. It is persisted in active selection and routes shots without a context source; native continuation never silently runs without its source.',
            'Changing reference arity or graph topology requires a new import. No arbitrary code or automatic graph rewrite is performed.',
            'Validate, run an isolated test, inspect get_video_job, choose an output if needed, then activate.',
            'Activation changes all subsequent LOCAL generations globally; existing job snapshots remain unchanged.',
            'Use stable request_id for retries. A succeeded setup receipt containing job_id means test submitted, not video finished.',
        ]}


@router.get('')
def list_workflows():
    return existing.list_h3_profiles()


@router.get('/native-sources')
async def list_native_sources(limit: int = Query(default=20, ge=1, le=100), project_id: str | None = None):
    """Find existing native sources, including setup jobs outside project lists."""
    from ..core.jobs.store import list_jobs
    from ..pipelines.h3_ref2va.video_context import native_source_job
    from ..pipelines.h3_ref2va.schemas import H3Ref2VaJobResponse
    from ..config import settings
    sources = []
    unavailable = []
    checked = 0
    jobs = list_jobs(None, pipeline_id='h3_ref2va', project_id=project_id)
    async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
        for job in jobs:
            cache = (job.params or {}).get('native_latent_context')
            if not isinstance(cache, dict) or cache.get('verified') is not True:
                continue
            try:
                source = native_source_job(job.id)
            except ValueError:
                continue
            cache = source.params['native_latent_context']
            checked += 1
            try:
                response = await client.head(settings.comfy_base_url.rstrip('/') + '/view', params={
                    'type': 'output', 'subfolder': cache['save_prefix'].rsplit('/', 1)[0],
                    'filename': 'clip_00001.safetensors'})
                if response.status_code != 200 or int(response.headers.get('content-length', '0')) <= 0:
                    unavailable.append({'job_id': job.id, 'reason': f'cache_http_{response.status_code}_or_empty'})
                    continue
            except (httpx.HTTPError, ValueError) as exc:
                unavailable.append({'job_id': job.id, 'reason': type(exc).__name__})
                continue
            sources.append({'job_id': source.id, 'project_id': source.project_id,
                'name': source.name, 'width': cache['width'], 'height': cache['height'],
                'cache_available': True, 'cache_bytes': int(response.headers['content-length']),
                'native_latent_context': H3Ref2VaJobResponse.from_job(source).native_latent_context})
            if len(sources) >= limit:
                break
    return {'sources': sources, 'checked_candidates': checked, 'unavailable_sources': unavailable,
        'includes_unassigned': project_id is None}


@router.get('/imports/{import_id}')
def inspect_import(import_id: str):
    store = H3ProfileStore()
    try:
        mapping = store.load_import_mapping(import_id)
        return {'import_id': import_id, 'workflow': store.load_import_workflow(import_id),
            'mapping': mapping.model_dump(mode='json') if mapping else None,
            'contract_version': contract_version_for(mapping) if mapping else None,
            'lifecycle': store.import_lifecycle(import_id),
            'test_evidence': store.import_test_evidence(import_id)}
    except ProfileStorageError as exc:
        return existing._store_error(exc)


def require(arguments, name):
    value = arguments.get(name)
    if value is None:
        raise HTTPException(422, f'{name} is required')
    return value


async def execute(action, args):
    store = H3ProfileStore()
    if action == 'configure':
        import_id = require(args, 'import_id')
        graph, workflow_hash = store.load_import_workflow_snapshot(import_id)
        previous_mapping = store.load_import_mapping(import_id)
        mapping_hash = store.mapping_sha256(previous_mapping) if previous_mapping else None
        if (workflow_hash != require(args, 'expected_workflow_sha256')
                or mapping_hash != args.get('expected_mapping_sha256')):
            raise ProfileChangedError('Imported workflow or mapping changed; refresh inspect_h3_workflow before configuring')
        mapping = H3BoundaryMapping.model_validate(require(args, 'mapping'))
        if not isinstance(mapping.inputs, H3DirectInputMapping):
            raise HTTPException(422, 'Agent configuration requires mapping.inputs.kind=direct')
        report = validate_h3_contract(graph, mapping)
        if not report.valid:
            return existing._error(422, 'contract_validation_failed', 'Invalid direct workflow bindings',
                {'issues': [item.model_dump(mode='json') for item in report.issues]})
        store.save_import_output(import_id, mapping.output.node_id)
        store.save_import_mapping(import_id, mapping)
        return inspect_import(import_id)
    if action == 'import':
        mapping = H3BoundaryMapping.model_validate(require(args, 'mapping'))
        if not isinstance(mapping.inputs, H3DirectInputMapping):
            raise HTTPException(422, 'Agent direct import requires mapping.inputs.kind=direct')
        graph = require(args, 'workflow')
        report = validate_h3_contract(graph, mapping)
        if not report.valid:
            return existing._error(422, 'contract_validation_failed', 'Invalid direct workflow bindings',
                {'issues': [item.model_dump(mode='json') for item in report.issues]})
        import_id = store.create_import(graph, display_name=args.get('display_name', 'Agent H3 workflow'))
        store.save_import_output(import_id, mapping.output.node_id)
        store.save_import_mapping(import_id, mapping)
        return inspect_import(import_id)
    if action == 'select':
        expected = (require(args, 'expected_active_profile_id'), require(args, 'expected_active_workflow_sha256'))
        before = existing._active_payload(store)
        store.select_profile(require(args, 'profile_id'), expected_active=expected,
            standalone_profile_id=args.get('standalone_profile_id'))
        return {'previous_active': before, 'active': existing._active_payload(store)}
    import_id = require(args, 'import_id')
    if action == 'validate':
        return await existing.validate_h3_import(import_id)
    if action == 'test':
        return await existing.test_h3_import(import_id, existing.TestProfileRequest.model_validate(
            {key: value for key, value in args.items() if key != 'import_id'}))
    if action == 'select_test_output':
        return existing.select_h3_test_output(import_id, existing.SelectTestOutputRequest(
            artifact_index=require(args, 'artifact_index')))
    if action == 'activate':
        expected = (require(args, 'expected_active_profile_id'), require(args, 'expected_active_workflow_sha256'))
        before = existing._active_payload(store)
        profile = store.activate_import(import_id, expected_active=expected,
            standalone_profile_id=args.get('standalone_profile_id'))
        return {'profile_id': profile.id, 'previous_active': before, 'active': existing._active_payload(store)}
    raise HTTPException(422, 'Unsupported workflow operation')


@router.post('/operations')
async def operation(body: WorkflowOperation):
    """Persist an admission receipt before side effects. Never replay ambiguous writes."""
    task_id = 'at_' + hashlib.sha256(f'h3-workflow:{body.request_id}'.encode()).hexdigest()[:32]
    fingerprint = hashlib.sha256(json.dumps(body.model_dump(mode='json'), sort_keys=True,
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    async with admission:
        if receipts.task_path(task_id).exists():
            record = receipts.read(task_id)
            if record['fingerprint'] != fingerprint:
                raise HTTPException(409, 'request_id reused with different H3 workflow inputs')
        else:
            record = {'task_id': task_id, 'request_id': body.request_id, 'tool': 'h3_workflow_' + body.action,
                'fingerprint': fingerprint, 'boot': receipts.BOOT, 'status': 'running', 'progress': []}
            receipts.save(record)
            try:
                try:
                    result = await execute(body.action, body.arguments)
                except ProfileStorageError as exc:
                    result = existing._store_error(exc)
                if isinstance(result, JSONResponse):
                    record.update(status='failed', error={'status_code': result.status_code,
                        **json.loads(result.body)})
                else:
                    record.update(status='succeeded', result=result)
            except (HTTPException, ValidationError, ValueError, TypeError) as exc:
                record.update(status='failed', error={'code': 'invalid_request',
                    'message': str(exc), 'status_code': getattr(exc, 'status_code', 422)})
            except Exception as exc:
                # The pre-admission receipt survives unknown execution results.
                record.update(status='interrupted', error={'code': 'operation_interrupted',
                    'message': str(exc) + '; inspect state before submitting a new request ID'})
            finally:
                receipts.save(record)
        return {key: value for key, value in record.items() if key not in ('boot', 'fingerprint')}
