"""MCP bridge to a running Director Studio instance (Python MCP SDK v2)."""
from __future__ import annotations

import os
from urllib.parse import quote
import httpx
from mcp.server import MCPServer
from mcp import types
import copy
import json
from typing import Literal
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
import bridge_mailbox

BASE = os.environ.get('DIRECTOR_STUDIO_URL', 'http://127.0.0.1:8796').rstrip('/')

H3ToolName = Literal[
    'get_h3_workflow_contract', 'list_h3_workflows', 'list_h3_native_sources',
    'import_h3_workflow', 'configure_h3_workflow', 'inspect_h3_workflow',
    'validate_h3_workflow', 'test_h3_workflow', 'select_h3_test_output',
    'activate_h3_workflow', 'select_h3_workflow',
]
H3_TOOLS = frozenset(H3ToolName.__args__)
DISCOVERY_TOOLS = {'get_tool_contract', 'h3_workflow'}


def full_catalog():
    mode = os.environ.get('DIRECTOR_MCP_CATALOG_MODE', 'compact')
    if mode not in {'compact', 'full'}:
        raise ValueError('DIRECTOR_MCP_CATALOG_MODE must be compact or full')
    return mode == 'full'


def short_description(description):
    return description.split('. ', 1)[0].split('\n', 1)[0][:220]


def validate_arguments(name, schema, arguments):
    from jsonschema import Draft202012Validator
    errors = Draft202012Validator(schema).iter_errors(arguments)
    error = next(errors, None)
    if error is not None:
        path = '.'.join(map(str, error.absolute_path)) or '<root>'
        raise ToolError(f'{name}: {path}: {error.message}. Read get_tool_contract("{name}") before retrying.')


class DirectorMCP(MCPServer):
    native_schemas = {}

    async def full_tools(self):
        result = await super().list_tools()
        self.native_schemas = {}
        try:
            native = await request('GET', '/agent/tools')
        except (httpx.HTTPError, ToolError):
            # Mailbox and ordinary bridge tools remain discoverable during backend outages.
            return result
        for tool in native['tools']:
            args = copy.deepcopy(tool['parameters'])
            definitions = args.pop('$defs', None)
            schema = {'type': 'object', 'properties': {
                'project_id': {'type': 'string'},
                'request_id': {'type': 'string', 'minLength': 1, 'maxLength': 128, 'description': 'Stable unique operation ID; retry the identical input with the same ID.'},
                'expected_revision': {'type': 'string', 'description': 'Revision from get_project_context; refresh after every completed mutation.'},
                'objective': {'type': 'string', 'description': 'Actual user instruction authorizing this operation; never invent approval.'},
                'arguments': args},
                'required': ['project_id', 'request_id', 'expected_revision', 'objective', 'arguments'], 'additionalProperties': False}
            if definitions: schema['$defs'] = definitions
            self.native_schemas[tool['name']] = schema
            result.append(types.Tool(name=tool['name'], description=tool['description'] + ' Native Director tool; returns an asynchronous task receipt. Check get_agent_task for actual completion. Availability and business preconditions come from get_project_context.', inputSchema=schema))
        return result

    async def list_tools(self):
        tools = await self.full_tools()
        if full_catalog():
            return [tool for tool in tools if tool.name not in DISCOVERY_TOOLS]
        result = []
        for tool in tools:
            if tool.name in H3_TOOLS:
                continue
            if tool.name in self.native_schemas:
                tool = tool.model_copy(deep=True)
                tool.input_schema['properties']['arguments'] = {'type': 'object'}
                tool.input_schema.pop('$defs', None)
                tool.description = (short_description(tool.description) +
                    f'. Read get_tool_contract("{tool.name}") for arguments before calling. Receipt is not completion.')
            result.append(tool)
        return result

    async def tool_contract(self, name):
        # Registered bridge/H3 contracts are available even if the backend is down.
        registered = await super().list_tools()
        tools = registered if any(t.name == name for t in registered) else await self.full_tools()
        tool = next((t for t in tools if t.name == name), None)
        if tool is None:
            raise ToolError(f'Unknown or unavailable tool: {name}. Refresh tools/list; check backend availability.')
        return tool

    def _tool_input_schema(self, name):
        return self.native_schemas.get(name) or super()._tool_input_schema(name)

    async def call_tool(self, name, arguments, context=None):
        if name not in self.native_schemas and self._tool_manager.get_tool(name) is None:
            # Hosts may cache a catalog across connections and call without listing again.
            await self.full_tools()
        if name in self.native_schemas:
            validate_arguments(name, self.native_schemas[name], arguments)
            body = {k: v for k, v in arguments.items() if k != 'project_id'}
            result = await request('POST', '/agent/projects/' + segment(arguments['project_id']) + '/tools/' + segment(name), body=body)
            return types.CallToolResult(content=[types.TextContent(type='text', text=json.dumps(result, ensure_ascii=False))], structuredContent=result)
        return await super().call_tool(name, arguments, context)

