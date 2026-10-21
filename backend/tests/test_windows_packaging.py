"""Windows packaging logic, tested on Linux with fakes (no Windows APIs are called)."""

from __future__ import annotations

import ctypes
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest
from app import launcher
from app.core.process import winjob
from app.runtime_paths import default_user_data_dir, resolve_runtime_paths

REPO = Path(__file__).resolve().parents[2]


# ---- Job Object --------------------------------------------------------------------------

class FakeKernel32:
    def __init__(self, *, fail: str | None = None) -> None:
        self.fail = fail
        self.calls: list[tuple] = []
        self.limit_flags: int | None = None
        self.closed: list[int] = []

    def CreateJobObjectW(self, attrs, name):
        self.calls.append(("create", attrs, name))
        return 0 if self.fail == "create" else 4242

    def SetInformationJobObject(self, handle, info_class, info_ptr, size):
        self.calls.append(("set", handle, info_class, size))
        info = ctypes.cast(info_ptr, ctypes.POINTER(winjob.JOBOBJECT_EXTENDED_LIMIT_INFORMATION)).contents
        self.limit_flags = info.BasicLimitInformation.LimitFlags
        return 0 if self.fail == "set" else 1

    def GetCurrentProcess(self):
        return -1  # pseudo handle, like Windows

    def AssignProcessToJobObject(self, job, process):
        self.calls.append(("assign", job, process))
        return 0 if self.fail == "assign" else 1

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1


@pytest.fixture(autouse=True)
def _reset_job(monkeypatch):
    monkeypatch.setattr(winjob, "_CURRENT", None)


def test_job_object_kills_on_close_and_allows_breakaway():
    k32 = FakeKernel32()
    job = winjob.contain_current_process(kernel32=k32, platform_name="nt", environ={})
    assert job is not None and job.handle == 4242
    assert k32.limit_flags & winjob.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    assert k32.limit_flags & winjob.JOB_OBJECT_LIMIT_BREAKAWAY_OK
    set_call = next(c for c in k32.calls if c[0] == "set")
    assert set_call[2] == winjob.JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS
    assert set_call[3] == ctypes.sizeof(winjob.JOBOBJECT_EXTENDED_LIMIT_INFORMATION)
    assert ("assign", 4242, -1) in k32.calls
    assert k32.closed == []  # the handle must stay open for the launcher's lifetime
    # idempotent: a second call returns the same job without creating another
    assert winjob.contain_current_process(kernel32=FakeKernel32(), platform_name="nt", environ={}) is job


def test_job_object_struct_layout_matches_win64():
    # JOBOBJECT_EXTENDED_LIMIT_INFORMATION is 144 bytes on x64 (BASIC 64 + IO 48 + 4 * SIZE_T)
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(winjob.JOBOBJECT_BASIC_LIMIT_INFORMATION) == 64
        assert ctypes.sizeof(winjob.JOBOBJECT_EXTENDED_LIMIT_INFORMATION) == 144


@pytest.mark.parametrize("stage", ["create", "set", "assign"])
def test_job_object_failures_raise_and_release_handle(stage):
    k32 = FakeKernel32(fail=stage)
    with pytest.raises(winjob.JobObjectError):
        winjob.contain_current_process(kernel32=k32, platform_name="nt", environ={})
    if stage != "create":
        assert k32.closed == [4242]
    assert winjob._CURRENT is None


def test_job_object_skipped_off_windows_or_when_disabled():
    k32 = FakeKernel32()
    assert winjob.contain_current_process(kernel32=k32, platform_name="posix", environ={}) is None
    assert winjob.contain_current_process(kernel32=k32, platform_name="nt", environ={"DS_NO_JOB_OBJECT": "1"}) is None
    assert k32.calls == []


def test_browser_is_started_outside_the_job_with_fallback():
    calls = []

    def popen(argv, **kw):
        calls.append((argv, kw["creationflags"]))
        if kw["creationflags"] & winjob.CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError("outer job forbids breakaway")
        return object()

    winjob.open_url_outside_job("http://127.0.0.1:8790", popen=popen)
    assert calls[0][0] == ["cmd.exe", "/d", "/c", "start", "", "http://127.0.0.1:8790"]
    assert calls[0][1] == winjob.CREATE_BREAKAWAY_FROM_JOB | winjob.CREATE_NO_WINDOW
    assert calls[1][1] == winjob.CREATE_NO_WINDOW
    assert winjob.detached_creationflags("posix") == 0


