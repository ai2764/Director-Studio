import asyncio
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from app.config import settings
from app.integrations.comfy_mcp import (
    ComfyMcpClient,
    ComfyMcpError,
    PersistentMcpToolSession,
)


def _result(payload: dict, *, is_error: bool = False):
    return SimpleNamespace(
        is_error=is_error,
        content=[SimpleNamespace(type="text", text=json.dumps(payload))],
    )


class FakeToolSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        response = self.responses.pop(0)
        if callable(response):
            return response(name, arguments)
        return response

    async def aclose(self) -> None:
        return None


def test_server_parameters_resolve_installed_windows_entrypoints(
    monkeypatch,
    tmp_path,
):
    python = tmp_path / "Python313" / "python.exe"
    scripts = python.parent / "Scripts"
    scripts.mkdir(parents=True)
    mcp_exe = scripts / "comfy-mcp.exe"
    comfy_exe = scripts / "comfy.exe"
    mcp_exe.write_bytes(b"")
    comfy_exe.write_bytes(b"")

    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.setattr("app.integrations.comfy_mcp.shutil.which", lambda _name: None)
    monkeypatch.setattr(settings, "comfy_mcp_command", "comfy-mcp")
    monkeypatch.setattr(settings, "comfy_mcp_comfy_bin", "comfy")

    params = PersistentMcpToolSession()._server_parameters()

    assert Path(params.command) == mcp_exe
    assert Path(params.env["COMFY_BIN"]) == comfy_exe


@pytest.mark.asyncio
async def test_persistent_session_opens_and_closes_contexts_in_same_task(monkeypatch):
    class TaskBoundContext:
        def __init__(self, value):
            self.value = value
            self.owner = None

        async def __aenter__(self):
            self.owner = asyncio.current_task()
            return self.value

        async def __aexit__(self, *_exc):
            if asyncio.current_task() is not self.owner:
                raise RuntimeError("context exited from a different task")

    class FakeClient:
        async def initialize(self):
            return None

        async def call_tool(self, _name, _arguments):
            return _result({"ok": True})

    mcp_module = ModuleType("mcp")
    mcp_module.StdioServerParameters = lambda **kwargs: kwargs
    mcp_module.ClientSession = lambda *_streams: TaskBoundContext(FakeClient())
    client_module = ModuleType("mcp.client")
    client_module.__path__ = []
    stdio_module = ModuleType("mcp.client.stdio")
    stdio_module.stdio_client = lambda _params: TaskBoundContext((object(), object()))
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.client", client_module)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio_module)

    session = PersistentMcpToolSession()
    client = ComfyMcpClient(session=session)
    result = await asyncio.create_task(client.call_tool("server_info", {}))

    await client.aclose()

    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_client_rejects_tools_outside_the_generation_whitelist():
    session = FakeToolSession([])
    client = ComfyMcpClient(session=session)

    with pytest.raises(ComfyMcpError, match="not allowed"):
        await client.call_tool("install_node", {"names": ["anything"]})

    assert session.calls == []


@pytest.mark.asyncio
async def test_upload_inputs_returns_cloud_names_in_logical_key_order():
    session = FakeToolSession(
        [
            _result(
                {
                    "uploads": [
                        {"cloud_name": "ds_job_1_actor.png"},
                        {"cloud_name": "ds_job_1_layout.jpg"},
                    ]
                }
            )
        ]
    )
    client = ComfyMcpClient(session=session)

    uploaded = await client.upload_inputs(
        "job_1",
        {
            "actor": ("actor.png", b"actor-bytes"),
            "layout": ("layout.jpg", b"layout-bytes"),
        },
    )

    assert uploaded == {
        "actor": "ds_job_1_actor.png",
        "layout": "ds_job_1_layout.jpg",
    }
    name, arguments = session.calls[0]
    assert name == "upload_file"
    assert arguments["overwrite"] is True
    assert [Path(path).name for path in arguments["paths"]] == [
        "ds_job_1_actor.png",
        "ds_job_1_layout.jpg",
    ]
    assert all(not Path(path).exists() for path in arguments["paths"])


@pytest.mark.asyncio
async def test_upload_inputs_skips_mcp_tool_when_there_are_no_inputs():
    session = FakeToolSession([])
    client = ComfyMcpClient(session=session)

    uploaded = await client.upload_inputs("job_text_only_actor", {})

    assert uploaded == {}
    assert session.calls == []


