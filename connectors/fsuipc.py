"""
connectors/fsuipc.py

MSFS / P3D / FSX connector through FSUIPC: FSUIPC7 for MSFS 2020 / 2024
(a separate program next to the sim), FSUIPC4/5/6 inside P3D / FSX. Same
job and interface as connectors/xplane.py: return the normalized
telemetry dict in the same shape and units, so core/flight_state.py can't
tell the difference. See connectors/auto.py.

No extra packages and no DLL: FSUIPC's IPC is plain Windows messaging and
shared memory, done here with ctypes, following FSUIPC's own 64-bit client
(IPCuser64.c):
  - FSUIPC's window is "UIPCMAIN" (WideClient's is "FS98MAIN")
  - requests go into a shared-memory block named "FsasmLib:IPC:<pid>:<n>",
    whose name is passed in a global atom with the registered window
    message "FsasmLib:IPC"; FSUIPC fills in the answers in place
  - a 64-bit read request is a packed 20-byte header (id 4, offset,
    size, 8-byte client pointer) followed by room for the data; a write
    is a 12-byte header (id 2, offset, size) followed by the data; a zero
    DWORD ends the list
The free, unregistered FSUIPC is enough for this.

FSUIPC can answer while there's no flight to read: FSUIPC7 runs on its own
and can be up before MSFS (or still sitting in the menus). Until the sim
is ready to fly (offset 0x3364 is zero and there's a position), read()
returns None - like a connector that has no data yet. The sim's name is
re-read on every read(), since FSUIPC7 says MSFS 2020 until it has
connected to MSFS 2024.

MSFS gives ATC type and model as localization keys
("ATCCOM.AC_MODEL A320.0.text"), cut off at FSUIPC's 24 bytes; only the
name part is kept.

Usage:
    connector = FSUIPCConnector()
    connector.connect()       # raises ConnectionError if FSUIPC isn't there
    data = connector.read()   # -> telemetry dict, or None while not ready
    connector.disconnect()

Touchdowns: read() is only called about once a second, so a background
thread reads on-ground, vertical speed, G, attitude and speeds 20x a
second and hands each touchdown over in data["touchdowns"] as {"vs", "g",
"bank", "pitch", "ias", "gs"}; one only counts after MIN_AIRBORNE_S in
the air, and G is the peak over the following half second.

Units match the other connectors: ft, kt, fpm, lbs, degrees. Heading is
magnetic. Flaps and gear are FSUIPC's 0-16383 handle positions turned
into 0-1 (flaps, like X-Plane) and 0/1 (gear).

Run this file with the sim and FSUIPC running to check the values
against the cockpit:
    python -m connectors.fsuipc
"""

import ctypes
import re
import struct
import sys
import threading
import time

READ_ID = 4
WRITE_ID = 2
READ_HEADER = struct.Struct("<IIIQ")
WRITE_HEADER = struct.Struct("<III")
MAX_SIZE = 0x7F00
MESSAGE_SUCCESS = 1
SEND_TIMEOUT_MS = 2000
LIB_VERSION = 2002

SAMPLE_INTERVAL_S = 0.05
TOUCHDOWN_G_SAMPLES = 10
MIN_AIRBORNE_S = 1.0

M_TO_FT = 3.28084
MS_TO_KT = 1.943844
ANGLE = 360.0 / (65536.0 * 65536.0)

