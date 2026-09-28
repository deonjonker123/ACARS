"""
core/flight_session.py

The single source of truth for "connect to the sim, poll it, track flight
state, check it against the dispatched flight plan, and record completed
flights to the database."

This exists so that logic isn't duplicated between ui/live_monitor.py and
any future embedded live-flight widget (e.g. on the Home page). Both should
use ONE instance of FlightSessionController rather than each rolling their
own connector+tracker+db wiring - a bug fixed here (like the ATCCOM
sanitization) only needs fixing once, everywhere it's used.

This module has no Qt dependency at all - it's plain Python, driven by
whoever owns it calling tick() on a timer (a QTimer in the UI, or a plain
loop in a test script).

Flight rules (a flight only exists because of a dispatched flight plan):
  - dispatch(plan, registration): a SimBrief OFP (core.simbrief) plus the
    fleet airframe the pilot picked. The airframe must be in the active
    fleet and the pilot rated for it.
  - Block time only starts (engine start) within ARMING_RADIUS_NM of the
    planned origin. Anywhere else, engine starts are ignored.
  - At block end, the aircraft must be within ARMING_RADIUS_NM of the
    planned destination or the planned alternate. Otherwise the flight is
    REJECTED: pilot's rejected count +1, nothing logged, no hours added.
  - cancel_dispatch() stops tracking and clears the plan - no penalty.
  - The sim's own tail number (ATC ID) is not checked; the flight is
    logged against the dispatched airframe.

Usage:
    controller = FlightSessionController()
    controller.dispatch(plan, "N104TW")       # raises FlightDispatchError
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

    try:
        controller.record_pending_flight()
    except FlightSaveBlocked as e:
        show(str(e))              # e.g. airframe deleted / pilot no longer rated -
                                  # the flight stays pending, fix it and save again
"""

import math
from datetime import datetime

from connectors.msfs import MSFSConnector
from core.flight_state import FlightStateTracker
from core.db import FlightDatabase, normalize_registration
from core.airports import AirportLookup

