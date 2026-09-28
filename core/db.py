"""
core/db.py

SQLite persistence layer for the ACARS app.

Three tables:
  - aircraft: individual tail-number instances (registration-specific),
              distinct from aircraft TYPES defined in fleet_and_ranks.json
              (types govern unlock rules; instances accumulate hours/location).
  - pilot:    single-row profile. rank/rank_badge are DERIVED from
              total_hours_flown (via core.pilot.PilotProgress) and cached
              here after each flight, not set directly.
  - flights:  the logbook. Summary columns per your spec, plus a log_data
              JSON blob holding the full recorded flight (block times,
              dep/arr, landing stats, events) - this is what "view flight
              log as recorded" points at.

Usage:
    db = FlightDatabase()
    db.init_db()
    db.add_aircraft("N172AB", "C172", current_location="KLAX")
    db.log_flight(
        flight_number="ACA001",
        aircraft_designation="C172",
        aircraft_registration="N172AB",
        network="OFFLINE",
        distance_nm=11.8,
        pax_count=1,
        cargo_kg=0,
        block_hours=0.18,
        arrival_airport="KSMO",
        landing_vs=-132.0,
        log_data={"dep": "KLAX", "arr": "KSMO", "events": [...]},
    )
    db.get_pilot()
"""

import sqlite3
import json
import os
from contextlib import contextmanager

from core.pilot import PilotProgress

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "acars.db"
)

VALID_NETWORKS = {"VATSIM", "IVAO", "OFFLINE"}


