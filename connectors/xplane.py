"""
X-Plane connector (X-Plane 11 and 12, UDP "RREF" subscriptions).

Same job and interface as connectors/msfs.py: talk to the sim and return a
normalized dict of current sim state in the same shape and units, so
core/flight_state.py can't tell the two apart. No plugin and no X-Plane
settings needed - X-Plane listens for these requests on UDP port 49000 out
of the box.

How it works: the connector sends X-Plane a subscription for each dataref
it wants and how often; X-Plane then streams the values back to our socket
until told to stop. A background thread receives them and keeps the latest
of each. Text values (aircraft name, ICAO type, tail number) are byte
arrays, subscribed one character at a time.

Usage:
    connector = XPlaneConnector()
    connector.connect()       # raises ConnectionError until X-Plane answers
    data = connector.read()   # -> dict, or None if not ready yet
    connector.disconnect()

connect() never waits: it (re)sends the subscriptions every RESUBSCRIBE_S
and raises until X-Plane has answered, so it can be retried every poll
without freezing anything - and X-Plane can be started after the app.
read() raises ConnectionError once nothing has arrived for STALE_S
(X-Plane closed), so the caller can go back to searching.

Touchdown rate: on-ground and vertical speed stream at FAST_HZ (20x a
second). Every airborne -> on-ground change is a touchdown, recorded as
the more negative of the last airborne and first on-ground vertical speed
(same rule as the MSFS sampler) and handed over in data["touchdowns"].

Units match the MSFS connector: ft, kt, fpm, lbs, degrees. Heading is
magnetic, like MSFS. Flaps are X-Plane's 0-1 handle ratio (MSFS gives a
detent number) - the tracker only looks for changes, so either works.
"""

import socket
import struct
import threading
import time

XPLANE_ADDRESS = ("127.0.0.1", 49000)
RESUBSCRIBE_S = 3.0
STALE_S = 3.0
FAST_HZ = 20
NORMAL_HZ = 5
TEXT_HZ = 1
TOUCHDOWN_G_WINDOW_S = 0.5
MIN_AIRBORNE_S = 1.0

M_TO_FT = 3.28084
MS_TO_KT = 1.943844
KG_TO_LB = 2.2046226

NUMERIC = {
    "on_ground":            ("sim/flightmodel/failures/onground_any", FAST_HZ, None),
    "vertical_speed":       ("sim/flightmodel/position/vh_ind_fpm", FAST_HZ, None),
    "eng1_combustion":      ("sim/flightmodel/engine/ENGN_running[0]", NORMAL_HZ, None),
    "eng2_combustion":      ("sim/flightmodel/engine/ENGN_running[1]", NORMAL_HZ, None),
    "altitude":             ("sim/flightmodel/position/elevation", NORMAL_HZ, M_TO_FT),
    "alt_above_ground":     ("sim/flightmodel/position/y_agl", NORMAL_HZ, M_TO_FT),
    "airspeed_indicated":   ("sim/flightmodel/position/indicated_airspeed", NORMAL_HZ, None),
    "ground_velocity":      ("sim/flightmodel/position/groundspeed", NORMAL_HZ, MS_TO_KT),
    "g_force":              ("sim/flightmodel/forces/g_nrml", FAST_HZ, None),
    "bank":                 ("sim/flightmodel/position/phi", FAST_HZ, None),
    "pitch":                ("sim/flightmodel/position/theta", FAST_HZ, None),
    "heading_true":         ("sim/flightmodel/position/mag_psi", NORMAL_HZ, None),
    "latitude":             ("sim/flightmodel/position/latitude", NORMAL_HZ, None),
    "longitude":            ("sim/flightmodel/position/longitude", NORMAL_HZ, None),
    "fuel_total_weight":    ("sim/flightmodel/weight/m_fuel_total", NORMAL_HZ, KG_TO_LB),
    "total_weight":         ("sim/flightmodel/weight/m_total", NORMAL_HZ, KG_TO_LB),
    "gear_handle_position": ("sim/cockpit2/controls/gear_handle_down", NORMAL_HZ, None),
    "flaps_handle_index":   ("sim/cockpit2/controls/flap_ratio", NORMAL_HZ, None),
    "parking_brake":        ("sim/cockpit2/controls/parking_brake_ratio", NORMAL_HZ, None),
    "design_speed_vle":     ("sim/aircraft/view/acf_Vle", TEXT_HZ, None),
}