OFFSETS = {
    "latitude":          (0x0560, 8, "q"),
    "longitude":         (0x0568, 8, "q"),
    "altitude":          (0x0570, 8, "q"),
    "pitch":             (0x0578, 4, "i"),
    "bank":              (0x057C, 4, "i"),
    "heading":           (0x0580, 4, "I"),
    "magvar":            (0x02A0, 2, "h"),
    "ground_alt":        (0x0020, 4, "i"),
    "ias":               (0x02BC, 4, "i"),
    "gs":                (0x02B4, 4, "i"),
    "vs":                (0x02C8, 4, "i"),
    "on_ground":         (0x0366, 2, "H"),
    "g_force":           (0x11BA, 2, "h"),
    "eng1_combustion":   (0x0894, 2, "H"),
    "eng2_combustion":   (0x092C, 2, "H"),
    "fuel_lbs":          (0x126C, 4, "i"),
    "total_weight":      (0x30C0, 8, "d"),
    "gear_handle":       (0x0BE8, 4, "I"),
    "flaps_handle":      (0x0BDC, 4, "I"),
    "parking_brake":     (0x0BC8, 2, "H"),
    "title":             (0x3D00, 256, "s"),
    "atc_id":            (0x313C, 12, "s"),
    "atc_type":          (0x3160, 24, "s"),
    "atc_model":         (0x3500, 24, "s"),
    "not_ready":         (0x3364, 1, "B"),
    "fs_version":        (0x3308, 2, "H"),
}
FAST_KEYS = ("on_ground", "vs", "g_force", "pitch", "bank", "ias", "gs")


def _decode(fmt, raw):
    if fmt == "s":
        return raw.split(b"\x00", 1)[0].decode("latin-1").strip() or None
    return struct.unpack("<" + fmt, raw)[0]


_ATCCOM_PATTERN = re.compile(r"ATCCOM\.(?:ATC_NAME|AC_MODEL)[ _]*([^.]*)")


def _sanitize_atccom(raw):
    """MSFS's "ATCCOM.AC_MODEL A320.0.text" (possibly cut short) -> "A320".
    Anything else is returned as it is."""
    if raw is None:
        return None
    match = _ATCCOM_PATTERN.match(raw)
    if match:
        return match.group(1).strip() or None
    return raw


def build_request(reads, writes=()):
    """[(offset, size)], [(offset, bytes)] -> the request block bytes."""
    parts = []
    for offset, data in writes:
        parts.append(WRITE_HEADER.pack(WRITE_ID, offset, len(data)) + bytes(data))
    for offset, size in reads:
        parts.append(READ_HEADER.pack(READ_ID, offset, size, 0) + bytes(size))
    block = b"".join(parts) + b"\x00\x00\x00\x00"
    if len(block) > MAX_SIZE:
        raise ValueError("FSUIPC request too large")
    return block


def parse_reply(block, count):
    """The request block after FSUIPC filled it in -> the `count` read
    results, in order, as bytes."""
    results, pos = [], 0
    while len(results) < count and pos + 4 <= len(block):
        kind = struct.unpack_from("<I", block, pos)[0]
        if kind == READ_ID:
            _, _, size, _ = READ_HEADER.unpack_from(block, pos)
            start = pos + READ_HEADER.size
            results.append(bytes(block[start:start + size]))
            pos = start + size
        elif kind == WRITE_ID:
            _, _, size = WRITE_HEADER.unpack_from(block, pos)
            pos += WRITE_HEADER.size + size
        else:
            break
    if len(results) != count:
        raise ConnectionError("FSUIPC sent back an unexpected reply.")
    return results


