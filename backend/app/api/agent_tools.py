"""Transport-neutral, project-scoped access to the native Director executor."""
from __future__ import annotations
import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, File, Form, UploadFile
from pydantic import BaseModel, Field
from .projects import get_director_service, CreateProjectBody, create_project_endpoint
from ..config import settings
from ..agents.director.harness_runtime import BackendTurn
from ..agents.director.tool_schema import DIRECTOR_TOOL_SCHEMAS, GPT_REF_FRAME_TOOL
from ..core.projects.chat_sessions import director_chat_sessions, DirectorChatSessionConflict
from ..core.managed_runs.store import active_run_for_project

router = APIRouter(prefix='/agent', tags=['agent-tools'])
BOOT = uuid.uuid4().hex
running = set()
admission = asyncio.Lock()

def task_path(task_id):
    if not task_id.startswith('at_') or not task_id[3:].isalnum():
        raise HTTPException(400, 'Invalid task ID')
    return Path(settings.data_dir) / 'agent-tasks' / (task_id + '.json')

def save(record):
    record['updated_at'] = datetime.now(timezone.utc).isoformat()
    record.setdefault('created_at', record['updated_at'])
    path = task_path(record['task_id'])
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)

def read(task_id):
    path = task_path(task_id)
    if not path.exists(): raise HTTPException(404, 'Task not found')
    record = json.loads(path.read_text(encoding='utf-8'))
    if record['status'] in ('queued', 'running') and record['boot'] != BOOT:
        record.update(status='interrupted', error='Backend restarted. Inspect project state; do not automatically replay this write.')
    return record

class NativeCall(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    expected_revision: str = Field(min_length=1)
    objective: str = Field(min_length=1, max_length=20000)
    arguments: dict = Field(default_factory=dict)

class NativeProject(CreateProjectBody):
    request_id: str = Field(min_length=1, max_length=128)

@router.post('/assets/import')
async def import_native_asset(
    request_id: str = Form(..., min_length=1, max_length=128),
    project_id: str = Form(...),
    kind: str = Form(...),
    name: str = Form(..., min_length=1, max_length=300),
    notes: str = Form('', max_length=16000),
    file_key: str = Form('master'),
    file: UploadFile = File(...),
):
    import io
    import re
    from PIL import Image, UnidentifiedImageError
    from .library import import_external_asset, _MAX_IMPORT_BYTES
    from ..core.projects.store import load_project
    if load_project(project_id) is None:
        raise HTTPException(404, 'Project not found')
    if kind not in {'actors', 'costumes', 'scenes', 'props', 'layouts'}:
        raise HTTPException(422, 'Select actors, costumes, scenes, props or layouts')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', file_key):
        raise HTTPException(422, 'file_key must be a simple library key, not a path')
    data = await file.read(_MAX_IMPORT_BYTES + 1)
    if not data or len(data) > _MAX_IMPORT_BYTES:
        raise HTTPException(413, 'Image must be nonempty and at most 40 MiB')
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {'PNG', 'JPEG', 'WEBP', 'GIF'}:
                raise HTTPException(422, 'Unsupported image format')
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, 'Image bytes could not be verified') from exc
    digest = hashlib.sha256(data).hexdigest()
    task_id = 'at_' + hashlib.sha256(f'import:{project_id}:{request_id}'.encode()).hexdigest()[:32]
    fingerprint = hashlib.sha256(json.dumps([project_id, kind, name, notes, file_key, file.filename, digest]).encode()).hexdigest()
    async with admission:
        if task_path(task_id).exists():
            record = read(task_id)
            if record['fingerprint'] != fingerprint:
                raise HTTPException(409, 'request_id reused with different asset input')
            return {k: v for k, v in record.items() if k not in ('boot', 'fingerprint')}
        record = {'task_id': task_id, 'request_id': request_id, 'project_id': project_id,
                  'tool': 'import_asset', 'fingerprint': fingerprint, 'boot': BOOT,
                  'status': 'running', 'progress': []}
        save(record)
        try:
            await file.seek(0)
            asset = await import_external_asset(file=file, kind=kind, name=name, notes=notes,
                                                project_id=project_id, file_key=file_key)
            record.update(status='succeeded', result={'imported': True, 'asset': asset.model_dump(mode='json'),
                                                      'sha256': digest, 'bytes': len(data)})
        except Exception as exc:
            record.update(status='failed', error=str(exc))
            raise
        finally:
            save(record)
        return {k: v for k, v in record.items() if k not in ('boot', 'fingerprint')}