TEXT = {
    "title":        ("sim/aircraft/view/acf_ui_name", 48),
    "title_alt":    ("sim/aircraft/view/acf_descrip", 48),
    "atc_model":    ("sim/aircraft/view/acf_ICAO", 8),
    "atc_id":       ("sim/aircraft/view/acf_tailnum", 12),
}


def _subscription_list():
    """[(index, dataref, rate, key, char_position or None)] - one entry per
    X-Plane subscription, numbered from 0."""
    subs = []
    for key, (dataref, rate, _) in NUMERIC.items():
        subs.append((len(subs), dataref, rate, key, None))
    for key, (dataref, length) in TEXT.items():
        for pos in range(length):
            subs.append((len(subs), f"{dataref}[{pos}]", TEXT_HZ, key, pos))
    return subs


def rref_request(rate, index, dataref):
    """One X-Plane RREF subscription message (rate 0 = unsubscribe)."""
    return b"RREF\x00" + struct.pack("<ii400s", rate, index, dataref.encode("ascii"))


def parse_rref(packet):
    """An X-Plane RREF reply -> [(index, value), ...] ([] if it isn't one)."""
    if len(packet) < 5 or packet[:4] != b"RREF":
        return []
    body = packet[5:]
    return [struct.unpack_from("<if", body, offset) for offset in range(0, len(body) - 7, 8)]


def _text(chars):
    """Characters (as floats, 0 = end) -> string."""
    out = []
    for value in chars:
        code = int(value)
        if code == 0:
            break
        if 32 <= code < 127:
            out.append(chr(code))
    return "".join(out).strip()