# ---- launcher stop on Windows ------------------------------------------------------------

def _cfg(tmp_path: Path) -> launcher.LaunchConfig:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    paths = resolve_runtime_paths(source_root=tmp_path, environ={})
    return launcher.LaunchConfig(paths=paths, data_dir=tmp_path, log_dir=tmp_path / "logs", run_dir=run_dir)


def test_windows_stop_uses_stop_request_instead_of_terminate(tmp_path):
    cfg = _cfg(tmp_path)
    # stand-in launcher: exits as soon as the stop request appears (like _watch_ready does)
    script = ("import pathlib,sys,time\n"
              "p = pathlib.Path(sys.argv[1])\n"
              "while not p.exists(): time.sleep(0.05)\n"
              "p.unlink()\n")
    proc = subprocess.Popen([sys.executable, "-c", script, str(cfg.run_dir / launcher.STOP_REQUEST)])
    try:
        (cfg.run_dir / "launcher.json").write_text(json.dumps(
            {"pid": proc.pid, "create_time": psutil.Process(proc.pid).create_time()}))
        started = time.monotonic()
        assert launcher.stop_running(cfg, timeout=15, windows=True) is True
        assert proc.wait(5) == 0  # exited by itself (terminate() would give a signal exit code)
        assert time.monotonic() - started < 15
        assert not (cfg.run_dir / launcher.STOP_REQUEST).exists()
    finally:
        if proc.poll() is None:
            proc.kill()


def test_consume_stop_request_is_one_shot(tmp_path):
    (tmp_path / launcher.STOP_REQUEST).write_text("1")
    assert launcher.consume_stop_request(tmp_path) is True
    assert launcher.consume_stop_request(tmp_path) is False


# ---- path resolution -------------------------------------------------------------------------

def test_windows_user_data_dir_and_install_defaults(tmp_path):
    assert default_user_data_dir(platform="win32", environ={"LOCALAPPDATA": "C:\\Users\\u\\AppData\\Local"}) \
        == Path("C:\\Users\\u\\AppData\\Local") / "DirectorStudio"
    build_windows = _load_installer_module("build_windows")
    defaults = build_windows.install_defaults("cu128")
    assert defaults["DS_COMFY_MCP_COMMAND"] == "${INSTALL_ROOT}\\runtime\\bin\\comfy-mcp.cmd"
    assert "DS_COMFY_DEV_CPU" not in defaults and build_windows.install_defaults("cpu")["DS_COMFY_DEV_CPU"] == "true"
    (tmp_path / "ds-install.json").write_text(json.dumps({"defaults": defaults}))
    home = tmp_path / "home"
    paths = resolve_runtime_paths(source_root=tmp_path, environ={"DS_HOME": str(home)})
    assert paths.installed and paths.env_file == home / ".env"
    assert paths.install_defaults["DS_COMFY_MCP_COMMAND"] == f"{tmp_path}\\runtime\\bin\\comfy-mcp.cmd"


def test_launcher_finds_windows_layout_binaries(tmp_path, monkeypatch):
    (tmp_path / "ds-install.json").write_text("{}")
    (tmp_path / "runtime" / "python").mkdir(parents=True)
    (tmp_path / "runtime" / "python" / "python.exe").write_text("")
    (tmp_path / "runtime" / "node").mkdir(parents=True)
    (tmp_path / "runtime" / "node" / "node.exe").write_text("")
    (tmp_path / "harness" / "dist").mkdir(parents=True)
    (tmp_path / "harness" / "dist" / "server.js").write_text("")
    paths = resolve_runtime_paths(source_root=tmp_path, environ={"DS_HOME": str(tmp_path / "h")})
    monkeypatch.setattr(launcher.os, "name", "nt")  # only path joins below: no new Path() objects
    try:
        assert launcher.backend_python(paths) == str(tmp_path / "runtime" / "python" / "python.exe")
        argv, _cwd = launcher.harness_command(paths)
        assert argv == [str(tmp_path / "runtime" / "node" / "node.exe"), str(tmp_path / "harness" / "dist" / "server.js")]
    finally:
        monkeypatch.undo()