@pytest.mark.asyncio
async def test_submit_workflow_validates_before_queueing_and_removes_temp_graph():
    session = FakeToolSession(
        [
            _result({"valid": True, "error_count": 0, "warnings": []}),
            _result({"status": "queued", "prompt_id": "prompt_123"}),
        ]
    )
    client = ComfyMcpClient(session=session)

    prompt_id = await client.submit_workflow({"1": {"class_type": "SaveVideo"}})

    assert prompt_id == "prompt_123"
    assert [name for name, _ in session.calls] == [
        "validate_workflow",
        "run_workflow",
    ]
    workflow_paths = [Path(args["workflow_path"]) for _, args in session.calls]
    assert all(not path.exists() for path in workflow_paths)
    assert session.calls[1][1]["wait"] is False
    assert session.calls[1][1]["confirm_spend"] is False


@pytest.mark.asyncio
async def test_submit_workflow_repairs_new_save_video_dynamic_codec_before_queueing():
    submitted_graphs: list[dict] = []

    def capture_graph(_name: str, arguments: dict):
        submitted_graphs.append(
            json.loads(Path(arguments["workflow_path"]).read_text(encoding="utf-8"))
        )
        if len(submitted_graphs) == 1:
            return _result(
                {
                    "valid": False,
                    "error_count": 1,
                    "errors": [
                        {
                            "node_id": "92",
                            "field": "format.codec",
                            "code": "required_input_missing",
                            "message": (
                                "required dynamic-combo input 'format.codec' is missing"
                            ),
                        }
                    ],
                }
            )
        if len(submitted_graphs) == 2:
            return _result({"valid": True, "error_count": 0, "warnings": []})
        return _result({"status": "queued", "prompt_id": "prompt_new_comfy"})

    session = FakeToolSession([capture_graph, capture_graph, capture_graph])
    client = ComfyMcpClient(session=session)
    original = {
        "92": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["130", 0],
                "filename_prefix": "video/MiniMax_H3",
                "format": "auto",
                "codec": "auto",
            },
        }
    }

    prompt_id = await client.submit_workflow(original)

    assert prompt_id == "prompt_new_comfy"
    assert [name for name, _ in session.calls] == [
        "validate_workflow",
        "validate_workflow",
        "run_workflow",
    ]
    assert submitted_graphs[0]["92"]["inputs"] == {
        "video": ["130", 0],
        "filename_prefix": "video/MiniMax_H3",
        "format": "auto",
        "codec": "auto",
    }
    assert submitted_graphs[1]["92"]["inputs"]["format.codec"] == "auto"
    assert submitted_graphs[2] == submitted_graphs[1]
    assert "format.codec" not in original["92"]["inputs"]


@pytest.mark.asyncio
async def test_validate_workflow_returns_payload_and_removes_temp_graph():
    session = FakeToolSession(
        [_result({"valid": True, "error_count": 0, "warnings": []})]
    )
    client = ComfyMcpClient(session=session)

    payload = await client.validate_workflow(
        {"1": {"class_type": "SaveVideo", "inputs": {}}}
    )

    assert payload == {"valid": True, "error_count": 0, "warnings": []}
    assert [name for name, _ in session.calls] == ["validate_workflow"]
    workflow_path = Path(session.calls[0][1]["workflow_path"])
    assert workflow_path.suffixes == [".api", ".json"]
    assert not workflow_path.exists()


@pytest.mark.asyncio
async def test_validate_workflow_formats_all_structured_errors():
    session = FakeToolSession(
        [
            _result(
                {
                    "valid": False,
                    "error_count": 2,
                    "errors": [
                        {"node_id": "136", "message": "missing model"},
                        {"node_id": "92", "error": "invalid codec"},
                    ],
                }
            )
        ]
    )
    client = ComfyMcpClient(session=session)

    with pytest.raises(
        ComfyMcpError,
        match="missing model.*invalid codec",
    ):
        await client.validate_workflow({"136": {"class_type": "Missing"}})

    workflow_path = Path(session.calls[0][1]["workflow_path"])
    assert not workflow_path.exists()


