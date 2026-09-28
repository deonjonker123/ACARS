"""
core/flight_session.py

The single source of truth for "connect to the sim, poll it, track flight
state, resolve airports, and record completed flights to the database."

This exists so that logic isn't duplicated between ui/live_monitor.py and
any future embedded live-flight widget (e.g. on the Home page). Both should
use ONE instance of FlightSessionController rather than each rolling their
own connector+tracker+db wiring - a bug fixed here (like the ATCCOM
sanitization) only needs fixing once, everywhere it's used.

This module has no Qt dependency at all - it's plain Python, driven by
whoever owns it calling tick() on a timer (a QTimer in the UI, or a plain
loop in a test script).

Usage:
    controller = FlightSessionController()
    controller.connect()          # safe to call even if the sim isn't up yet
    ...
    result = controller.tick()    # call this once per poll interval
    result["connected"]           # bool - sim handshake succeeded
    result["has_data"]            # bool - actively receiving flight data
    result["state"]               # "IDLE" or "BLOCK"
    result["data"]                # latest raw telemetry dict, or None
    result["live"]                # tracker's live stats (distance, fuel burned, elapsed hours, g peak)
    result["events"]              # events that fired this tick
    result["pending_flight"]      # bool - a completed flight is waiting to be saved
"""

from datetime import datetime

from connectors.msfs import MSFSConnector
from core.flight_state import FlightStateTracker
from core.db import FlightDatabase
from core.airports import AirportLookup


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

        # Holds the block_end event once a flight completes, until the
        # pilot explicitly saves it via record_pending_flight() - nothing
        # gets written to the DB automatically anymore.
        self.pending_flight_event = None

    def connect(self):
        try:
            self.connector.connect()
            self.connected = True
        except Exception as e:
            self.connected = False
            self._last_connect_error = str(e)

    def tick(self):
        """Call once per poll interval. Handles reconnecting automatically
        if the connection drops or hasn't been established yet."""
        if not self.connected:
            self.connect()
            return {
                "connected": self.connected,
                "has_data": False,
                "state": self.tracker.state,
                "data": None,
                "live": {},
                "events": [],
                "pending_flight": self.pending_flight_event is not None,
                "sim_name": getattr(self.connector, "SIM_NAME", "Unknown Sim"),
            }

        try:
            data = self.connector.read()
        except Exception as e:
            self.connected = False
            self._last_connect_error = str(e)
            return {
                "connected": False,
                "has_data": False,
                "state": self.tracker.state,
                "data": None,
                "live": {},
                "events": [],
                "pending_flight": self.pending_flight_event is not None,
                "sim_name": getattr(self.connector, "SIM_NAME", "Unknown Sim"),
            }

        if data is None:
            return {
                "connected": True,
                "has_data": False,
                "state": self.tracker.state,
                "data": None,
                "live": {},
                "events": [],
                "pending_flight": self.pending_flight_event is not None,
                "sim_name": getattr(self.connector, "SIM_NAME", "Unknown Sim"),
            }

        result = self.tracker.update(data)
        for event in result["events"]:
            if event["type"] == "block_end":
                # No longer auto-recorded here. Held until the pilot hits
                # Save (record_pending_flight) or discards it.
                self.pending_flight_event = event

        return {
            "connected": True,
            "has_data": True,
            "state": result["state"],
            "data": data,
            "live": result["live"],
            "events": result["events"],
            "pending_flight": self.pending_flight_event is not None,
            "sim_name": getattr(self.connector, "SIM_NAME", "Unknown Sim"),
        }

    def record_pending_flight(self):
        """Call this when the pilot presses Save. Writes the held
        block_end event to the database and returns the new flight's id,
        or None if there was nothing pending."""
        if self.pending_flight_event is None:
            return None
        flight_id = self._record_flight(self.pending_flight_event)
        self.pending_flight_event = None
        return flight_id

    def discard_pending_flight(self):
        """Drops the pending flight without saving it - e.g. if the pilot
        decides a landing shouldn't count. Not currently wired to any UI
        button, but available if you want a 'Discard' option alongside Save."""
        self.pending_flight_event = None

    # ---------------- flight recording (moved here from live_monitor.py) ----------------

    def _resolve_airport(self, lat, lon):
        if self.airport_lookup_ready:
            match = self.airport_lookup.find_nearest(lat, lon)
            if match is not None:
                return match["icao"]
        if lat is None or lon is None:
            return "UNKNOWN"
        return f"{lat:.4f},{lon:.4f}"

    def _record_flight(self, event):
        """
        Persists a completed flight. Returns the new flight's id.

        Known limitations (same as before, just consolidated here now):
        - Airport resolution falls back to raw lat/lon text outside a 50nm
          radius of anything in the OurAirports dataset.
        - No dispatch screen yet: pax_count/cargo_kg/flight_number/network
          are placeholder/None/"OFFLINE".
        - Registration falls back to "UNKNOWN-<title>" if ATC_ID is blank,
          which will collide across flights in the same untitled livery.
        """
        registration = event.get("atc_id") or f"UNKNOWN-{event.get('aircraft_title') or 'aircraft'}"
        # atc_model is the actual type code ("C172"), matching fleet_and_ranks.json.
        # atc_type gives the manufacturer ("CESSNA") - not useful for fleet matching.
        designation = event.get("atc_model") or event.get("atc_type") or event.get("aircraft_title") or "UNKNOWN"

        departure_airport = self._resolve_airport(event.get("dep_lat"), event.get("dep_lon"))
        arrival_airport = self._resolve_airport(event.get("arr_lat"), event.get("arr_lon"))

        if self.db.get_aircraft(registration) is None:
            self.db.add_aircraft(registration, designation, current_location=departure_airport)

        departed_at = datetime.fromtimestamp(event["block_start_time"]).isoformat() if event.get("block_start_time") else None
        arrived_at = datetime.fromtimestamp(event["time"]).isoformat() if event.get("time") else None

        self.db.log_flight(
            flight_number=None,
            aircraft_designation=designation,
            aircraft_registration=registration,
            network="OFFLINE",
            departure_airport=departure_airport,
            arrival_airport=arrival_airport,
            distance_nm=event.get("distance_nm"),
            pax_count=None,
            cargo_kg=None,
            block_hours=event.get("block_hours"),
            departed_at=departed_at,
            arrived_at=arrived_at,
            landing_vs=event.get("landing_vs"),
            log_data=event,
        )
        return self.db.list_flights(limit=1)[0]["id"]

    def disconnect(self):
        # Wrapped defensively: if the underlying SimConnect teardown throws
        # for any reason, we still want self.connected reset to False and
        # the exception NOT to propagate up silently - an unhandled
        # exception here was killing the rest of the caller's click handler
        # partway through (e.g. home_page's Stop button appearing to do
        # nothing, because the UI updates after this call never ran).
        try:
            self.connector.disconnect()
        except Exception as e:
            print(f"[flight_session] Error during disconnect (ignored, continuing): {e}")
        finally:
            self.connected = False


