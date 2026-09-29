"""
core/flight_session.py

The single source of truth for "connect to the sim, poll it, track flight
state, check it against the dispatched flight plan, and record completed
flights to the database."

The app uses ONE instance of FlightSessionController (owned by the
Dashboard) rather than each page rolling its own connector+tracker+db
wiring - a bug fixed here (like the ATCCOM sanitization) only needs fixing
once, everywhere it's used.

This module has no Qt dependency at all - it's plain Python, driven by
whoever owns it calling tick() on a timer (a QTimer in the UI, or a plain
loop in a test script).

Flight rules (a flight only exists because of a dispatched flight plan):
  - dispatch(plan, registration): a SimBrief OFP (core.simbrief) plus the
    fleet airframe the pilot picked. The airframe must be in the active
    fleet and the pilot rated for it.
  - Block time only starts (engine start) within ARMING_RADIUS_NM of the
    planned origin. Anywhere else, engine starts are ignored.
    - At block end the flight is complete and waits for the pilot:
    submit_pending_flight() or discard_pending_flight(). Nothing is
    judged until Submit.
  - On Submit, the aircraft must have parked within ARMING_RADIUS_NM of
    the planned destination or the planned alternate. Otherwise the flight
    is REJECTED: logged as rejected (for the record and the debrief),
    pilot's rejected count +1, no hours or stats.
  - A landing harder than HARD_LANDING_LIMIT_FPM also REJECTS the flight,
    the same way. Exactly the limit is accepted; a flight with no
    captured landing rate isn't judged on it.
  - Discard (a completed flight) and cancel_dispatch() (any time) stop
    tracking and clear the plan - nothing logged, no penalty.
  - Network: the pilot declares it at dispatch (OFFLINE, VATSIM or IVAO).
    For VATSIM/IVAO the UI runs core.networks.check_online() for that
    network in the background about once a minute and hands each result
    to record_network_check(). Only checks made during block time count.
    The flight is logged as the declared network if the pilot was seen
    online on it in at least NETWORK_ONLINE_SHARE of those checks,
    otherwise OFFLINE (also if it could never be checked). Checks where a
    feed couldn't be reached (and nothing was found) don't count.

Usage:
    controller = FlightSessionController()
    controller.dispatch(plan, "N104TW", network="VATSIM")   # raises FlightDispatchError
    ...
    result = controller.tick()    # call this once per poll interval
    result["connected"]           # bool - sim handshake succeeded
    result["has_data"]            # bool - actively receiving flight data
    result["state"]               # "IDLE" or "BLOCK"
    result["data"]                # latest raw telemetry dict, or None
    result["live"]                # tracker's live stats (distance, fuel burned, elapsed hours, g peak)
    result["events"]              # events that fired this tick (incl. "flight_rejected")
    result["pending_flight"]      # bool - a completed flight is waiting to be saved
    result["dispatch"]            # the dispatch dict, or None
    result["arming"]              # user-readable status of the origin check, or None

    controller.record_network_check(check_online(...))   # see core/networks.py

    try:
        controller.record_pending_flight()
    except FlightSaveBlocked as e:
        show(str(e))              # e.g. airframe deleted / pilot no longer rated -
                                  # the flight stays pending, fix it and save again
"""

import json
import math
import os
import time
from datetime import datetime

from connectors.auto import AutoConnector
from core.flight_state import FlightStateTracker
from core.db import (FlightDatabase, normalize_registration, VALID_NETWORKS, STATUS_ACCEPTED, STATUS_REJECTED)
from core.airports import AirportLookup
from core.landing_grade import grade_flight
from core.paths import ACTIVE_FLIGHT_PATH

ARMING_RADIUS_NM = 5.0
HARD_LANDING_LIMIT_FPM = -800
NETWORK_ONLINE_SHARE = 0.5
UNKNOWN_AIRPORT = "----"

class FlightSaveBlocked(Exception):
    """Raised by record_pending_flight() when the flight may not be saved
    (the dispatched airframe was deleted, or the pilot is no longer rated
    for it). The message is user-readable. The flight stays pending."""


class FlightDispatchError(Exception):
    """Raised by dispatch() when the plan/airframe can't be dispatched.
    The message is user-readable."""


def _distance_nm(lat1, lon1, lat2, lon2):
    """Great-circle distance in nm, or None if any coordinate is missing."""
    if None in (lat1, lon1, lat2, lon2):
        return None
    r_nm = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r_nm * math.asin(math.sqrt(a))