# ---- windows entry script --------------------------------------------------------------------

def _load_pyw():
    spec = importlib.util.spec_from_loader("ds_pyw", loader=None)
    module = importlib.util.module_from_spec(spec)
    source = (REPO / "installer" / "windows" / "director-studio.pyw").read_text(encoding="utf-8")
    module.__file__ = str(REPO / "installer" / "windows" / "director-studio.pyw")
    exec(compile(source, module.__file__, "exec"), module.__dict__)  # noqa: S102 - our own .pyw
    return module


def test_pyw_argument_mapping_and_data_home(tmp_path):
    pyw = _load_pyw()
    assert pyw.launcher_argv([]) == ["run", "--open"]
    assert pyw.launcher_argv(["run", "--no-open", "--port", "9000"]) == ["run", "--port", "9000"]
    assert pyw.launcher_argv(["stop"]) == ["stop"]
    assert pyw.launcher_argv(["status"]) == ["status"]
    assert pyw.data_home({"LOCALAPPDATA": str(tmp_path)}) == tmp_path / "DirectorStudio"
    assert pyw.data_home({"DS_HOME": str(tmp_path / "x"), "LOCALAPPDATA": "C:\\nope"}) == tmp_path / "x"


def test_pyw_redirects_streams_under_pythonw(tmp_path, monkeypatch):
    pyw = _load_pyw()
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    pyw._redirect_missing_streams(tmp_path)
    print("hello from pythonw")
    sys.stdout.flush()
    stream = sys.stdout
    monkeypatch.undo()
    stream.close()
    assert "hello from pythonw" in (tmp_path / "logs" / "launcher.log").read_text()


def test_pyw_refuses_without_marker(tmp_path, monkeypatch, capsys):
    pyw = _load_pyw()
    monkeypatch.setattr(pyw, "ROOT", tmp_path)
    monkeypatch.setenv("DS_HOME", str(tmp_path / "home"))
    assert pyw.main(["status"]) == 1
    assert "ds-install.json" in capsys.readouterr().err


# ---- build_windows ---------------------------------------------------------------------------

def _load_installer_module(name: str):
    sys.path.insert(0, str(REPO / "installer"))
    try:
        spec = importlib.util.spec_from_file_location(f"ds_installer_{name}", REPO / "installer" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(REPO / "installer"))


def test_uv_commands_for_foreign_and_native_hosts(tmp_path, monkeypatch):
    bw = _load_installer_module("build_windows")
    monkeypatch.setattr(bw.common, "uv", lambda: "uv")
    lock = tmp_path / "lock.txt"
    foreign = [str(c) for c in bw.uv_install_command(tmp_path / "py", lock, torch_backend="cu128", native=False)]
    assert "--require-hashes" in foreign and "--only-binary" in foreign
    assert foreign[foreign.index("--python-platform") + 1] == "x86_64-pc-windows-msvc"
    assert foreign[foreign.index("--target") + 1] == str(tmp_path / "py" / "Lib" / "site-packages")
    assert foreign[foreign.index("--torch-backend") + 1] == "cu128"
    native = [str(c) for c in bw.uv_install_command(tmp_path / "py", lock, target=tmp_path / "tools", native=True)]
    assert native[native.index("--python") + 1] == str(tmp_path / "py" / "python.exe")
    assert "--python-platform" not in native and native[native.index("--target") + 1] == str(tmp_path / "tools")


def test_cmd_wrappers_are_crlf_and_use_bundled_python():
    bw = _load_installer_module("build_windows")
    text = bw.CMD_WRAPPER.format(name="comfy-mcp", code=bw.WRAPPERS["comfy-mcp"])
    assert "\n" not in text.replace("\r\n", "")
    assert '"%~dp0..\\python\\python.exe" -s -c' in text and "PYTHONPATH=%~dp0..\\comfy-tools" in text
    assert text.isascii()