@pytest.mark.asyncio
async def test_submit_workflow_repairs_new_save_video_dynamic_codec_before_queueing():
    submitted_graphs: list[dict] = []

    def capture_graph(_name: str, arguments: dict):
        submitted_graphs.append(
            json.loads(Path(arguments["workflow_path"]).read_text(encoding="utf-8"))
        )
        if len(submitted_graphs) == 1:
            return _result(
                {
                    "valid": False,
                    "error_count": 1,
                    "errors": [
                        {
                            "node_id": "92",
                            "field": "format.codec",
                            "code": "required_input_missing",
                            "message": (
                                "required dynamic-combo input 'format.codec' is missing"
                            ),
                        }
                    ],
                }
            )
        if len(submitted_graphs) == 2:
            return _result({"valid": True, "error_count": 0, "warnings": []})
        return _result({"status": "queued", "prompt_id": "prompt_new_comfy"})

    session = FakeToolSession([capture_graph, capture_graph, capture_graph])
    client = ComfyMcpClient(session=session)
    original = {
        "92": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["130", 0],
                "filename_prefix": "video/MiniMax_H3",
                "format": "auto",
                "codec": "auto",
            },
        }
    }

    prompt_id = await client.submit_workflow(original)

    assert prompt_id == "prompt_new_comfy"
    assert [name for name, _ in session.calls] == [
        "validate_workflow",
        "validate_workflow",
        "run_workflow",
    ]
    assert submitted_graphs[0]["92"]["inputs"] == {
        "video": ["130", 0],
        "filename_prefix": "video/MiniMax_H3",
        "format": "auto",
        "codec": "auto",
    }
    assert submitted_graphs[1]["92"]["inputs"]["format.codec"] == "auto"
    assert submitted_graphs[2] == submitted_graphs[1]
    assert "format.codec" not in original["92"]["inputs"]


@pytest.mark.asyncio
async def test_submit_workflow_stops_when_live_validation_fails():
    session = FakeToolSession(
        [
            _result(
                {
                    "valid": False,
                    "error_count": 1,
                    "errors": [{"node_id": "136", "message": "missing model"}],
                }
            )
        ]
    )
    client = ComfyMcpClient(session=session)

    with pytest.raises(ComfyMcpError, match="missing model"):
        await client.submit_workflow({"136": {"class_type": "Missing"}})

    assert [name for name, _ in session.calls] == ["validate_workflow"]


@pytest.mark.asyncio
async def test_client_preserves_plain_text_from_mcp_tool_errors():
    session = FakeToolSession(
        [
            SimpleNamespace(
                is_error=True,
                content=[
                    SimpleNamespace(
                        type="text",
                        text="Error executing tool run_workflow",
                    )
                ],
            )
        ]
    )
    client = ComfyMcpClient(session=session)

    with pytest.raises(
        ComfyMcpError,
        match="MCP tool run_workflow failed: Error executing tool run_workflow",
    ):
        await client.call_tool("run_workflow", {"workflow_path": "test.api.json"})


@pytest.mark.asyncio
async def test_wait_for_completion_retries_structured_timeouts_until_completed():
    session = FakeToolSession(
        [
            _result(
                {
                    "timed_out": True,
                    "status": {"status": "running", "outputs": []},
                }
            ),
            _result(
                {
                    "status": "completed",
                    "outputs": ["http://127.0.0.1:8188/view?filename=video.mp4"],
                }
            ),
        ]
    )
    client = ComfyMcpClient(session=session, job_timeout_sec=5.0)

    result = await client.wait_for_completion(
        "prompt_123",
        cancel_event=asyncio.Event(),
    )

    assert result["status"] == "completed"
    assert result["outputs"] == ["http://127.0.0.1:8188/view?filename=video.mp4"]
    assert [name for name, _ in session.calls] == ["job", "job"]
    assert all(args["action"] == "wait" for _, args in session.calls)


@pytest.mark.asyncio
async def test_fetch_outputs_reads_downloaded_files_before_temp_cleanup():
    def write_output(_name: str, arguments: dict):
        path = Path(arguments["out_dir"]) / "prompt_000.mp4"
        path.write_bytes(b"video-bytes")
        return _result(
            {
                "files": [
                    {
                        "path": str(path),
                        "url": "http://127.0.0.1:8188/view?filename=video.mp4",
                        "size": len(b"video-bytes"),
                    }
                ]
            }
        )

    session = FakeToolSession([write_output])
    client = ComfyMcpClient(session=session)

    outputs = await client.fetch_outputs("prompt_123")

    assert len(outputs) == 1
    assert outputs[0].filename == "prompt_000.mp4"
    assert outputs[0].source_url == ("http://127.0.0.1:8188/view?filename=video.mp4")
    assert outputs[0].data == b"video-bytes"
    output_dir = Path(session.calls[0][1]["out_dir"])
    assert not output_dir.exists()