ARMING_RADIUS_NM = 5.0


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
    everything except the bulky parts - SimBrief's OFP text (~50 KB), the
    takeoff/landing report text and the navlog. The PDF link is kept."""
    stored = {k: v for k, v in plan.items() if k not in ("ofp", "navlog")}
    stored["ofp"] = {"pdf_url": (plan.get("ofp") or {}).get("pdf_url")}
    return stored


def _distance_to_airport(lat, lon, airport):
    if airport is None:
        return None
    return _distance_nm(lat, lon, airport["lat"], airport["lon"])


class FlightSessionController:
    def __init__(self, db=None):
        self.connector = MSFSConnector()
        self.tracker = FlightStateTracker()
        self.db = db or FlightDatabase()
        self.db.init_db()
        self.airport_lookup = AirportLookup()
        self.airport_lookup_ready = False
        try:
            self.airport_lookup.load()
            self.airport_lookup_ready = True
        except FileNotFoundError as e:
            print(f"[flight_session] Airport lookup unavailable: {e}")

        self.connected = False
        self.dispatch_info = None
        self.pending_flight_event = None

    def dispatch(self, plan, registration):
        """Locks in a flight plan and the fleet airframe to fly it in."""
        if self.tracker.state == "BLOCK":
            raise FlightDispatchError("A flight is in progress. Cancel it before dispatching a new one.")
        if self.pending_flight_event is not None:
            raise FlightDispatchError("Save or cancel the completed flight before dispatching a new one.")

        registration = normalize_registration(registration)
        if not registration:
            raise FlightDispatchError("Pick an aircraft from your fleet.")
        reason = self.db.save_block_reason(registration)
        if reason is not None:
            raise FlightDispatchError(reason)

        aircraft = self.db.get_aircraft(registration)
        self.dispatch_info = {
            "plan": plan,
            "registration": registration,
            "designation": aircraft["designation"],
        }
        self.tracker.reset()

    def cancel_dispatch(self):
        """Stops tracking and drops the dispatched plan (and any unsaved
        completed flight). Nothing is logged and nothing counts against
        the pilot. Monitoring (the connector) is left to the caller."""
        self.dispatch_info = None
        self.pending_flight_event = None
        self.tracker.reset()

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
                rejection = self._check_arrival(event)
                if rejection is not None:
                    events.append(rejection)
                else:
                    self.pending_flight_event = event

        if result["state"] == "BLOCK":
            arming = None
        elif self.pending_flight_event is not None:
            arming = "Flight complete - save it."

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
        """At block end: accept if within range of the planned destination
        or alternate (and note which one in event["arrival_icao"]).
        Otherwise reject the flight and return a "flight_rejected" event."""
        plan = self.dispatch_info["plan"]
        lat, lon = event.get("arr_lat"), event.get("arr_lon")

        for airport in (plan["destination"], plan.get("alternate")):
            dist = _distance_to_airport(lat, lon, airport)
            if dist is not None and dist <= ARMING_RADIUS_NM:
                event["arrival_icao"] = airport["icao"]
                return None

        dest = plan["destination"]
        dist = _distance_to_airport(lat, lon, dest)
        where = f"{dist:,.1f} nm from {dest['icao']}" if dist is not None else f"away from {dest['icao']}"
        alt = plan.get("alternate")
        alt_text = f" (ALT {alt['icao']})" if alt else ""
        reason = f"Landed {where}{alt_text} - flight rejected."

        self.db.reject_flight()
        self.dispatch_info = None
        return {"type": "flight_rejected", "time": event.get("time"), "reason": reason}

    def record_pending_flight(self):
        """Call this when the pilot presses Save. Writes the held
        block_end event to the database and returns the new flight's id,
        or None if there was nothing pending. Clears the dispatch on
        success.

        Raises FlightSaveBlocked (flight stays pending) if the dispatched
        airframe was removed from the fleet or the pilot isn't rated for
        it any more."""
        if self.pending_flight_event is None or self.dispatch_info is None:
            return None
        flight_id = self._record_flight(self.pending_flight_event)
        # Only cleared once the save actually succeeded
        self.pending_flight_event = None
        self.dispatch_info = None
        return flight_id

    def discard_pending_flight(self):
        """Drops the pending flight without saving it - e.g. if the pilot
        decides a landing shouldn't count. Not currently wired to any UI
        button, but available if you want a 'Discard' option alongside Save."""
        self.pending_flight_event = None

    def _record_flight(self, event):
        """
        Persists a completed flight. Returns the new flight's id.

        Departure/arrival are the PLANNED airports: block start was only
        allowed within range of the origin, and block end was checked
        against the destination/alternate (event["arrival_icao"]).
        Flight number, pax and cargo come from the SimBrief plan; distance
        and block time are what was actually flown.
        """
        plan = self.dispatch_info["plan"]
        registration = self.dispatch_info["registration"]

        reason = self.db.save_block_reason(registration)
        if reason is not None:
            raise FlightSaveBlocked(reason)

        designation = self.db.get_aircraft(registration)["designation"]

        departed_at = datetime.fromtimestamp(event["block_start_time"]).isoformat() if event.get("block_start_time") else None
        arrived_at = datetime.fromtimestamp(event["time"]).isoformat() if event.get("time") else None

        log_data = dict(event)
        log_data["flight_plan"] = _plan_for_log(plan)

        self.db.log_flight(
            flight_number=plan.get("flight_number"),
            aircraft_designation=designation,
            aircraft_registration=registration,
            network="OFFLINE",
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
        )
        return self.db.list_flights(limit=1)[0]["id"]

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
                "atc_id": "ASXGS"}   # sim tail number deliberately different - ignored

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
    print("  saved flight id:", controller.record_pending_flight())

    print("\n3) KLAX -> KSNA (planned alternate) - saved:")
    controller.dispatch(plan, "N104TW")
    fly([tick(33.9425, -118.408, True, 1.0), tick(33.8, -118.1, True, 0.0, 500),
         tick(33.6757, -117.8682, True, 1.0, -150), tick(33.6757, -117.8682, False, 1.0)])
    print("  saved flight id:", controller.record_pending_flight())

    print("\n4) KLAX -> KBUR (not planned) - rejected:")
    controller.dispatch(plan, "N104TW")
    fly([tick(33.9425, -118.408, True, 1.0), tick(34.1, -118.35, True, 0.0, 500),
         tick(34.2007, -118.3585, True, 1.0, -150), tick(34.2007, -118.3585, False, 1.0)])

    print("\nLogbook:")
    for f in controller.db.list_flights():
        print(f"  {f['flight_number']} {f['departure_airport']} -> {f['arrival_airport']} "
              f"{f['aircraft_registration']} pax={f['pax_count']} cargo={f['cargo_kg']}")
    p = controller.db.get_pilot()
    print(f"Pilot: completed={p['total_completed']} rejected={p['total_rejected']} "
          f"hours={p['total_hours_flown']:.4f}")