@router.post('/projects')
async def create_native_project(body: NativeProject):
    task_id = 'at_' + hashlib.sha256(f'create:{body.request_id}'.encode()).hexdigest()[:32]
    fingerprint = hashlib.sha256(json.dumps(body.model_dump(mode='json'), sort_keys=True).encode()).hexdigest()
    async with admission:
        if task_path(task_id).exists():
            record = read(task_id)
            if record['fingerprint'] != fingerprint: raise HTTPException(409, 'request_id reused with different input')
            return {k: v for k, v in record.items() if k not in ('boot', 'fingerprint')}
        record = {'task_id': task_id, 'request_id': body.request_id, 'fingerprint': fingerprint,
                  'boot': BOOT, 'status': 'running', 'progress': [], 'tool': 'create_project'}
        save(record)
        try:
            project = await create_project_endpoint(CreateProjectBody(**body.model_dump(exclude={'request_id'})))
            record.update(status='succeeded', project_id=project.id,
                          result={'created': True, 'project': project.model_dump(mode='json')})
        except Exception as exc:
            record.update(status='failed', error=str(exc))
            raise
        finally: save(record)
        return {k: v for k, v in record.items() if k not in ('boot', 'fingerprint')}

@router.get('/tools')
async def catalog():
    tools = {t['function']['name']: t['function'] for t in [*DIRECTOR_TOOL_SCHEMAS, GPT_REF_FRAME_TOOL]}
    return {'version': 2, 'tools': list(tools.values())}

@router.get('/projects/{project_id}/context')
async def context(project_id: str, objective: str = '', svc=Depends(get_director_service)):
    try:
        turn = BackendTurn(project_id, objective, svc, None)
        packet = turn.context()
        return {'project_id': project_id, 'revision': turn.snapshot()[2], 'state': packet['state'], 'instructions': packet['system'],
                'available_tools': packet['tools']}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

async def execute(record, turn, name, session_id):
    async def progress(event):
        record['progress'] = [*record['progress'], event][-50:]
        save(record)
    turn.on_progress = progress
    try:
        record['status'] = 'running'
        # Durable evidence before any executor mutation, for manual recovery.
        record['before'] = {'project': turn.snapshot()[0].model_dump(mode='json'),
                            'shots': [s.model_dump(mode='json') for s in turn.snapshot()[1]]}
        save(record)
        result = await turn.tool({'name': name, 'arguments': record['arguments'], 'call_id': record['request_id']})
        record.update(status='succeeded' if result.get('ok') else 'failed', result=result,
                      revision=turn.snapshot()[2], actions=turn.actions)
    except asyncio.CancelledError:
        record.update(status='interrupted', error='Task cancelled; inspect state before retrying.')
        raise
    except Exception as exc:
        record.update(status='failed', error=str(exc))
    finally:
        save(record)
        await director_chat_sessions.finish(record['project_id'], session_id)

@router.post('/projects/{project_id}/tools/{name}', status_code=202)
async def call(project_id: str, name: str, body: NativeCall, svc=Depends(get_director_service)):
    task_id = 'at_' + hashlib.sha256(f'{project_id}:{body.request_id}'.encode()).hexdigest()[:32]
    fingerprint = hashlib.sha256(json.dumps([name, body.model_dump()], sort_keys=True).encode()).hexdigest()
    async with admission:
        if task_path(task_id).exists():
            existing = read(task_id)
            if existing['fingerprint'] != fingerprint: raise HTTPException(409, 'request_id reused with different input')
            return {'task_id': task_id, 'status': existing['status'], 'deduplicated': True}
        turn = BackendTurn(project_id, body.objective, svc, None)
        try:
            packet = turn.context()
        except ValueError as exc: raise HTTPException(404, str(exc)) from exc
        schema = next((t['function'] for t in packet['tools'] if t['function']['name'] == name), None)
        if schema is None: raise HTTPException(409, 'Tool unavailable in current context; refresh agent context')
        from jsonschema import Draft202012Validator
        errors = list(Draft202012Validator(schema['parameters']).iter_errors(body.arguments))
        if errors: raise HTTPException(422, errors[0].message)
        if turn.snapshot()[2] != body.expected_revision: raise HTTPException(409, 'Project revision changed; refresh context')
        if active_run_for_project(project_id): raise HTTPException(409, 'A managed run is active')
        try: session = await director_chat_sessions.reserve(project_id)
        except DirectorChatSessionConflict as exc: raise HTTPException(409, str(exc)) from exc
        record = {'task_id': task_id, 'request_id': body.request_id, 'project_id': project_id,
                  'tool': name, 'arguments': body.arguments, 'fingerprint': fingerprint,
                  'boot': BOOT, 'status': 'queued', 'progress': []}
        turn.expected_state = body.expected_revision
        turn.user_message_id = task_id
        try: save(record)
        except Exception:
            await director_chat_sessions.finish(project_id, session.session_id)
            raise
        task = asyncio.create_task(execute(record, turn, name, session.session_id))
        running.add(task)
        task.add_done_callback(running.discard)
        await director_chat_sessions.attach(project_id, session.session_id, task)
        return {'task_id': task_id, 'status': 'queued', 'project_id': project_id}

@router.get('/tasks/{task_id}')
async def get_task(task_id: str):
    result = read(task_id)
    # Recovery evidence is kept on disk, not resent on each status request.
    return {k: v for k, v in result.items() if k not in ('before', 'fingerprint', 'boot', 'arguments')}
