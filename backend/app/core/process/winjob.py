"""Windows Job Object containment for the Director Studio launcher.

The launcher puts *itself* into a job created with
``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``.  Every process it starts afterwards
(backend, Harness/Node, and the backend's ComfyUI/Ollama) inherits the job, so
when the launcher exits - cleanly, by crash, by Task Manager "End task", or at
logoff - Windows closes the last job handle and terminates the whole tree.  This
is the Windows counterpart of PDEATHSIG + process groups on Linux.

``JOB_OBJECT_LIMIT_BREAKAWAY_OK`` is also set so that processes that must
outlive DS (the user's web browser opened on first start) can be started with
``CREATE_BREAKAWAY_FROM_JOB``.

Pure ctypes, no pywin32.  ``kernel32`` is injectable so the logic is unit
tested on Linux with a fake.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes
from typing import Any

JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


# Explicit 32-bit DWORD: wintypes.DWORD is c_ulong, which is 8 bytes on LP64 hosts and
# would give the wrong layout when the structures are exercised in tests on Linux.
_DWORD = ctypes.c_uint32


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", _DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", _DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", _DWORD),
        ("SchedulingClass", _DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JobObjectError(OSError):
    pass


def _kernel32() -> Any:  # pragma: no cover - windows only
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    return k32


def _last_error() -> int:
    get = getattr(ctypes, "get_last_error", None)
    return int(get()) if get else 0


class KillOnCloseJob:
    """A job object that kills every member process when its last handle closes."""

    def __init__(self, kernel32: Any | None = None, *, name: str | None = None) -> None:
        self._k32 = kernel32 if kernel32 is not None else _kernel32()
        handle = self._k32.CreateJobObjectW(None, name)
        if not handle:
            raise JobObjectError(_last_error(), "CreateJobObjectW failed")
        self.handle = handle
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK
        ok = self._k32.SetInformationJobObject(handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                                               ctypes.byref(info), ctypes.sizeof(info))
        if not ok:
            error = _last_error()
            self.close()
            raise JobObjectError(error, "SetInformationJobObject failed")

    def assign_current_process(self) -> None:
        if not self._k32.AssignProcessToJobObject(self.handle, self._k32.GetCurrentProcess()):
            raise JobObjectError(_last_error(), "AssignProcessToJobObject failed")

    def close(self) -> None:
        if self.handle:
            self._k32.CloseHandle(self.handle)
            self.handle = None


_CURRENT: KillOnCloseJob | None = None


def contain_current_process(*, kernel32: Any | None = None, platform_name: str | None = None,
                            environ: dict[str, str] | None = None) -> KillOnCloseJob | None:
    """Put this process (and so all future children) into a kill-on-close job.

    Returns the job (keep it alive for the life of the process; it is also kept
    in a module global) or ``None`` when not on Windows, disabled with
    ``DS_NO_JOB_OBJECT=1``, or when Windows refuses (logged by the caller):
    containment is a safety net, never a reason not to start.
    """
    global _CURRENT
    env = os.environ if environ is None else environ
    if (platform_name or os.name) != "nt" or str(env.get("DS_NO_JOB_OBJECT", "")).strip() in {"1", "true", "yes"}:
        return None
    if _CURRENT is not None:
        return _CURRENT
    job = KillOnCloseJob(kernel32)
    try:
        job.assign_current_process()
    except JobObjectError:
        job.close()
        raise
    _CURRENT = job
    return job


def detached_creationflags(platform_name: str | None = None) -> int:
    """Creation flags for a process that must survive DS (the browser)."""
    if (platform_name or os.name) != "nt":
        return 0
    return CREATE_BREAKAWAY_FROM_JOB | CREATE_NO_WINDOW


def open_url_outside_job(url: str, *, popen: Any = subprocess.Popen) -> None:
    """Open ``url`` with the default browser without making the browser part of DS's job.

    ``webbrowser``/``os.startfile`` would start a fresh browser *inside* the
    job, and the user's browser would be killed together with DS.
    """
    argv = ["cmd.exe", "/d", "/c", "start", "", url]
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    try:
        popen(argv, creationflags=detached_creationflags("nt"), **quiet)
    except OSError:
        # An outer job (e.g. a CI runner or terminal) may forbid breakaway; the
        # browser then lives in our job, which is still better than no browser.
        popen(argv, creationflags=CREATE_NO_WINDOW, **quiet)