def _mini_tree(root: Path, bw) -> None:
    for rel in bw.REQUIRED_FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if "." in Path(rel).name or rel.endswith("server.js"):
            path.write_bytes(b"x\r\n")
        else:
            path.mkdir(exist_ok=True)
    (root / "ds-install.json").write_text(json.dumps({
        "platform": "windows-x64", "defaults": bw.install_defaults("cu128"),
        "components": {"custom_nodes": {"ComfyUI-KJNodes": "x"}}}))
    (root / "runtime" / "ComfyUI" / "custom_nodes" / "ComfyUI-KJNodes").mkdir(parents=True, exist_ok=True)


def test_check_tree_passes_on_complete_tree_and_flags_problems(tmp_path):
    bw = _load_installer_module("build_windows")
    root = tmp_path / "stage"
    _mini_tree(root, bw)
    report = bw.check_tree(root)
    assert report["ok"], report["problems"]
    # now break it in every way the checker knows about
    (root / "runtime" / "llama.cpp" / "llama-server.exe").unlink()
    (root / "runtime" / "python" / "Lib" / "site-packages" / "fastapi" / "_x.cpython-313-x86_64-linux-gnu.so").write_bytes(b"\x7fELF")
    (root / "runtime" / "ffmpeg" / "ffmpeg").write_bytes(b"\x7fELF....")
    (root / "runtime" / "bin" / "comfy.cmd").write_bytes(b"@echo off\nrem lf\n")
    deep = root / "runtime" / "ComfyUI" / ("d" * 120) / ("e" * 80)
    deep.mkdir(parents=True)
    (deep / "f.py").write_text("")
    marker = json.loads((root / "ds-install.json").read_text())
    marker["defaults"]["DS_COMFY_MCP_COMMAND"] = "${INSTALL_ROOT}/runtime/bin/comfy-mcp.cmd"
    marker["components"]["custom_nodes"]["ComfyUI-H3-Motion-Context"] = "y"
    (root / "ds-install.json").write_text(json.dumps(marker))
    problems = " | ".join(bw.check_tree(root)["problems"])
    for needle in ("missing: runtime/llama.cpp/llama-server.exe", "2 non-Windows binaries", "LF line endings",
                   "path too long", "forward slashes", "ComfyUI-H3-Motion-Context"):
        assert needle in problems, (needle, problems)


def test_iscc_command_passes_all_defines(tmp_path):
    bw = _load_installer_module("build_windows")
    cmd = bw.iscc_command("ISCC.exe", tmp_path / "root", tmp_path / "out", "0.2.0", "cu128")
    assert cmd[0] == "ISCC.exe" and cmd[-1].endswith("DirectorStudio.iss")
    defines = {c.split("=", 1)[0][2:]: c.split("=", 1)[1] for c in cmd if c.startswith("/D")}
    assert defines == {"AppVersion": "0.2.0", "AppVersionNumeric": "0.2.0.0", "Variant": "cu128",
                       "SourceDir": str(tmp_path / "root"), "OutputDir": str(tmp_path / "out"),
                       "AppId": bw.MANIFEST["windows"]["app_id"]}


# ---- Inno Setup script lint ------------------------------------------------------------------