class _WindowsTransport:
    """The shared memory + window message plumbing (Windows only)."""
    _next_try = 0

    def __init__(self):
        from ctypes import wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        u, k = self.user32, self.kernel32
        u.FindWindowExA.restype = wintypes.HWND
        u.FindWindowExA.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCSTR, wintypes.LPCSTR]
        u.RegisterWindowMessageA.restype = wintypes.UINT
        u.RegisterWindowMessageA.argtypes = [wintypes.LPCSTR]
        u.SendMessageTimeoutA.restype = ctypes.c_ssize_t
        u.SendMessageTimeoutA.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                          wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        k.GlobalAddAtomA.restype = wintypes.ATOM
        k.GlobalAddAtomA.argtypes = [wintypes.LPCSTR]
        k.GlobalDeleteAtom.argtypes = [wintypes.ATOM]
        k.CreateFileMappingA.restype = wintypes.HANDLE
        k.CreateFileMappingA.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                         wintypes.DWORD, wintypes.DWORD, wintypes.LPCSTR]
        k.MapViewOfFile.restype = ctypes.c_void_p
        k.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
        k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        self.hwnd = self.atom = self.hmap = self.view = None

    def open(self):
        u, k = self.user32, self.kernel32
        self.hwnd = u.FindWindowExA(None, None, b"UIPCMAIN", None) or u.FindWindowExA(None, None, b"FS98MAIN", None)
        if not self.hwnd:
            raise ConnectionError("FSUIPC isn't running.")
        self.msg = u.RegisterWindowMessageA(b"FsasmLib:IPC")
        _WindowsTransport._next_try += 1
        name = f"FsasmLib:IPC:{k.GetCurrentProcessId():X}:{_WindowsTransport._next_try:X}".encode("ascii")
        self.atom = k.GlobalAddAtomA(name)
        self.hmap = k.CreateFileMappingA(ctypes.c_void_p(-1), None, 0x04, 0, MAX_SIZE + 256, name)
        self.view = k.MapViewOfFile(self.hmap, 0x0002, 0, 0, 0) if self.hmap else None
        if not (self.msg and self.atom and self.hmap and self.view):
            self.close()
            raise ConnectionError("Couldn't set up the link to FSUIPC.")

    def exchange(self, block):
        ctypes.memmove(self.view, block, len(block))
        result = ctypes.c_size_t(0)
        for _ in range(3):
            if self.user32.SendMessageTimeoutA(self.hwnd, self.msg, self.atom, 0, 0x0001,
                                               SEND_TIMEOUT_MS, ctypes.byref(result)):
                break
            time.sleep(0.1)
        else:
            raise ConnectionError("FSUIPC stopped answering.")
        if result.value != MESSAGE_SUCCESS:
            raise ConnectionError("FSUIPC rejected the request.")
        return ctypes.string_at(self.view, len(block))

    def close(self):
        k = self.kernel32
        if self.view:
            k.UnmapViewOfFile(self.view)
        if self.hmap:
            k.CloseHandle(self.hmap)
        if self.atom:
            k.GlobalDeleteAtom(self.atom)
        self.hwnd = self.atom = self.hmap = self.view = None


class FSUIPCLink:
    """Thread-safe read access to FSUIPC offsets."""

    def __init__(self, transport=None):
        self._transport = transport
        self._lock = threading.Lock()
        self.fs_version = None

    def open(self):
        if self._transport is None:
            if sys.platform != "win32":
                raise ConnectionError("FSUIPC is Windows only.")
            self._transport = _WindowsTransport()
        self._transport.open()
        try:
            for attempt in range(5):
                raw = self._exchange([(0x3304, 4), (0x3308, 4)],
                                     writes=[(0x330A, struct.pack("<H", LIB_VERSION))] if attempt == 0 else ())
                fsuipc_version, fs_version = (struct.unpack("<I", r)[0] for r in raw)
                if fsuipc_version and fs_version:
                    break
                time.sleep(0.1)
            if (fs_version & 0xFFFF0000) != 0xFADE0000:
                raise ConnectionError("The program answering isn't FSUIPC.")
            self.fs_version = fs_version & 0xFFFF
        except Exception:
            self.close()
            raise

    def _exchange(self, reads, writes=()):
        with self._lock:
            return parse_reply(self._transport.exchange(build_request(reads, writes)), len(reads))

    def read(self, keys):
        """{key: decoded value} for keys of OFFSETS."""
        raw = self._exchange([OFFSETS[k][:2] for k in keys])
        return {k: _decode(OFFSETS[k][2], r) for k, r in zip(keys, raw)}

    def close(self):
        if self._transport is not None:
            self._transport.close()


def _attitude(values):
    """FSUIPC pitch/bank -> (bank either way, pitch nose-up) in degrees.
    FSUIPC's pitch is negative nose-up."""
    bank = abs(values["bank"] * ANGLE) if values.get("bank") is not None else None
    pitch = -values["pitch"] * ANGLE if values.get("pitch") is not None else None
    return bank, pitch