if __name__ == "__main__":
    # Standalone logic test, no sim/Qt needed - drives the controller with
    # synthetic tick data the same way test_wiring2.py did, to confirm the
    # extraction didn't change behavior.
    import tempfile, os

    controller = FlightSessionController()
    test_db_path = os.path.join(tempfile.gettempdir(), "acars_session_test.db")
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
    controller.db = FlightDatabase(db_path=test_db_path)
    controller.db.init_db()
    controller.connected = True  # bypass real SimConnect for this test

    ticks = [
        {"on_ground": 1.0, "engine_running": False, "eng1_combustion": 0, "eng2_combustion": 0,
         "title": "C172SP", "atc_type": "CESSNA", "atc_model": "C172", "atc_id": "N172AB",
         "latitude": 33.9425, "longitude": -118.408, "fuel_total_weight": 150,
         "vertical_speed": 0, "g_force": 1.0, "gear_handle_position": 1, "flaps_handle_index": 0, "parking_brake": 1},
        {"on_ground": 1.0, "engine_running": True, "eng1_combustion": 1, "eng2_combustion": 0,
         "title": "C172SP", "atc_type": "CESSNA", "atc_model": "C172", "atc_id": "N172AB",
         "latitude": 33.9425, "longitude": -118.408, "fuel_total_weight": 150,
         "vertical_speed": 0, "g_force": 1.0, "gear_handle_position": 1, "flaps_handle_index": 0, "parking_brake": 0},
        {"on_ground": 0.0, "engine_running": True, "eng1_combustion": 1, "eng2_combustion": 0,
         "title": "C172SP", "atc_type": "CESSNA", "atc_model": "C172", "atc_id": "N172AB",
         "latitude": 34.0, "longitude": -118.43, "fuel_total_weight": 145,
         "vertical_speed": 500, "g_force": 1.2, "gear_handle_position": 1, "flaps_handle_index": 1, "parking_brake": 0},
        {"on_ground": 1.0, "engine_running": True, "eng1_combustion": 1, "eng2_combustion": 0,
         "title": "C172SP", "atc_type": "CESSNA", "atc_model": "C172", "atc_id": "N172AB",
         "latitude": 34.0158, "longitude": -118.4513, "fuel_total_weight": 142,
         "vertical_speed": -132.16, "g_force": 1.31, "gear_handle_position": 1, "flaps_handle_index": 0, "parking_brake": 0},
        {"on_ground": 1.0, "engine_running": False, "eng1_combustion": 0, "eng2_combustion": 0,
         "title": "C172SP", "atc_type": "CESSNA", "atc_model": "C172", "atc_id": "N172AB",
         "latitude": 34.0158, "longitude": -118.4513, "fuel_total_weight": 142,
         "vertical_speed": 0, "g_force": 1.0, "gear_handle_position": 1, "flaps_handle_index": 0, "parking_brake": 1},
    ]

    def fake_read():
        return ticks.pop(0) if ticks else None

    controller.connector.read = fake_read

    for _ in range(len(ticks)):
        result = controller.tick()
        print(f"state={result['state']} events={[e['type'] for e in result['events']]} pending_flight={result['pending_flight']}")

    print("\nBefore save - flights table should be empty:")
    print(controller.db.list_flights())

    flight_id = controller.record_pending_flight()
    print(f"\nAfter record_pending_flight() -> flight_id={flight_id}")

    print("\nFinal flights table:")
    for f in controller.db.list_flights():
        print(f"  {f['departure_airport']} -> {f['arrival_airport']}  designation={f['aircraft_designation']}")