class FlightDatabase:
    def __init__(self, db_path=_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------------- schema setup ----------------

    def _ensure_column(self, conn, table, column, col_type):
        """Adds `column` to `table` if it doesn't already exist - a minimal
        migration helper so schema changes don't require deleting real data."""
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

    def init_db(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS aircraft (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    registration TEXT UNIQUE NOT NULL,
                    designation TEXT NOT NULL,
                    current_location TEXT,
                    hours_flown REAL NOT NULL DEFAULT 0,
                    distance_flown REAL NOT NULL DEFAULT 0,
                    image_path TEXT
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS pilot (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    name TEXT,
                    rank TEXT NOT NULL DEFAULT 'Student Pilot',
                    rank_badge_path TEXT,
                    total_completed INTEGER NOT NULL DEFAULT 0,
                    total_rejected INTEGER NOT NULL DEFAULT 0,
                    total_hours_flown REAL NOT NULL DEFAULT 0,
                    average_landing_rate REAL,
                    current_location TEXT
                )
            """)

            # Migration: if 'pilot' already existed before 'name' was added
            # (e.g. your existing data/acars.db from earlier testing),
            # CREATE TABLE IF NOT EXISTS above is a no-op and won't add the
            # column. Add it here if it's missing, rather than requiring
            # you to delete real logged flight data to pick up schema changes.
            self._ensure_column(conn, "pilot", "name", "TEXT")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS flights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flight_number TEXT,
                    aircraft_designation TEXT NOT NULL,
                    aircraft_registration TEXT NOT NULL,
                    network TEXT NOT NULL DEFAULT 'OFFLINE',
                    departure_airport TEXT NOT NULL,
                    arrival_airport TEXT NOT NULL,
                    departed_at TEXT,
                    arrived_at TEXT,
                    distance_nm REAL,
                    pax_count INTEGER,
                    cargo_kg REAL,
                    block_hours REAL NOT NULL,
                    landing_vs REAL,
                    logged_at TEXT NOT NULL DEFAULT (datetime('now')),
                    log_data TEXT,
                    FOREIGN KEY (aircraft_registration) REFERENCES aircraft(registration)
                )
            """)

            # Migration: landing_vs was captured all along but only ever used
            # for the pilot's running average - never actually stored on the
            # flight row itself, so it couldn't be shown/sorted per-flight.
            # Existing rows (logged before this fix) will show NULL here,
            # even though the value technically exists inside their log_data
            # blob - not backfilled automatically, flagging that rather than
            # silently pretending old rows have it.
            self._ensure_column(conn, "flights", "landing_vs", "REAL")

            # Ensure the single pilot row exists
            conn.execute("""
                INSERT OR IGNORE INTO pilot (id, rank, total_hours_flown)
                VALUES (1, 'Student Pilot', 0)
            """)

    # ---------------- aircraft ----------------

    def add_aircraft(self, registration, designation, current_location=None, image_path=None):
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO aircraft (registration, designation, current_location, image_path)
                VALUES (?, ?, ?, ?)
            """, (registration, designation, current_location, image_path))

    def get_aircraft(self, registration):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM aircraft WHERE registration = ?", (registration,)
            ).fetchone()
            return dict(row) if row else None

    def list_aircraft(self):
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM aircraft ORDER BY designation, registration").fetchall()
            return [dict(r) for r in rows]

    def _update_aircraft_after_flight(self, conn, registration, hours_delta, distance_delta, new_location):
        conn.execute("""
            UPDATE aircraft
            SET hours_flown = hours_flown + ?,
                distance_flown = distance_flown + ?,
                current_location = COALESCE(?, current_location)
            WHERE registration = ?
        """, (hours_delta, distance_delta, new_location, registration))

    # ---------------- pilot ----------------

    def get_pilot(self):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM pilot WHERE id = 1").fetchone()
            return dict(row) if row else None

    def set_pilot_name(self, name):
        """No settings screen exists yet to do this from the UI - call this
        once directly (e.g. from a scratch script or the console) to set
        your pilot name until a proper settings page exists."""
        with self._connect() as conn:
            conn.execute("UPDATE pilot SET name = ? WHERE id = 1", (name,))

    def _sync_pilot_rank(self, conn, total_hours):
        """Recompute rank/badge from total hours via PilotProgress and cache them."""
        progress = PilotProgress(total_hours)
        rank = progress.current_rank
        badge_path = f"badges/{rank['id']}.png"
        conn.execute("""
            UPDATE pilot SET rank = ?, rank_badge_path = ? WHERE id = 1
        """, (rank["name"], badge_path))
        return progress

    def reject_flight(self):
        """Call when a dispatched flight is abandoned/rejected rather than completed."""
        with self._connect() as conn:
            conn.execute("UPDATE pilot SET total_rejected = total_rejected + 1 WHERE id = 1")

    # ---------------- flights (logbook) ----------------

    def log_flight(self, flight_number, aircraft_designation, aircraft_registration,
                    network, departure_airport, arrival_airport, distance_nm,
                    pax_count, cargo_kg, block_hours,
                    departed_at=None, arrived_at=None, landing_vs=None, log_data=None):
        """
        Records a completed flight and updates pilot + aircraft stats in one
        transaction. `log_data` can be any JSON-serializable dict (the full
        recorded flight detail) - stored as a blob, retrievable via
        get_flight_log().

        departed_at/arrived_at: ISO timestamp strings for the actual block
        start/end. Optional - if omitted, only block_hours (the duration)
        is available, not the actual clock times.
        """
        if network not in VALID_NETWORKS:
            raise ValueError(f"network must be one of {VALID_NETWORKS}, got {network!r}")

        with self._connect() as conn:
            conn.execute("""
                INSERT INTO flights (
                    flight_number, aircraft_designation, aircraft_registration,
                    network, departure_airport, arrival_airport, departed_at, arrived_at,
                    distance_nm, pax_count, cargo_kg, block_hours, landing_vs, log_data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                flight_number, aircraft_designation, aircraft_registration,
                network, departure_airport, arrival_airport, departed_at, arrived_at,
                distance_nm, pax_count, cargo_kg, block_hours, landing_vs,
                json.dumps(log_data) if log_data is not None else None,
            ))

            self._update_aircraft_after_flight(
                conn, aircraft_registration, block_hours, distance_nm or 0, arrival_airport
            )

            pilot = conn.execute("SELECT * FROM pilot WHERE id = 1").fetchone()
            new_total_hours = pilot["total_hours_flown"] + block_hours
            new_completed = pilot["total_completed"] + 1

            # Incremental running average for landing rate, skipped if no VS captured
            new_avg_landing = pilot["average_landing_rate"]
            if landing_vs is not None:
                if new_avg_landing is None:
                    new_avg_landing = landing_vs
                else:
                    new_avg_landing = new_avg_landing + (landing_vs - new_avg_landing) / new_completed

            conn.execute("""
                UPDATE pilot
                SET total_hours_flown = ?,
                    total_completed = ?,
                    average_landing_rate = ?,
                    current_location = COALESCE(?, current_location)
                WHERE id = 1
            """, (new_total_hours, new_completed, new_avg_landing, arrival_airport))

            self._sync_pilot_rank(conn, new_total_hours)

    def list_flights(self, limit=50):
        with self._connect() as conn:
            # ORDER BY logged_at alone isn't reliable: SQLite's datetime('now')
            # only has 1-second resolution, so two flights logged within the
            # same second can tie and sort unpredictably. id DESC as a
            # tiebreaker guarantees insertion order regardless.
            rows = conn.execute(
                "SELECT * FROM flights ORDER BY logged_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_flight_log(self, flight_id):
        """Returns the full recorded detail (log_data) for one flight, parsed from JSON."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT log_data FROM flights WHERE id = ?", (flight_id,)
            ).fetchone()
            if row is None or row["log_data"] is None:
                return None
            return json.loads(row["log_data"])


if __name__ == "__main__":
    # Quick manual sanity check - exercises the full flow end to end.
    # Uses a proper cross-platform temp path (not a hardcoded /tmp/... which
    # resolves incorrectly on Windows) and cleans up any previous run first,
    # so this is always safe to re-run without hitting UNIQUE constraint errors.
    import tempfile

    test_db_path = os.path.join(tempfile.gettempdir(), "acars_test.db")
    if os.path.exists(test_db_path):
        os.remove(test_db_path)

    db = FlightDatabase(db_path=test_db_path)
    db.init_db()
    db.add_aircraft("N172AB", "C172", current_location="KLAX")

    print("Pilot before flight:", db.get_pilot())

    db.log_flight(
        flight_number="ACA001",
        aircraft_designation="C172",
        aircraft_registration="N172AB",
        network="OFFLINE",
        departure_airport="KLAX",
        arrival_airport="KSMO",
        departed_at="2026-09-28T15:20:00",
        arrived_at="2026-09-28T15:29:24",
        distance_nm=11.8,
        pax_count=1,
        cargo_kg=0,
        block_hours=0.1567,
        landing_vs=-132.16,
        log_data={"note": "test flight"},
    )

    print("Pilot after flight:", db.get_pilot())
    print("Aircraft after flight:", db.get_aircraft("N172AB"))
    print("Flights:", db.list_flights())
    flight_id = db.list_flights()[0]["id"]
    print("Flight log detail:", db.get_flight_log(flight_id))