def _fpm(raw):
    return raw * 60.0 * M_TO_FT / 256.0


class _TouchdownSampler(threading.Thread):
    """Catches touchdowns between read()s - see the module docstring."""

    def __init__(self, link):
        super().__init__(name="FSUIPCTouchdownSampler", daemon=True)
        self._link = link
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._touchdowns = []
        self._open = None
        self._open_samples_left = 0
        self._was_on_ground = None
        self._airborne_since = None
        self._last_airborne_vs = None
        self._error_reported = False

    def run(self):
        while not self._stop_event.is_set():
            try:
                self._sample()
            except Exception as e:
                if not self._error_reported:
                    print(f"[fsuipc] Touchdown sampler error (falling back to 1 s readings): {e}")
                    self._error_reported = True
            self._stop_event.wait(SAMPLE_INTERVAL_S)

    def _sample(self):
        v = self._link.read(FAST_KEYS)
        on_ground = bool(v["on_ground"])
        vs = _fpm(v["vs"])
        g = v["g_force"] / 624.0

        if self._open is not None:
            if self._open["g"] is None or g > self._open["g"]:
                self._open["g"] = g
            self._open_samples_left -= 1
            if self._open_samples_left <= 0:
                self._close_touchdown()

        now = time.monotonic()
        if not on_ground:
            if self._was_on_ground is not False:
                self._airborne_since = now
            self._last_airborne_vs = vs
        elif (self._was_on_ground is False and self._last_airborne_vs is not None
              and self._airborne_since is not None and now - self._airborne_since >= MIN_AIRBORNE_S):
            if self._open is not None:
                self._close_touchdown()
            bank, pitch = _attitude(v)
            self._open = {"vs": min(self._last_airborne_vs, vs), "g": g, "bank": bank, "pitch": pitch,
                          "ias": max(0.0, v["ias"] / 128.0), "gs": v["gs"] / 65536.0 * MS_TO_KT}
            self._open_samples_left = TOUCHDOWN_G_SAMPLES
        self._was_on_ground = on_ground

    def _close_touchdown(self):
        with self._lock:
            self._touchdowns.append(self._open)
        self._open = None

    def take(self):
        with self._lock:
            touchdowns, self._touchdowns = self._touchdowns, []
        return touchdowns

    def stop(self):
        self._stop_event.set()


FS_NAMES = {8: "FSX", 9: "ESP", 10: "P3D", 11: "FSX-SE", 12: "P3D", 13: "MSFS 2020", 14: "MSFS 2024"}


def normalize(v):
    """Raw FSUIPC values (FSUIPCLink.read of all OFFSETS) -> the telemetry
    dict the other connectors return."""
    altitude_ft = v["altitude"] / (65536.0 * 65536.0) * M_TO_FT
    ground_ft = v["ground_alt"] / 256.0 * M_TO_FT
    heading = (v["heading"] * ANGLE - v["magvar"] * 360.0 / 65536.0) % 360
    bank, pitch = _attitude(v)
    return {
        "on_ground": 1.0 if v["on_ground"] else 0.0,
        "eng1_combustion": bool(v["eng1_combustion"]),
        "eng2_combustion": bool(v["eng2_combustion"]),
        "engine_running": bool(v["eng1_combustion"]) or bool(v["eng2_combustion"]),
        "altitude": altitude_ft,
        "alt_above_ground": altitude_ft - ground_ft,
        "airspeed_indicated": max(0.0, v["ias"] / 128.0),
        "ground_velocity": v["gs"] / 65536.0 * MS_TO_KT,
        "vertical_speed": _fpm(v["vs"]),
        "g_force": v["g_force"] / 624.0,
        "heading_true": heading,
        "bank": bank,
        "pitch": pitch,
        "latitude": v["latitude"] * 90.0 / (10001750.0 * 65536.0 * 65536.0),
        "longitude": v["longitude"] * 360.0 / (65536.0 ** 4),
        "fuel_total_weight": float(v["fuel_lbs"]),
        "total_weight": v["total_weight"],
        "title": v["title"],
        "atc_type": _sanitize_atccom(v["atc_type"]),
        "atc_model": _sanitize_atccom(v["atc_model"]),
        "atc_id": v["atc_id"],
        "gear_handle_position": 1 if v["gear_handle"] >= 8192 else 0,
        "flaps_handle_index": round(v["flaps_handle"] / 16383.0, 2),
        "parking_brake": 1 if v["parking_brake"] else 0,
        "design_speed_vle": None,
    }