class XPlaneConnector:
    SIM_NAME = "X-Plane"

    def __init__(self, address=XPLANE_ADDRESS):
        self.address = address
        self._subs = _subscription_list()
        self._by_index = {s[0]: s for s in self._subs}
        self._lock = threading.Lock()
        self._sock = None
        self._thread = None
        self._running = False
        self._last_subscribe = 0.0
        self._reset_values()

    def _reset_values(self):
        self._values = {}
        self._chars = {key: [0.0] * length for key, (_, length) in TEXT.items()}
        self._last_packet = None
        self._touchdowns = []
        self._open = None
        self._open_since = None
        self._airborne_since = None
        self._was_on_ground = None
        self._last_airborne_vs = None

    def connect(self):
        """Makes sure we're listening and subscribed. Raises ConnectionError
        until X-Plane has actually sent data - never waits for it."""
        if not self._running:
            self._start()
        if time.monotonic() - self._last_subscribe >= RESUBSCRIBE_S and not self._receiving():
            self._subscribe(on=True)
        if not self._receiving():
            raise ConnectionError("X-Plane isn't answering on UDP port 49000.")

    def disconnect(self):
        if self._sock is not None and self._receiving():
            try:
                self._subscribe(on=False)
            except OSError:
                pass
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        self._last_subscribe = 0.0
        with self._lock:
            self._reset_values()

    def is_connected(self):
        return self._receiving()

    def read(self):
        """The latest values in the MSFS connector's dict shape, or None if
        the core values haven't arrived yet. Raises ConnectionError once
        X-Plane has gone quiet for STALE_S."""
        if not self._receiving():
            raise ConnectionError("Lost contact with X-Plane.")

        with self._lock:
            values = dict(self._values)
            chars = {key: list(c) for key, c in self._chars.items()}
            touchdowns, self._touchdowns = self._touchdowns, []

        if values.get("on_ground") is None or values.get("eng1_combustion") is None:
            return None

        raw = {}
        for key, (_, _, factor) in NUMERIC.items():
            value = values.get(key)
            raw[key] = value * factor if value is not None and factor else value

        raw["on_ground"] = 1.0 if raw["on_ground"] >= 0.5 else 0.0
        if raw["bank"] is not None:
            raw["bank"] = abs(raw["bank"])
        if raw["airspeed_indicated"] is not None:
            raw["airspeed_indicated"] = max(0.0, raw["airspeed_indicated"])
        raw["engine_running"] = bool(raw["eng1_combustion"]) or bool(raw.get("eng2_combustion"))
        if raw["gear_handle_position"] is not None:
            raw["gear_handle_position"] = int(round(raw["gear_handle_position"]))
        if raw["flaps_handle_index"] is not None:
            raw["flaps_handle_index"] = round(raw["flaps_handle_index"], 2)
        if raw["parking_brake"] is not None:
            raw["parking_brake"] = 1 if raw["parking_brake"] >= 0.5 else 0

        raw["title"] = _text(chars["title"]) or _text(chars["title_alt"]) or None
        raw["atc_model"] = _text(chars["atc_model"]) or None
        raw["atc_type"] = raw["atc_model"]
        raw["atc_id"] = _text(chars["atc_id"]) or None
        raw["touchdowns"] = touchdowns
        return raw

    def _receiving(self):
        return self._last_packet is not None and time.monotonic() - self._last_packet < STALE_S

    def _start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.settimeout(0.5)
        self._running = True
        self._thread = threading.Thread(target=self._listen, name="XPlaneListener", daemon=True)
        self._thread.start()

    def _subscribe(self, on):
        for index, dataref, rate, _, _ in self._subs:
            self._sock.sendto(rref_request(rate if on else 0, index, dataref), self.address)
        self._last_subscribe = time.monotonic()

    def _listen(self):
        while self._running:
            try:
                packet, _ = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                if not self._running:
                    break
                time.sleep(0.2)
                continue
            pairs = parse_rref(packet)
            if pairs:
                self._store(pairs)

    def _store(self, pairs):
        with self._lock:
            for index, value in pairs:
                sub = self._by_index.get(index)
                if sub is None:
                    continue
                _, _, _, key, pos = sub
                if pos is None:
                    self._values[key] = value
                else:
                    self._chars[key][pos] = value
            self._last_packet = time.monotonic()
            self._check_touchdown()

    def _check_touchdown(self):
        """Called with the lock held after each packet."""
        if self._open is not None:
            self._track_touchdown_g()

        on_ground = self._values.get("on_ground")
        vs = self._values.get("vertical_speed")
        if on_ground is None:
            return
        on_ground = on_ground >= 0.5
        now = time.monotonic()
        if not on_ground:
            if self._was_on_ground is not False:
                self._airborne_since = now
            if vs is not None:
                self._last_airborne_vs = vs
        elif (self._was_on_ground is False and self._last_airborne_vs is not None
              and self._airborne_since is not None and now - self._airborne_since >= MIN_AIRBORNE_S):
            readings = [self._last_airborne_vs] + ([vs] if vs is not None else [])
            self._open_touchdown(min(readings))
        self._was_on_ground = on_ground

    def _open_touchdown(self, vs):
        """A touchdown just happened: note the moment's attitude and speeds,
        then keep watching G for TOUCHDOWN_G_WINDOW_S before handing it over."""
        if self._open is not None:
            self._close_touchdown()
        values = self._values
        bank = values.get("bank")
        ias = values.get("airspeed_indicated")
        gs = values.get("ground_velocity")
        self._open = {
            "vs": vs,
            "g": values.get("g_force"),
            "bank": abs(bank) if bank is not None else None,
            "pitch": values.get("pitch"),
            "ias": max(0.0, ias) if ias is not None else None,
            "gs": gs * MS_TO_KT if gs is not None else None,
        }
        self._open_since = time.monotonic()

    def _track_touchdown_g(self):
        g = self._values.get("g_force")
        if g is not None and (self._open["g"] is None or g > self._open["g"]):
            self._open["g"] = g
        if time.monotonic() - self._open_since >= TOUCHDOWN_G_WINDOW_S:
            self._close_touchdown()

    def _close_touchdown(self):
        self._touchdowns.append(self._open)
        self._open = None
        self._open_since = None


if __name__ == "__main__":
    connector = XPlaneConnector()
    print("Waiting for X-Plane on", XPLANE_ADDRESS, "... (Ctrl+C to stop)")
    try:
        while True:
            try:
                connector.connect()
                data = connector.read()
            except ConnectionError as e:
                print(" ", e)
                time.sleep(1)
                continue
            if data:
                print(f"  {data['title']!r} {data['atc_model']} {data['atc_id']}  "
                      f"lat={data['latitude']:.4f} lon={data['longitude']:.4f}  "
                      f"alt={data['altitude']:.0f} ft  IAS={data['airspeed_indicated']:.0f} kt  "
                      f"VS={data['vertical_speed']:.0f} fpm  ground={data['on_ground']}  "
                      f"engine={data['engine_running']}  fuel={data['fuel_total_weight']:.0f} lb  "
                      f"touchdowns={data['touchdowns']}")
            time.sleep(1)
    except KeyboardInterrupt:
        connector.disconnect()