mcp = DirectorMCP('Director Studio', version='2.1.0', instructions='Native tools execute on the requested project. Read get_project_context for revision, evidence and available names. Read get_tool_contract(tool_name) for complete arguments before an unfamiliar call; never guess omitted fields. H3 setup uses h3_workflow; read its operation contract first and fetch get_h3_workflow_contract only when connecting a workflow. Supply the actual user objective and stable request_id. A receipt is not completion: inspect get_agent_task/get_video_job. Backend schemas, availability and state checks remain authoritative.')


@mcp.tool()
async def get_tool_contract(tool_name: str) -> dict:
    """Read ONE tool's full description and JSON input schema before calling it; names come from tools/list or project context. Read-only."""
    tool = await mcp.tool_contract(tool_name)
    return tool.model_dump(mode='json', by_alias=True, exclude_none=True)


@mcp.tool()
async def h3_workflow(tool_name: H3ToolName, arguments: dict) -> types.CallToolResult:
    """H3 workflow setup on demand. Read get_tool_contract(tool_name), then pass its exact arguments. Generation requires user authorization; activate/select affect subsequent local videos globally."""
    if tool_name not in H3_TOOLS:
        raise ToolError('Only H3 setup operations are accepted; read get_tool_contract("h3_workflow").')
    tool = await mcp.tool_contract(tool_name)
    schema = copy.deepcopy(tool.input_schema)
    schema['additionalProperties'] = False
    validate_arguments(tool_name, schema, arguments)
    return await mcp.call_tool(tool_name, arguments)

async def request(method: str, path: str, *, params=None, body=None, form_data=None, files=None, timeout=30):
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        response = await client.request(method, BASE + '/api' + path, params=params, json=body, data=form_data, files=files)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text[:600]
            raise ToolError(f'Director Studio API HTTP {response.status_code}: {detail}. Inspect state before retrying a write.') from exc
        return response.json()

def segment(value: str) -> str:
    if not value or '/' in value or '\\' in value or value in ('.', '..'):
        raise ValueError('Invalid identifier')
    return quote(value, safe='')

def transport_security_settings() -> TransportSecuritySettings:
    # Public deployment names belong in environment configuration, not source.
    extra_hosts = [host.strip() for host in os.environ.get('DIRECTOR_MCP_ALLOWED_HOSTS', '').split(',') if host.strip()]
    return TransportSecuritySettings(
        allowed_hosts=list(dict.fromkeys(['127.0.0.1:*', 'localhost:*', *extra_hosts])),
        allowed_origins=[],
    )

@mcp.tool()
async def list_projects() -> list:
    """List Director Studio projects and their IDs."""
    return await request('GET', '/projects')

@mcp.tool()
async def get_project(project_id: str) -> dict:
    """Get project details and current shots using the exact project ID."""
    return await request('GET', '/projects/' + segment(project_id))

@mcp.tool()
async def get_shot(shot_id: str) -> dict:
    """Get the current details, references and status for a shot."""
    return await request('GET', '/shots/' + segment(shot_id))

@mcp.tool()
async def list_assets(kind: str, project_id: str = '', include_unassigned: bool = False) -> list:
    """List library assets by kind, e.g. actors, scenes, props, costumes or layouts."""
    params = {'kind': kind, 'include_unassigned': str(include_unassigned).lower()}
    if project_id: params['project_id'] = project_id
    return await request('GET', '/library', params=params)