def is_ready(v):
    """True once the sim has a flight loaded: not in the menus or loading,
    and somewhere other than exactly 0, 0."""
    return not v["not_ready"] and (v["latitude"] != 0 or v["longitude"] != 0)


class FSUIPCConnector:
    SIM_NAME = "FSUIPC"

    def __init__(self, transport=None):
        self._transport = transport
        self._link = None
        self._sampler = None

    def connect(self):
        """Links to FSUIPC. Raises ConnectionError if it isn't running."""
        link = FSUIPCLink(self._transport)
        link.open()
        self._link = link
        self._set_name(link.fs_version)
        self._sampler = _TouchdownSampler(link)
        self._sampler.start()

    def disconnect(self):
        if self._sampler:
            self._sampler.stop()
            self._sampler.join(timeout=1.0)
            self._sampler = None
        if self._link:
            self._link.close()
            self._link = None

    def is_connected(self):
        return self._link is not None

    def _set_name(self, fs_version):
        name = FS_NAMES.get(fs_version)
        self.SIM_NAME = f"{name} (FSUIPC)" if name else "FSUIPC"

    def read(self):
        """The telemetry dict, or None while the sim isn't ready to fly.
        Raises ConnectionError if FSUIPC went away."""
        if self._link is None:
            raise ConnectionError("Not connected to FSUIPC.")
        values = self._link.read(tuple(OFFSETS))
        self._set_name(values["fs_version"])
        if not is_ready(values):
            return None
        data = normalize(values)
        data["touchdowns"] = self._sampler.take() if self._sampler else []
        return data


if __name__ == "__main__":
    connector = FSUIPCConnector()
    print("Looking for FSUIPC... (Ctrl+C to stop)")
    try:
        while True:
            try:
                if not connector.is_connected():
                    connector.connect()
                    print(f"  Connected: {connector.SIM_NAME}")
                d = connector.read()
                if d is None:
                    print(f"  [{connector.SIM_NAME}] connected, waiting for the sim to be ready to fly")
                    time.sleep(1)
                    continue
                print(f"  [{connector.SIM_NAME}] {d['title']!r} {d['atc_id']}  lat={d['latitude']:.4f} lon={d['longitude']:.4f}  "
                      f"alt={d['altitude']:.0f} ft (AGL {d['alt_above_ground']:.0f})  hdg={d['heading_true']:.0f}  "
                      f"IAS={d['airspeed_indicated']:.0f} GS={d['ground_velocity']:.0f} kt  "
                      f"VS={d['vertical_speed']:.0f} fpm  G={d['g_force']:.2f}  "
                      f"pitch={d['pitch']:.1f} bank={d['bank']:.1f}  ground={d['on_ground']:.0f}  "
                      f"engine={d['engine_running']}  fuel={d['fuel_total_weight']:.0f} lb  "
                      f"gear={d['gear_handle_position']} flaps={d['flaps_handle_index']} brake={d['parking_brake']}"
                      f"  touchdowns={d['touchdowns']}"
                      f"\n    type={d['atc_type']!r} model={d['atc_model']!r}")
            except ConnectionError as e:
                print(" ", e)
                connector.disconnect()
            time.sleep(1)
    except KeyboardInterrupt:
        connector.disconnect()