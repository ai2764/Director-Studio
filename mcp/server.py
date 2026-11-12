"""MCP bridge to a running Director Studio instance (Python MCP SDK v2)."""
from __future__ import annotations

import os
from urllib.parse import quote
import httpx
from mcp.server import MCPServer
from mcp import types
import copy
import json
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
import bridge_mailbox

BASE = os.environ.get('DIRECTOR_STUDIO_URL', 'http://127.0.0.1:8796').rstrip('/')
class DirectorMCP(MCPServer):
    native_schemas = {}

    async def list_tools(self):
        result = await super().list_tools()
        try:
            native = await request('GET', '/agent/tools')
        except (httpx.HTTPError, ToolError):
            # Mailbox and ordinary bridge tools remain discoverable during backend outages.
            return result
        self.native_schemas = {}
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

    def _tool_input_schema(self, name):
        return self.native_schemas.get(name) or super()._tool_input_schema(name)

    async def call_tool(self, name, arguments, context=None):
        if name not in self.native_schemas and self._tool_manager.get_tool(name) is None:
            # Hosts may cache a catalog across connections and call without listing again.
            await self.list_tools()
        if name in self.native_schemas:
            from jsonschema import Draft202012Validator
            errors = list(Draft202012Validator(self.native_schemas[name]).iter_errors(arguments))
            if errors: raise ToolError(errors[0].message)
            body = {k: v for k, v in arguments.items() if k != 'project_id'}
            result = await request('POST', '/agent/projects/' + segment(arguments['project_id']) + '/tools/' + segment(name), body=body)
            return types.CallToolResult(content=[types.TextContent(type='text', text=json.dumps(result, ensure_ascii=False))], structuredContent=result)
        return await super().call_tool(name, arguments, context)

mcp = DirectorMCP('Director Studio', version='2.0.0', instructions='Native tools execute directly on the requested project; no chat delegation. First read get_project_context for revision, evidence and available tools. Supply the actual user objective and a stable request_id to a native tool. A task receipt means accepted, not completed: inspect get_agent_task. Never infer success from prose. Project authoring mode need not change. Tool schemas are canonical; current availability and state checks are enforced in the backend.')

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
    """Read a video generation job and its current status."""
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
async def get_project_context(project_id: str, objective: str = '') -> dict:
    """Read current revision, agent evidence, and currently available native tool contracts. Read this before authoring tool arguments."""
    return await request('GET', '/agent/projects/' + segment(project_id) + '/context', params={'objective': objective})

@mcp.tool()
async def get_agent_task(task_id: str) -> dict:
    """Read accepted native operation status, bounded progress, actual result and actions. succeeded/failed/interrupted are terminal. After interrupted or failed writes inspect project state before choosing a new request ID."""
    return await request('GET', '/agent/tasks/' + segment(task_id))

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