def test_inno_setup_script_static_lint():
    raw = (REPO / "installer" / "windows" / "DirectorStudio.iss").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM keeps Inno Setup from reading the file as ANSI"
    text = raw.decode("utf-8-sig")
    assert "\n" not in text.replace("\r\n", ""), "CRLF line endings"
    sections = [line.strip() for line in text.splitlines() if line.startswith("[")]
    for section in ("[Setup]", "[Files]", "[Icons]", "[Run]", "[UninstallRun]", "[UninstallDelete]", "[Code]"):
        assert section in sections
    setup = text.split("[Setup]", 1)[1].split("\r\n[", 1)[0]
    directives = dict(line.split("=", 1) for line in setup.splitlines() if "=" in line and not line.startswith(";"))
    assert directives["PrivilegesRequired"] == "lowest"
    assert directives["DefaultDirName"] == "{localappdata}\\Programs\\DirectorStudio"
    assert directives["DiskSpanning"] == "yes" and directives["ArchitecturesAllowed"] == "x64compatible"
    assert int(directives["DiskSliceSize"]) < 2**31  # GitHub release assets must be < 2 GiB
    assert directives["AppId"] == "{{#AppId}"
    for line in text.splitlines():
        if line.startswith("Type: filesandordirs"):
            assert "{app}" in line and "{localappdata}" not in line, line  # never delete user data
    used = set(__import__("re").findall(r"\{#(\w+)\}", text))
    defined = set(__import__("re").findall(r"#(?:ifndef|define)\s+(\w+)", text))
    assert used <= defined, used - defined
    for name in ("director-studio.ico",):
        assert (REPO / "installer" / "windows" / name).is_file()
    for rel in ("director-studio.pyw", "runtime\\python\\pythonw.exe", "DirectorStudio.cmd", "director-studio.ico"):
        assert rel in text or rel == "director-studio.pyw" and "{#AppExeName}" in text
    bw = _load_installer_module("build_windows")
    assert bw.MANIFEST["windows"]["app_id"] in text  # default AppId matches the manifest


def test_kill_tree_falls_back_when_ctrl_break_is_refused(monkeypatch):
    import signal as _signal

    from app.core.process import managed

    calls = []

    class FakeProc:
        pid = 1

        def children(self, recursive=False):
            return []

        def send_signal(self, sig):
            calls.append(("signal", sig))
            raise OSError(12, "Invalid access")  # what Windows returns without a shared console

        def terminate(self):
            calls.append(("terminate",))

        def wait(self, timeout=None):
            calls.append(("wait",))

        def is_running(self):
            return False

    monkeypatch.setattr(managed.psutil, "Process", lambda pid: FakeProc())
    monkeypatch.setattr(managed.psutil, "wait_procs", lambda procs, timeout=None: ([], []))
    monkeypatch.setattr(managed.os, "name", "nt")
    monkeypatch.setattr(_signal, "CTRL_BREAK_EVENT", 1, raising=False)
    try:
        managed.kill_tree(1, root_first=True)
    finally:
        monkeypatch.undo()
    assert calls[:3] == [("signal", 1), ("terminate",), ("wait",)]


def test_bytecode_precompile_command_and_iss_ships_pycs(tmp_path):
    bw = _load_installer_module("build_windows")
    for rel in ("runtime/python/Lib", "backend/app"):
        (tmp_path / rel).mkdir(parents=True)
    cmd = [str(c) for c in bw.precompile_command(tmp_path, python="py")]
    assert cmd[:3] == ["py", "-m", "compileall"]
    assert cmd[cmd.index("--invalidation-mode") + 1] == "checked-hash"
    assert cmd[-2:] == [str(tmp_path / "runtime/python/Lib"), str(tmp_path / "backend/app")]
    import re

    skip = re.compile(cmd[cmd.index("-x") + 1])
    assert skip.search("C:\\app\\runtime\\comfy-python\\Lib\\site-packages\\numpy\\tests\\test_a.py")
    assert skip.search("/r/site-packages/torch/testing/_x.py")
    assert not skip.search("/r/site-packages/torch/nn/modules/linear.py")
    text = (REPO / "installer" / "windows" / "DirectorStudio.iss").read_bytes().decode("utf-8-sig")
    files_line = next(line for line in text.splitlines() if line.startswith("Source:"))
    assert "pyc" not in files_line and "__pycache__" not in files_line


def test_bytecode_written_by_host_python_is_loadable(tmp_path):
    bw = _load_installer_module("build_windows")
    (tmp_path / "backend" / "app").mkdir(parents=True)
    (tmp_path / "backend" / "app" / "mod.py").write_text("VALUE = 41 + 1\n")
    bw.stage_bytecode(tmp_path)
    pyc = next((tmp_path / "backend" / "app" / "__pycache__").glob("mod.cpython-*.pyc"))
    assert int.from_bytes(pyc.read_bytes()[4:8], "little") == 3  # checked-hash
