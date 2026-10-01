"""
connectors/simconnect_probe.py

Checks that a sim really answers SimConnect before the MSFS connector lets
the Python-SimConnect library connect.

Why: Python-SimConnect opens SimConnect and then waits - forever, with no
timeout - for the sim's "open" reply. A sim that rejects it (P3D answers
MSFS's SimConnect with SIMCONNECT_EXCEPTION_VERSION_MISMATCH) or never
replies would freeze the whole app. This does the same handshake itself,
straight through SimConnect.dll with a time limit, in a background thread,
and only reports "open" when the sim actually said hello.

Usage (from MSFSConnector.connect(), once per poll):
    probe = SimConnectProbe()
    if not probe.check():          # never blocks
        raise ConnectionError(probe.status)
    # the sim answers - safe to create SimConnect() now

probe.last_result is the latest finished check: "open", "not_running",
"version_mismatch", "no_answer", "quit" or "error: ..." (None before the
first one). After a rejection or no answer, the next check waits
RETRY_AFTER_S, so a sim that won't talk isn't hammered every second.
"""

import ctypes
import importlib.util
import os
import threading
import time
from ctypes import POINTER, Structure, byref, c_char_p, c_uint32, c_void_p, c_long

PROBE_TIMEOUT_S = 3.0
POLL_S = 0.05
RETRY_AFTER_S = 30.0

RECV_ID_EXCEPTION = 1
RECV_ID_OPEN = 2
RECV_ID_QUIT = 3
EXCEPTION_VERSION_MISMATCH = 5


class _Recv(Structure):
    _fields_ = [("dwSize", c_uint32), ("dwVersion", c_uint32), ("dwID", c_uint32)]


class _RecvException(Structure):
    _fields_ = [("dwSize", c_uint32), ("dwVersion", c_uint32), ("dwID", c_uint32),
                ("dwException", c_uint32), ("dwSendID", c_uint32), ("dwIndex", c_uint32)]


def simconnect_dll_path():
    """The SimConnect.dll the Python-SimConnect package uses, or None."""
    spec = importlib.util.find_spec("SimConnect")
    if spec is None or not spec.submodule_search_locations:
        return None
    path = os.path.join(list(spec.submodule_search_locations)[0], "SimConnect.dll")
    return path if os.path.exists(path) else None


def _load(dll_path):
    dll = ctypes.WinDLL(dll_path)
    dll.SimConnect_Open.restype = c_long
    dll.SimConnect_Open.argtypes = [POINTER(c_void_p), c_char_p, c_void_p, c_uint32, c_void_p, c_uint32]
    dll.SimConnect_GetNextDispatch.restype = c_long
    dll.SimConnect_GetNextDispatch.argtypes = [c_void_p, POINTER(POINTER(_Recv)), POINTER(c_uint32)]
    dll.SimConnect_Close.restype = c_long
    dll.SimConnect_Close.argtypes = [c_void_p]
    return dll


def handshake(dll, timeout=PROBE_TIMEOUT_S):
    """Opens SimConnect through `dll`, waits up to `timeout` for the sim's
    reply, closes again. Returns "open", "not_running", "version_mismatch",
    "no_answer", "quit" or "exception <n>"."""
    handle = c_void_p()
    if dll.SimConnect_Open(byref(handle), b"Flyt connection check", None, 0, None, 0) < 0:
        return "not_running"
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message, size = POINTER(_Recv)(), c_uint32()
            if dll.SimConnect_GetNextDispatch(handle, byref(message), byref(size)) >= 0 and message:
                kind = message.contents.dwID
                if kind == RECV_ID_OPEN:
                    return "open"
                if kind == RECV_ID_QUIT:
                    return "quit"
                if kind == RECV_ID_EXCEPTION:
                    code = ctypes.cast(message, POINTER(_RecvException)).contents.dwException
                    return "version_mismatch" if code == EXCEPTION_VERSION_MISMATCH else f"exception {code}"
                continue
            time.sleep(POLL_S)
        return "no_answer"
    finally:
        dll.SimConnect_Close(handle)


_MESSAGES = {
    "version_mismatch": "A sim answered SimConnect but rejected this SimConnect version "
                        "(P3D does this) - not using SimConnect.",
    "no_answer": "SimConnect opened but the sim never replied - not using SimConnect.",
    "quit": "The sim closed SimConnect straight away.",
}


class SimConnectProbe:
    """Runs handshake() in a background thread; see the module docstring."""

    def __init__(self, dll_path=None, loader=_load):
        self._dll_path = dll_path
        self._loader = loader
        self._dll = None
        self._thread = None
        self._pending = None
        self._retry_at = 0.0
        self._reported = None
        self.last_result = None
        self.status = "Checking SimConnect..."

    def check(self):
        """True if the latest check says the sim answers. Otherwise starts a
        check (or waits for one) and returns False - never blocks."""
        if self._thread is not None and self._thread.is_alive():
            return False
        if self._pending is not None:
            result, self._pending = self._pending, None
            self._thread = None
            self.last_result = result
            if result == "open":
                return True
            self.status = _MESSAGES.get(result, f"SimConnect not available ({result}).")
            if result != "not_running":
                self._retry_at = time.monotonic() + RETRY_AFTER_S
                if result != self._reported:
                    print(f"[simconnect] {self.status}")
                    self._reported = result
            return False
        if time.monotonic() < self._retry_at:
            return False
        self._thread = threading.Thread(target=self._run, name="SimConnectProbe", daemon=True)
        self._thread.start()
        return False

    def _run(self):
        try:
            if self._dll is None:
                path = self._dll_path or simconnect_dll_path()
                if path is None:
                    self._pending = "error: SimConnect.dll not found"
                    return
                self._dll = self._loader(path)
            self._pending = handshake(self._dll)
        except Exception as e:
            self._pending = f"error: {e}"