def _plan_for_log(plan):
    """The flight plan as stored in the logbook's flight detail (log_data):
    everything except the bulky parts - SimBrief's OFP text (~50 KB) and
    the takeoff/landing report text. The PDF link is kept, and the navlog
    only as each fix's ident and position (the debrief's planned route)."""
    stored = {k: v for k, v in plan.items() if k not in ("ofp", "navlog")}
    stored["ofp"] = {"pdf_url": (plan.get("ofp") or {}).get("pdf_url")}
    stored["navlog"] = [
        {"ident": fix.get("ident"), "lat": fix.get("lat"), "lon": fix.get("lon")}
        for fix in plan.get("navlog") or []
        if fix.get("lat") is not None and fix.get("lon") is not None
    ]
    return stored

def _distance_to_airport(lat, lon, airport):
    if airport is None:
        return None
    return _distance_nm(lat, lon, airport["lat"], airport["lon"])

class FlightSessionController:
    def __init__(self, db=None):
        self.connector = AutoConnector()
        self.tracker = FlightStateTracker()
        self.db = db or FlightDatabase()
        self.db.init_db()

        self.connected = False
        self.dispatch_info = None
        self.pending_flight_event = None
        self._reset_network_checks()
        self._ui_state = {}

    def dispatch(self, plan, registration, network="OFFLINE"):
        """Locks in a flight plan, the fleet airframe to fly it in and the
        network the pilot says they'll fly it on."""
        if self.tracker.state == "BLOCK":
            raise FlightDispatchError("A flight is in progress. Cancel it before dispatching a new one.")
        if self.pending_flight_event is not None:
            raise FlightDispatchError("Save or cancel the completed flight before dispatching a new one.")

        registration = normalize_registration(registration)
        if not registration:
            raise FlightDispatchError("Pick an aircraft from your fleet.")
        network = (network or "OFFLINE").upper()
        if network not in VALID_NETWORKS:
            raise FlightDispatchError(f"Unknown network: {network}.")
        reason = self.db.save_block_reason(registration)
        if reason is not None:
            raise FlightDispatchError(reason)

        aircraft = self.db.get_aircraft(registration)
        self.dispatch_info = {
            "plan": plan,
            "registration": registration,
            "designation": aircraft["designation"],
            "network": network,
        }
        self.tracker.reset()
        self._reset_network_checks()
        self._ui_state = {}
        self.save_active_flight()

    def cancel_dispatch(self):
        """Stops tracking and drops the dispatched plan (and any unsaved
        completed flight). Nothing is logged and nothing counts against
        the pilot. Monitoring (the connector) is left to the caller."""
        self.dispatch_info = None
        self.pending_flight_event = None
        self.tracker.reset()
        self._reset_network_checks()
        self.clear_active_flight()

    def save_active_flight(self, ui_state=None):
        """Writes the dispatched flight to ACTIVE_FLIGHT_PATH: the dispatch,
        the tracker, the network counts and any completed-but-unsaved
        flight. ui_state is extra data the UI wants back on resume (e.g. the
        Live Map track); the last one given is kept for the automatic saves.
        Written to a temp file first, so a crash mid-write can't corrupt it."""
        if ui_state is not None:
            self._ui_state = ui_state
        if self.dispatch_info is None:
            self.clear_active_flight()
            return
        saved = {
            "saved_at": time.time(),
            "dispatch": self.dispatch_info,
            "tracker": self.tracker.to_state(),
            "pending_flight_event": self.pending_flight_event,
            "network_checks": self._network_checks,
            "network_online": self._network_online,
            "ui": self._ui_state,
        }
        temp_path = ACTIVE_FLIGHT_PATH + ".tmp"
        try:
            os.makedirs(os.path.dirname(ACTIVE_FLIGHT_PATH), exist_ok=True)
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(saved, f)
            os.replace(temp_path, ACTIVE_FLIGHT_PATH)
        except (OSError, TypeError, ValueError) as e:
            print(f"[flight_session] Couldn't save the flight in progress: {e}")

    @staticmethod
    def load_active_flight():
        """The saved flight in progress, or None if there isn't one (or it
        can't be read - it's then renamed to .bad so it doesn't come back)."""
        if not os.path.exists(ACTIVE_FLIGHT_PATH):
            return None
        try:
            with open(ACTIVE_FLIGHT_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if not isinstance(saved, dict) or not saved.get("dispatch"):
                raise ValueError("no dispatch in it")
            return saved
        except (OSError, ValueError) as e:
            print(f"[flight_session] Couldn't read the saved flight ({e}) - set aside as .bad")
            try:
                os.replace(ACTIVE_FLIGHT_PATH, ACTIVE_FLIGHT_PATH + ".bad")
            except OSError:
                pass
            return None

    def restore_active_flight(self, saved):
        """Carries on a flight from load_active_flight(). Returns the UI's
        saved extra data (see save_active_flight)."""
        self.dispatch_info = saved["dispatch"]
        self.tracker.restore_state(saved.get("tracker") or {})
        self.pending_flight_event = saved.get("pending_flight_event")
        self._network_checks = saved.get("network_checks") or 0
        self._network_online = dict(saved.get("network_online") or {})
        self._ui_state = saved.get("ui") or {}
        return self._ui_state

    @staticmethod
    def clear_active_flight():
        try:
            os.remove(ACTIVE_FLIGHT_PATH)
        except FileNotFoundError:
            pass
        except OSError as e:
            print(f"[flight_session] Couldn't remove the saved flight: {e}")

    def _reset_network_checks(self):
        self._network_checks = 0
        self._network_online = {}

    def record_network_check(self, result):
        """Counts one core.networks.check_online() result towards this
        flight's network - only during block time, and only if the check
        was conclusive (online somewhere, or every feed was reachable)."""
        if self.tracker.state != "BLOCK" or not result:
            return
        network = result.get("network")
        if network is None and result.get("errors"):
            return
        self._network_checks += 1
        if network is not None:
            self._network_online[network] = self._network_online.get(network, 0) + 1

    def declared_network(self):
        """The network chosen at dispatch ("OFFLINE" if none)."""
        return (self.dispatch_info or {}).get("network") or "OFFLINE"

    def network_for_log(self):
        """The declared network if the checks confirm it, else "OFFLINE"."""
        declared = self.declared_network()
        if declared == "OFFLINE" or not self._network_checks:
            return "OFFLINE"
        online = self._network_online.get(declared, 0)
        return declared if online / self._network_checks >= NETWORK_ONLINE_SHARE else "OFFLINE"

    def _arming_status(self, data):
        """Returns (can_start_block, user-readable status or None)."""
        if self.dispatch_info is None:
            return False, "No flight dispatched - fetch a SimBrief plan first."
        if self.pending_flight_event is not None:
            return False, "Flight complete - save it."
        if self.tracker.state == "BLOCK":
            return False, None

        origin = self.dispatch_info["plan"]["origin"]
        dist = _distance_to_airport(data.get("latitude"), data.get("longitude"), origin)
        if dist is None:
            return False, "Waiting for aircraft position..."
        if dist > ARMING_RADIUS_NM:
            return False, f"Move to {origin['icao']} ({dist:,.1f} nm away)."
        return True, f"At {origin['icao']} - start engine to begin the flight."

    def _idle_result(self, connected):
        return {
            "connected": connected,
            "has_data": False,
            "state": self.tracker.state,
            "data": None,
            "live": {},
            "events": [],
            "pending_flight": self.pending_flight_event is not None,
            "dispatch": self.dispatch_info,
            "arming": None,
            "sim_name": getattr(self.connector, "SIM_NAME", "Unknown Sim"),
        }

    def tick(self):
        """Call once per poll interval. Handles reconnecting automatically
        if the connection drops or hasn't been established yet."""
        if not self.connected:
            self.connect()
            return self._idle_result(self.connected)

        try:
            data = self.connector.read()
        except Exception as e:
            self.connected = False
            self._last_connect_error = str(e)
            return self._idle_result(False)

        if data is None:
            return self._idle_result(True)

        can_start, arming = self._arming_status(data)
        result = self.tracker.update(data, can_start_block=can_start)

        events = list(result["events"])
        for event in result["events"]:
            if event["type"] == "block_end":
                self.pending_flight_event = event

        if self.dispatch_info is not None and any(
                e["type"] in ("block_start", "airborne", "landing", "block_end") for e in events):
            self.save_active_flight()

        if result["state"] == "BLOCK":
            arming = None
        elif self.pending_flight_event is not None:
            arming = "Flight complete - submit or discard it."

        return {
            "connected": True,
            "has_data": True,
            "state": result["state"],
            "data": data,
            "live": result["live"],
            "events": events,
            "pending_flight": self.pending_flight_event is not None,
            "dispatch": self.dispatch_info,
            "arming": arming,
            "sim_name": getattr(self.connector, "SIM_NAME", "Unknown Sim"),
        }

    def _check_arrival(self, event):
        """The flight rules, checked on Submit: the aircraft must have parked
        within range of the planned destination or alternate (noted in
        event["arrival_icao"]), and landed no harder than
        HARD_LANDING_LIMIT_FPM. Returns None if the flight is accepted,
        otherwise the user-readable reason it's rejected."""
        plan = self.dispatch_info["plan"]
        lat, lon = event.get("arr_lat"), event.get("arr_lon")

        for airport in (plan["destination"], plan.get("alternate")):
            dist = _distance_to_airport(lat, lon, airport)
            if dist is not None and dist <= ARMING_RADIUS_NM:
                event["arrival_icao"] = airport["icao"]
                break
        else:
            dest = plan["destination"]
            dist = _distance_to_airport(lat, lon, dest)
            where = f"{dist:,.1f} nm from {dest['icao']}" if dist is not None else f"away from {dest['icao']}"
            alt = plan.get("alternate")
            alt_text = f" (ALT {alt['icao']})" if alt else ""
            return f"Parked {where}{alt_text}, not at the planned destination or alternate."

        landing_vs = event.get("landing_vs")
        if isinstance(landing_vs, (int, float)) and landing_vs < HARD_LANDING_LIMIT_FPM:
            return f"Landed at {landing_vs:,.0f} fpm (limit {HARD_LANDING_LIMIT_FPM:,} fpm)."
        return None

    def submit_pending_flight(self):
        """Call when the pilot presses Submit. Checks the completed flight
        against the flight rules and logs it either way - accepted (hours,
        rank and stats updated) or rejected (kept for the record and the
        debrief, rejected count +1, nothing else counts). The dispatch
        ends. Returns the outcome for the UI, or None if nothing was pending:

            {"accepted", "flight_id", "flight_number", "departure", "arrival",
             "registration", "block_hours", "landing_vs", "network",
             "grade" ({"score", "letter", "parts"} or None),
             "reason" (rejected only)}

        Raises FlightSaveBlocked (flight stays pending) if an accepted
        flight can't be logged: the dispatched airframe was removed from
        the fleet or the pilot isn't rated for it any more."""
        event = self.pending_flight_event
        if event is None or self.dispatch_info is None:
            return None

        plan = self.dispatch_info["plan"]
        reason = self._check_arrival(event)
        if reason is not None and not event.get("arrival_icao"):
            event["arrival_icao"] = self._parked_at(event) or UNKNOWN_AIRPORT
        grade = grade_flight(event, event.get("landing_vs"))
        network = self.network_for_log()
        flight_id = self._record_flight(event, grade, reject_reason=reason)

        outcome = {
            "accepted": reason is None,
            "flight_id": flight_id,
            "flight_number": plan.get("flight_number"),
            "departure": plan["origin"]["icao"],
            "arrival": event.get("arrival_icao") or plan["destination"]["icao"],
            "registration": self.dispatch_info["registration"],
            "block_hours": event.get("block_hours"),
            "landing_vs": event.get("landing_vs"),
            "network": network,
            "grade": grade,
        }
        if reason is not None:
            outcome["reason"] = reason

        self._end_dispatch()
        return outcome

    @staticmethod
    def _parked_at(event):
        """The ICAO of the airport the aircraft parked at, for a flight
        that ended away from its plan - or None if there's none nearby."""
        try:
            lookup = AirportLookup()
            lookup.load()
            airport = lookup.find_nearest(event.get("arr_lat"), event.get("arr_lon"),
                                          max_radius_nm=ARMING_RADIUS_NM)
        except (OSError, RuntimeError) as e:
            print(f"[flight_session] Couldn't look up the arrival airport: {e}")
            return None
        return airport["icao"] if airport else None

    def discard_pending_flight(self):
        """Call when the pilot presses Discard: drops the completed flight
        and ends the dispatch. Nothing is logged, nothing counts."""
        self.cancel_dispatch()

    def _end_dispatch(self):
        self.pending_flight_event = None
        self.dispatch_info = None
        self._reset_network_checks()
        self.clear_active_flight()

    def _record_flight(self, event, grade, reject_reason=None):
        """
        Persists a submitted flight - accepted, or rejected if a
        reject_reason is given. Returns the new flight's id.

        Departure is the PLANNED origin (block start was only allowed
        within range of it); arrival is the planned destination/alternate
        it parked at (event["arrival_icao"]), or for a flight rejected for
        parking elsewhere, the airport it did park at.
        Flight number, pax and cargo come from the SimBrief plan; distance
        and block time are what was actually flown.
        """
        plan = self.dispatch_info["plan"]
        registration = self.dispatch_info["registration"]
        accepted = reject_reason is None

        if accepted:
            reason = self.db.save_block_reason(registration)
            if reason is not None:
                raise FlightSaveBlocked(reason)

        aircraft = self.db.get_aircraft(registration, include_retired=True)
        designation = aircraft["designation"] if aircraft else self.dispatch_info["designation"]

        departed_at = datetime.fromtimestamp(event["block_start_time"]).isoformat() if event.get(
            "block_start_time") else None
        arrived_at = datetime.fromtimestamp(event["time"]).isoformat() if event.get("time") else None

        log_data = dict(event)
        log_data["flight_plan"] = _plan_for_log(plan)
        network = self.network_for_log()
        log_data["network_checks"] = {
            "declared": self.declared_network(),
            "checks": self._network_checks,
            "online": dict(self._network_online),
            "logged_as": network,
        }

        return self.db.log_flight(
            flight_number=plan.get("flight_number"),
            aircraft_designation=designation,
            aircraft_registration=registration,
            network=network,
            departure_airport=plan["origin"]["icao"],
            arrival_airport=event.get("arrival_icao") or plan["destination"]["icao"],
            distance_nm=event.get("distance_nm"),
            pax_count=plan.get("pax_count"),
            cargo_kg=plan.get("cargo_kg"),
            block_hours=event.get("block_hours"),
            departed_at=departed_at,
            arrived_at=arrived_at,
            landing_vs=event.get("landing_vs"),
            log_data=log_data,
            status=STATUS_ACCEPTED if accepted else STATUS_REJECTED,
            landing_grade=grade["score"] if grade else None,
            reject_reason=reject_reason,
        )

    def connect(self):
        try:
            self.connector.connect()
            self.connected = True
        except Exception as e:
            self.connected = False
            self._last_connect_error = str(e)

    def disconnect(self):
        try:
            self.connector.disconnect()
        except Exception as e:
            print(f"[flight_session] Error during disconnect (ignored, continuing): {e}")
        finally:
            self.connected = False


if __name__ == "__main__":
    import tempfile, os

    controller = FlightSessionController()
    test_db_path = os.path.join(tempfile.gettempdir(), "acars_session_test.db")
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
    controller.db = FlightDatabase(db_path=test_db_path)
    controller.db.init_db()
    controller.db.add_aircraft("N104TW", "C172", categories=["prop"], unlock_rank="student_pilot")
    controller.connected = True

    KLAX = {"icao": "KLAX", "name": "LOS ANGELES INTL", "lat": 33.942497, "lon": -118.40805}
    KSMO = {"icao": "KSMO", "name": "SANTA MONICA MUN", "lat": 34.015822, "lon": -118.451306}
    KSNA = {"icao": "KSNA", "name": "JOHN WAYNE", "lat": 33.675661, "lon": -117.868233}
    plan = {"flight_number": "TW001", "origin": KLAX, "destination": KSMO, "alternate": KSNA,
            "pax_count": 3, "cargo_kg": 44}

    def tick(lat, lon, engine, on_ground, vs=0.0):
        return {"on_ground": on_ground, "engine_running": engine, "latitude": lat, "longitude": lon,
                "fuel_total_weight": 150, "vertical_speed": vs, "g_force": 1.0,
                "gear_handle_position": 1, "flaps_handle_index": 0, "parking_brake": 0,
                "atc_id": "ASXGS"}

    def fly(ticks):
        for t in ticks:
            controller.connector.read = lambda t=t: t
            r = controller.tick()
            print(f"  state={r['state']:5} arming={r['arming']!r} events={[e['type'] for e in r['events']]}")
            for e in r["events"]:
                if e["type"] == "flight_rejected":
                    print("  ->", e["reason"])

    print("1) Engine start 300 nm away from origin - must NOT start a block:")
    controller.dispatch(plan, "N104TW")
    fly([tick(37.6, -122.4, True, 1.0)])

    print("\n2) KLAX -> KSMO (planned destination) - saved:")
    controller.dispatch(plan, "N104TW")
    fly([tick(33.9425, -118.408, False, 1.0), tick(33.9425, -118.408, True, 1.0),
         tick(34.0, -118.43, True, 0.0, 500), tick(34.0158, -118.4513, True, 1.0, -120),
         tick(34.0158, -118.4513, False, 1.0)])
    print("  saved flight id:", controller.submit_pending_flight())

    print("\n3) KLAX -> KSNA (planned alternate) - saved:")
    controller.dispatch(plan, "N104TW")
    fly([tick(33.9425, -118.408, True, 1.0), tick(33.8, -118.1, True, 0.0, 500),
         tick(33.6757, -117.8682, True, 1.0, -150), tick(33.6757, -117.8682, False, 1.0)])
    print("  saved flight id:", controller.record_pending_flight())

    print("\n3b) Declared VATSIM, online for 3 of 4 checks (one feed timeout ignored) - VATSIM:")
    controller.dispatch(plan, "N104TW", network="VATSIM")
    fly([tick(33.9425, -118.408, True, 1.0), tick(34.0, -118.43, True, 0.0, 500)])
    for check in ({"network": "VATSIM", "errors": {}}, {"network": "VATSIM", "errors": {}},
                  {"network": None, "errors": {}}, {"network": "VATSIM", "errors": {}},
                  {"network": None, "errors": {"VATSIM": "timeout"}}):
        controller.record_network_check(check)
    print("  network so far:", controller.network_for_log())
    fly([tick(34.0158, -118.4513, True, 1.0, -120), tick(34.0158, -118.4513, False, 1.0)])
    controller.record_network_check({"network": None, "errors": {}})
    print("  saved flight id:", controller.record_pending_flight())

    print("\n3c) Declared IVAO but only seen on VATSIM - OFFLINE:")
    controller.dispatch(plan, "N104TW", network="IVAO")
    fly([tick(33.9425, -118.408, True, 1.0), tick(34.0, -118.43, True, 0.0, 500)])
    for _ in range(3):
        controller.record_network_check({"network": "VATSIM", "errors": {}})
    fly([tick(34.0158, -118.4513, True, 1.0, -120), tick(34.0158, -118.4513, False, 1.0)])
    print("  saved flight id:", controller.record_pending_flight())

    print("\n3d) Declared VATSIM, online for 1 of 3 checks - OFFLINE:")
    controller.dispatch(plan, "N104TW", network="VATSIM")
    fly([tick(33.9425, -118.408, True, 1.0), tick(34.0, -118.43, True, 0.0, 500)])
    for check in ({"network": "VATSIM", "errors": {}}, {"network": None, "errors": {}},
                  {"network": None, "errors": {}}):
        controller.record_network_check(check)
    fly([tick(34.0158, -118.4513, True, 1.0, -120), tick(34.0158, -118.4513, False, 1.0)])
    print("  saved flight id:", controller.record_pending_flight())

    print("\n4) KLAX -> KBUR (not planned) - rejected:")
    controller.dispatch(plan, "N104TW")
    fly([tick(33.9425, -118.408, True, 1.0), tick(34.1, -118.35, True, 0.0, 500),
         tick(34.2007, -118.3585, True, 1.0, -150), tick(34.2007, -118.3585, False, 1.0)])

    print("\n5) KLAX -> KSMO, touched down at -950 fpm - rejected:")
    controller.dispatch(plan, "N104TW")
    fly([tick(33.9425, -118.408, True, 1.0), tick(34.0, -118.43, True, 0.0, -950),
         tick(34.0158, -118.4513, True, 1.0, 60), tick(34.0158, -118.4513, False, 1.0)])

    print("\n6) KLAX -> KSMO, touched down at exactly -800 fpm - saved:")
    controller.dispatch(plan, "N104TW")
    fly([tick(33.9425, -118.408, True, 1.0), tick(34.0, -118.43, True, 0.0, -800),
         tick(34.0158, -118.4513, True, 1.0, 40), tick(34.0158, -118.4513, False, 1.0)])
    print("  saved flight id:", controller.record_pending_flight())

    print("\nLogbook:")
    for f in controller.db.list_flights():
        print(f"  {f['flight_number']} {f['departure_airport']} -> {f['arrival_airport']} "
              f"{f['aircraft_registration']} pax={f['pax_count']} cargo={f['cargo_kg']} "
              f"network={f['network']} landing={f['landing_vs']}")
    p = controller.db.get_pilot()
    print(f"Pilot: completed={p['total_completed']} rejected={p['total_rejected']} "
          f"hours={p['total_hours_flown']:.4f}")