@mcp.tool()
async def import_asset(request_id: str, project_id: str, kind: str, name: str,
                       image_base64: str, filename: str, notes: str = '', file_key: str = 'master') -> dict:
    """Import ONE external image into the specified project's library without regeneration. Send raw base64 image bytes (PNG/JPEG/WebP/GIF, at most 40 MiB decoded), not a local path or URL. Each call creates one LibraryAsset with one file key. Use fullbody_threeview/bust_threeview for actor sheets, master for ordinary references, layout for layouts. Preserve identity/usage descriptions in notes. Reuse identical request_id and inputs for retries; different images need different IDs. Returns status and result.asset with the exact ID/files for patch_shot_refs. succeeded means imported, not bound to a shot. Large base64 payloads are best sent programmatically from the client."""
    import base64
    import binascii
    limit = 40 * 1024 * 1024
    if not request_id.strip() or len(request_id) > 128 or not filename or len(filename) > 255:
        raise ToolError('Provide request_id (1-128 characters) and a filename (1-255 characters).')
    if len(image_base64) > ((limit + 2) // 3) * 4:
        raise ToolError('Image exceeds 40 MiB decoded limit.')
    try:
        data = base64.b64decode(image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ToolError('image_base64 must contain raw valid base64 image bytes, without a data URL prefix.') from exc
    if not data or len(data) > limit:
        raise ToolError('Image must be nonempty and at most 40 MiB.')
    segment(project_id)
    return await request('POST', '/agent/assets/import',
                         form_data={'request_id': request_id, 'project_id': project_id, 'kind': kind,
                                    'name': name, 'notes': notes, 'file_key': file_key},
                         files={'file': (filename, data, 'application/octet-stream')}, timeout=120)

@mcp.tool()
async def list_video_jobs(project_id: str, limit: int = 30) -> list:
    """List video generation jobs for a project; inspect status before retrying a generation."""
    return await request('GET', '/h3-ref2va/jobs', params={'project_id': project_id, 'limit': max(1, min(limit, 100))})

@mcp.tool()
async def get_video_job(job_id: str) -> dict:
    """Read video status and native_latent_context metadata, if present. verified records describe generation-time proof; use list_h3_native_sources to check current source availability."""
    return await request('GET', '/h3-ref2va/jobs/' + segment(job_id))

@mcp.tool()
async def get_director_runtime() -> dict:
    """Inspect the Director agent runtime and configured capabilities."""
    return await request('GET', '/director/runtime')

@mcp.tool()
async def create_project(request_id: str, name: str, script_text: str = '', mode: str = 'director', authoring_mode: str = 'director_agent') -> dict:
    """Create a new project, saving the supplied script verbatim. Required request_id makes identical retries return the original result. Never reuse an ID for different inputs. Returns persisted operation status and the actual created project; after interrupted status inspect state before any new attempt."""
    return await request('POST', '/agent/projects', body={'request_id': request_id, 'name': name, 'script_text': script_text, 'mode': mode, 'authoring_mode': authoring_mode})

@mcp.tool()
async def get_project_context(project_id: str, objective: str = '', include_tool_schemas: bool | None = None) -> dict:
    """Read revision, evidence, instructions and available tool names before mutations. Fetch detailed arguments with get_tool_contract. Set include_tool_schemas=true for all schemas."""
    result = await request('GET', '/agent/projects/' + segment(project_id) + '/context', params={'objective': objective})
    if include_tool_schemas is None:
        include_tool_schemas = full_catalog()
    if not include_tool_schemas:
        result = {**result, 'available_tools': [
            {'name': tool['function']['name'], 'description': short_description(tool['function']['description'])}
            for tool in result.get('available_tools', [])]}
    return result

@mcp.tool()
async def get_agent_task(task_id: str) -> dict:
    """Read accepted native operation status, bounded progress, actual result and actions. succeeded/failed/interrupted are terminal. After interrupted or failed writes inspect project state before choosing a new request ID."""
    return await request('GET', '/agent/tasks/' + segment(task_id))

@mcp.tool()
async def get_h3_workflow_contract() -> dict:
    """Read the CURRENT full mapping JSON schema and contract_revision before connecting H3/H3N. Includes native_latent_context and explicit input-only/native output examples. Refresh this tool rather than assume cached v4 documentation. No official Ref2AV node is required."""
    return await request('GET', '/agent/h3-workflows/contract')


@mcp.tool()
async def list_h3_workflows() -> dict:
    """List installed H3 workflow profiles and current global active ID/hash. Refresh before activation or rollback. Applies to subsequent local generation jobs across all projects."""
    return await request('GET', '/agent/h3-workflows')


@mcp.tool()
async def list_h3_native_sources(limit: int = 20, project_id: str | None = None) -> dict:
    """Discover existing native_source_job_id candidates BEFORE generating a new bootstrap. Omit project_id to include ALL projects and unassigned setup-test jobs. Checks succeeded local jobs, verified cache ownership, current Comfy host and a fresh nonempty cache-file HEAD. Returns job IDs, dimensions, metadata and cache_available=true; unavailable_sources distinguishes missing files/network failures from no recorded cache. Read-only; does not generate, activate or reinstall profiles."""
    params = {'limit': limit}
    if project_id is not None:
        params['project_id'] = project_id
    return await request('GET', '/agent/h3-workflows/native-sources', params=params, timeout=180)


@mcp.tool()
async def import_h3_workflow(request_id: str, workflow: dict, mapping: dict,
                             display_name: str = 'Agent H3 workflow') -> dict:
    """Import ComfyUI API JSON plus a COMPLETE explicit direct mapping; no automatic node discovery or manual setup sequence. Read get_h3_workflow_contract first. Set mapping.inputs.kind=direct and bind existing literal ports; no official H3 node is needed. Returns a durable receipt with result.import_id, workflow, mapping and lifecycle. Import does not activate or generate. Reuse identical request_id and inputs for retries."""
    return await request('POST', '/agent/h3-workflows/operations', body={
        'request_id': request_id, 'action': 'import', 'arguments': {
            'workflow': workflow, 'mapping': mapping, 'display_name': display_name}}, timeout=120)


@mcp.tool()
async def configure_h3_workflow(request_id: str, import_id: str, mapping: dict,
                                 expected_workflow_sha256: str,
                                 expected_mapping_sha256: str | None) -> dict:
    """Replace the COMPLETE mapping of an existing import without changing its graph or import_id. Read inspect_h3_workflow first and supply lifecycle.workflow_sha256/mapping_sha256 for stale-write checks. Supports native input-only load_path/load_index bindings when the graph has no SaveLatent node. Clears previous validation/test proof; revalidate using a new request_id. Does not generate or activate. Identical request_id retries return the same receipt."""
    return await request('POST', '/agent/h3-workflows/operations', body={
        'request_id': request_id, 'action': 'configure', 'arguments': {
            'import_id': import_id, 'mapping': mapping,
            'expected_workflow_sha256': expected_workflow_sha256,
            'expected_mapping_sha256': expected_mapping_sha256}}, timeout=120)


@mcp.tool()
async def inspect_h3_workflow(import_id: str) -> dict:
    """Read the exact imported workflow, accepted mapping, contract version and validation/test lifecycle. No heuristic remapping. Inspect after interrupted writes or before deciding the next step."""
    return await request('GET', '/agent/h3-workflows/imports/' + segment(import_id))


@mcp.tool()
async def validate_h3_workflow(request_id: str, import_id: str) -> dict:
    """Validate explicit ports and the filled graph against ComfyUI dependencies without queueing video generation. Returns a receipt; check status and structured error details. Validation alone does not authorize activation."""
    return await request('POST', '/agent/h3-workflows/operations', body={
        'request_id': request_id, 'action': 'validate', 'arguments': {'import_id': import_id}}, timeout=180)


@mcp.tool()
async def test_h3_workflow(request_id: str, import_id: str, picture_asset_ids: list[str],
                           audio_asset_ids: list[str] | None = None,
                           context_project_id: str | None = None,
                           context_upload_id: str | None = None,
                           native_source_job_id: str | None = None) -> dict:
    """Start ONE isolated local GPU test after successful validation. Requires actual user authorization to generate. Picture/Audio asset IDs are ordered and must exactly match mapped slots. For native latent continuation supply native_source_job_id from a succeeded local DS job with verified cache; test dimensions match that source. Native bootstrap needs no source. For a context-video mapping supply existing project upload/project IDs. Reuse request_id on timeout. Receipt success means submitted; poll get_video_job and inspect_h3_workflow. Output is not bound to a production shot."""
    return await request('POST', '/agent/h3-workflows/operations', body={
        'request_id': request_id, 'action': 'test', 'arguments': {
            'import_id': import_id, 'picture_asset_ids': picture_asset_ids,
            'audio_asset_ids': audio_asset_ids or [], 'context_project_id': context_project_id,
            'context_upload_id': context_upload_id, 'native_source_job_id': native_source_job_id}}, timeout=120)


@mcp.tool()
async def select_h3_test_output(request_id: str, import_id: str, artifact_index: int) -> dict:
    """Choose one observed successful test video when multiple outputs require a selection. artifact_index comes from test output candidates; no generation rerun."""
    return await request('POST', '/agent/h3-workflows/operations', body={
        'request_id': request_id, 'action': 'select_test_output', 'arguments': {
            'import_id': import_id, 'artifact_index': artifact_index}})


@mcp.tool()
async def activate_h3_workflow(request_id: str, import_id: str,
                               expected_active_profile_id: str,
                               expected_active_workflow_sha256: str,
                               standalone_profile_id: str | None = None) -> dict:
    """Install and globally activate a validated, successfully tested import for subsequent LOCAL videos. Supply current active ID/hash; stale values fail. Optional standalone_profile_id persists a tested source-free fallback for shots without context. Existing snapshots remain unchanged. Returns previous_active and active. Check receipt.status."""
    return await request('POST', '/agent/h3-workflows/operations', body={
        'request_id': request_id, 'action': 'activate', 'arguments': {
            'import_id': import_id, 'expected_active_profile_id': expected_active_profile_id,
            'expected_active_workflow_sha256': expected_active_workflow_sha256,
            'standalone_profile_id': standalone_profile_id}})


@mcp.tool()
async def select_h3_workflow(request_id: str, profile_id: str,
                             expected_active_profile_id: str,
                             expected_active_workflow_sha256: str,
                             standalone_profile_id: str | None = None) -> dict:
    """Switch globally to an installed tested profile or builtin-official-h3. Supply current active ID/hash. Optional standalone_profile_id persists a tested source-free fallback for shots without context. Does not regenerate or alter existing snapshots. Returns previous_active and active."""
    return await request('POST', '/agent/h3-workflows/operations', body={
        'request_id': request_id, 'action': 'select', 'arguments': {
            'profile_id': profile_id, 'expected_active_profile_id': expected_active_profile_id,
            'expected_active_workflow_sha256': expected_active_workflow_sha256,
            'standalone_profile_id': standalone_profile_id}})


@mcp.tool()
async def send_message(sender: str, recipient: str, subject: str, body: str, reply_to: int | None = None, client_message_id: str | None = None) -> dict:
    """Send a durable message between codex and muse. Supply a unique client_message_id for safe retries; reply_to links a response to a message. This mailbox is shared by the tailnet and sender names are not authenticated."""
    return bridge_mailbox.send(sender, recipient, subject, body, reply_to, client_message_id)

@mcp.tool()
async def list_messages(recipient: str, after_id: int = 0, limit: int = 50, include_acknowledged: bool = True) -> dict:
    """Read messages addressed to codex or muse, ordered by ID. Save next_after_id to poll for new messages; use include_acknowledged=false for unread items."""
    return bridge_mailbox.list_messages(recipient, after_id, limit, include_acknowledged)

@mcp.tool()
async def acknowledge_message(recipient: str, message_id: int) -> dict:
    """Mark a message addressed to the specified recipient as read. The message remains in history."""
    return bridge_mailbox.acknowledge(recipient, message_id)

if __name__ == '__main__':
    mcp.run(transport='streamable-http', host='127.0.0.1', port=int(os.environ.get('DIRECTOR_MCP_PORT', '8798')), stateless_http=True, json_response=True, max_request_body_size=60 * 1024 * 1024, transport_security=transport_security_settings())
