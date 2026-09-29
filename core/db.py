"""
core/db.py

SQLite persistence layer for the ACARS app.

Tables:
  - aircraft: the fleet. One row per airframe, identified by its unique
              registration (e.g. N482TW). Holds the aircraft name (stored
              in the `designation` column, e.g. "C172"), categories, the
              rank required to fly it, the airframe's own hours/distance,
              and an image. Several airframes can share a name (three
              C172s = three rows), but never a registration.
              Deleting an aircraft only RETIRES it (retired = 1): it leaves
              the fleet and can't be flown again, but the row stays so its
              logbook flights keep a valid registration to point at, and
              the registration can never be reused by a different plane.
  - pilot:    single-row profile. rank/rank_badge are DERIVED from
              total_hours_flown (via core.pilot.PilotProgress) and cached
              here after each flight, not set directly. Pilot stats are
              never touched by aircraft add/edit/delete.
  - flights:  the logbook. Summary columns plus a log_data JSON blob
              holding the full recorded flight.
  - app_meta: small key/value store for one-time flags (e.g. whether the
              fleet has been seeded from fleet_and_ranks.json yet).

Airframe hours (aircraft.hours_flown) and pilot hours
(pilot.total_hours_flown) are separate counters: both go up when a flight
is saved, but neither is ever derived from the other.

Usage:
    db = FlightDatabase()
    db.init_db()   # also seeds the fleet from fleet_and_ranks.json, once
    db.add_aircraft("N172AB", "C172", categories=["prop"], unlock_rank="student_pilot")
    db.save_block_reason("N172AB")   # None if saveable, else a message why not
    db.log_flight(...)
"""

import sqlite3
import json
import os
import random
import re
from contextlib import contextmanager

from core.pilot import PilotProgress, load_fleet_data
from core.paths import DB_PATH

_DB_PATH = DB_PATH

VALID_NETWORKS = {"VATSIM", "IVAO", "OFFLINE"}
VALID_CATEGORIES = ("prop", "airliner", "bizjet", "cargo")
COMPANY_REG_SUFFIX = "TW"
_REG_PATTERN = re.compile(r"^[A-Z0-9-]{2,10}$")


def normalize_registration(registration):
    """Uppercases and strips a registration so 'n482tw ' and 'N482TW'
    are treated as the same plane everywhere (DB, sim ATC ID, UI)."""
    return (registration or "").strip().upper()


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

            self._ensure_column(conn, "aircraft", "categories", "TEXT")
            self._ensure_column(conn, "aircraft", "unlock_rank", "TEXT")
            self._ensure_column(conn, "aircraft", "retired", "INTEGER NOT NULL DEFAULT 0")

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

            self._ensure_column(conn, "pilot", "name", "TEXT")
            self._ensure_column(conn, "pilot", "simbrief_id", "TEXT")
            self._ensure_column(conn, "pilot", "vatsim_id", "TEXT")
            self._ensure_column(conn, "pilot", "ivao_id", "TEXT")
            self._ensure_column(conn, "pilot", "home_airport", "TEXT")

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

            self._ensure_column(conn, "flights", "landing_vs", "REAL")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

            conn.execute("""
                INSERT OR IGNORE INTO pilot (id, rank, total_hours_flown)
                VALUES (1, 'Student Pilot', 0)
            """)

            self._seed_fleet_once(conn)

    def _seed_fleet_once(self, conn):
        """First run only: turns every aircraft in fleet_and_ranks.json into
        a fleet airframe with a generated Tailwind registration. Guarded by
        an app_meta flag so deleting aircraft later never brings them back."""
        seeded = conn.execute(
            "SELECT value FROM app_meta WHERE key = 'fleet_seeded'"
        ).fetchone()
        if seeded is not None:
            return

        _, fleet = load_fleet_data()
        for a in fleet:
            registration = self._generate_registration(conn)
            conn.execute("""
                INSERT INTO aircraft (registration, designation, categories, unlock_rank)
                VALUES (?, ?, ?, ?)
            """, (registration, a["name"], ",".join(a["categories"]), a["unlock_rank"]))

        conn.execute("INSERT INTO app_meta (key, value) VALUES ('fleet_seeded', '1')")

    def _generate_registration(self, conn):
        """Random unused Tailwind registration (N1TW .. N999TW). Checks
        against every registration ever used, including retired aircraft."""
        taken = {r["registration"] for r in conn.execute("SELECT registration FROM aircraft")}
        free = [
            f"N{n}{COMPANY_REG_SUFFIX}" for n in range(1, 1000)
            if f"N{n}{COMPANY_REG_SUFFIX}" not in taken
        ]
        if not free:
            raise ValueError("No free Tailwind registrations left (N1TW-N999TW all used)")
        return random.choice(free)

    def generate_registration(self):
        """Public version for the Add Aircraft dialog's pre-filled reg."""
        with self._connect() as conn:
            return self._generate_registration(conn)

    def _validate_aircraft_fields(self, registration, designation, categories, unlock_rank):
        """Raises ValueError with a user-readable message if anything is off.
        Returns the cleaned (registration, designation, categories_text)."""
        registration = normalize_registration(registration)
        designation = (designation or "").strip()

        if not _REG_PATTERN.match(registration):
            raise ValueError("Registration must be 2-10 characters: letters, digits or '-'.")
        if not designation:
            raise ValueError("Aircraft name is required.")

        categories = [c.strip().lower() for c in (categories or []) if c and c.strip()]
        if not categories:
            raise ValueError("Pick at least one category.")
        unknown = [c for c in categories if c not in VALID_CATEGORIES]
        if unknown:
            raise ValueError(f"Unknown category: {', '.join(unknown)}")

        categories = [c for c in VALID_CATEGORIES if c in categories]

        ranks, _ = load_fleet_data()
        if unlock_rank not in {r["id"] for r in ranks}:
            raise ValueError("Pick a valid unlock rank.")

        return registration, designation, ",".join(categories)

    def _registration_taken(self, conn, registration):
        return conn.execute(
            "SELECT 1 FROM aircraft WHERE registration = ?", (registration,)
        ).fetchone() is not None

    @staticmethod
    def _aircraft_dict(row):
        """Row -> dict, with categories split into a list for the UI."""
        d = dict(row)
        d["categories"] = [c for c in (d.get("categories") or "").split(",") if c]
        return d

    def add_aircraft(self, registration, designation, categories, unlock_rank,
                     current_location=None, image_path=None):
        """Adds a new airframe to the fleet. Raises ValueError if the
        registration is already used (by any aircraft, retired included)."""
        registration, designation, categories_text = self._validate_aircraft_fields(
            registration, designation, categories, unlock_rank
        )
        with self._connect() as conn:
            if self._registration_taken(conn, registration):
                raise ValueError(f"Registration {registration} is already in use.")
            conn.execute("""
                INSERT INTO aircraft (registration, designation, categories, unlock_rank,
                                      current_location, image_path)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (registration, designation, categories_text, unlock_rank,
                  current_location, image_path))
        return registration

    def update_aircraft(self, current_registration, registration, designation,
                        categories, unlock_rank, image_path=None):
        """Edits an active airframe. If the registration changes, its logbook
        flights are moved to the new registration too, so the airframe keeps
        its history. Airframe hours/distance/location are left untouched.
        Returns the (possibly new) registration."""
        current_registration = normalize_registration(current_registration)
        registration, designation, categories_text = self._validate_aircraft_fields(
            registration, designation, categories, unlock_rank
        )
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM aircraft WHERE registration = ? AND retired = 0",
                (current_registration,)
            ).fetchone()
            if existing is None:
                raise ValueError(f"Aircraft {current_registration} not found.")

            if registration != current_registration:
                if self._registration_taken(conn, registration):
                    raise ValueError(f"Registration {registration} is already in use.")
                conn.execute("PRAGMA defer_foreign_keys = ON")
                conn.execute(
                    "UPDATE flights SET aircraft_registration = ? WHERE aircraft_registration = ?",
                    (registration, current_registration)
                )

            conn.execute("""
                UPDATE aircraft
                SET registration = ?, designation = ?, categories = ?,
                    unlock_rank = ?, image_path = ?
                WHERE id = ?
            """, (registration, designation, categories_text, unlock_rank,
                  image_path, existing["id"]))
        return registration

    def delete_aircraft(self, registration):
        """Removes an airframe from the fleet by retiring it. Its logbook
        flights stay exactly as recorded, pilot stats are untouched, and the
        registration stays reserved so it can't be reused."""
        registration = normalize_registration(registration)
        with self._connect() as conn:
            conn.execute(
                "UPDATE aircraft SET retired = 1 WHERE registration = ?", (registration,)
            )

    def get_aircraft(self, registration, include_retired=False):
        registration = normalize_registration(registration)
        query = "SELECT * FROM aircraft WHERE registration = ?"
        if not include_retired:
            query += " AND retired = 0"
        with self._connect() as conn:
            row = conn.execute(query, (registration,)).fetchone()
            return self._aircraft_dict(row) if row else None

    def list_aircraft(self):
        """Active fleet only (retired aircraft are hidden)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM aircraft WHERE retired = 0 ORDER BY designation, registration"
            ).fetchall()
            return [self._aircraft_dict(r) for r in rows]

    def save_block_reason(self, registration):
        """Returns None if a flight in this aircraft may be saved, otherwise a
        user-readable reason why not: the registration isn't in the active
        fleet, or the pilot isn't rated for it yet. Aircraft with no unlock
        rank set (old auto-created rows) are treated as Student Pilot."""
        registration = normalize_registration(registration)
        aircraft = self.get_aircraft(registration)
        if aircraft is None:
            return (f"{registration or 'This aircraft'} is not in the fleet. "
                    f"Add it on the Aircraft page, then save again.")

        pilot = self.get_pilot()
        progress = PilotProgress(pilot["total_hours_flown"])
        rank_by_id = {r["id"]: r for r in progress.ranks}
        required = rank_by_id.get(aircraft["unlock_rank"] or "student_pilot")
        if required is not None and progress.total_hours < required["min_hours"]:
            return (f"You are not rated for {aircraft['designation']} ({registration}). "
                    f"It unlocks at {required['name']}.")
        return None

    def _update_aircraft_after_flight(self, conn, registration, hours_delta, distance_delta, new_location):
        conn.execute("""
            UPDATE aircraft
            SET hours_flown = hours_flown + ?,
                distance_flown = distance_flown + ?,
                current_location = COALESCE(?, current_location)
            WHERE registration = ?
        """, (hours_delta, distance_delta, new_location, registration))

    def get_pilot(self):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM pilot WHERE id = 1").fetchone()
            return dict(row) if row else None

    def set_pilot_name(self, name):
        """Sets just the name. The Settings page uses update_pilot_profile()."""
        with self._connect() as conn:
            conn.execute("UPDATE pilot SET name = ? WHERE id = 1", (name,))

    def update_pilot_profile(self, name, simbrief_id=None, vatsim_id=None,
                             ivao_id=None, home_airport=None):
        """Saves the Settings page fields. Raises ValueError with a
        user-readable message if anything is invalid (nothing is saved then).

        - name: required.
        - simbrief_id / vatsim_id / ivao_id: optional, digits only.
        - home_airport: optional, 4-character ICAO code (letters/digits),
          stored uppercase. Whether it's a REAL airport is checked by the
          caller against airports.csv - this only checks the format.
        Pilot stats (hours, rank, flights, landing rate) are never touched.
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("Pilot name is required.")

        cleaned_ids = {}
        for label, value in (("SimBrief Pilot ID", simbrief_id),
                             ("VATSIM ID", vatsim_id),
                             ("IVAO ID", ivao_id)):
            value = (value or "").strip()
            if value and not value.isdigit():
                raise ValueError(f"{label} must contain digits only.")
            cleaned_ids[label] = value or None

        home_airport = (home_airport or "").strip().upper()
        if home_airport and not re.fullmatch(r"[A-Z0-9]{4}", home_airport):
            raise ValueError("Home airport must be a 4-character ICAO code, e.g. KLAX.")

        with self._connect() as conn:
            conn.execute("""
                UPDATE pilot
                SET name = ?, simbrief_id = ?, vatsim_id = ?, ivao_id = ?, home_airport = ?
                WHERE id = 1
            """, (name, cleaned_ids["SimBrief Pilot ID"], cleaned_ids["VATSIM ID"],
                  cleaned_ids["IVAO ID"], home_airport or None))

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

    def log_flight(self, flight_number, aircraft_designation, aircraft_registration,
                    network, departure_airport, arrival_airport, distance_nm,
                    pax_count, cargo_kg, block_hours,
                    departed_at=None, arrived_at=None, landing_vs=None, log_data=None):
        """
        Records a completed flight and updates pilot + aircraft stats in one
        transaction. `log_data` can be any JSON-serializable dict (the full
        recorded flight detail) - stored as a blob, retrievable via
        get_flight_log().

        Refuses (ValueError) if save_block_reason() says this aircraft can't
        be saved - not in the active fleet, or pilot not rated for it.

        departed_at/arrived_at: ISO timestamp strings for the actual block
        start/end. Optional - if omitted, only block_hours (the duration)
        is available, not the actual clock times.
        """
        if network not in VALID_NETWORKS:
            raise ValueError(f"network must be one of {VALID_NETWORKS}, got {network!r}")

        aircraft_registration = normalize_registration(aircraft_registration)
        reason = self.save_block_reason(aircraft_registration)
        if reason is not None:
            raise ValueError(reason)

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
    import tempfile

    test_db_path = os.path.join(tempfile.gettempdir(), "acars_test.db")
    if os.path.exists(test_db_path):
        os.remove(test_db_path)

    db = FlightDatabase(db_path=test_db_path)
    db.init_db()
    db.init_db()
    print("Seeded fleet size:", len(db.list_aircraft()))
    print("Sample:", db.list_aircraft()[0])

    db.add_aircraft("n172ab", "C172", categories=["prop"], unlock_rank="student_pilot",
                    current_location="KLAX")
    print("Added:", db.get_aircraft("N172AB"))

    print("Save check, unknown reg:", db.save_block_reason("N999ZZ"))
    b350 = next(a for a in db.list_aircraft() if a["designation"] == "B350")
    print("Save check, locked B350:", db.save_block_reason(b350["registration"]))
    print("Save check, C172:", db.save_block_reason("N172AB"))

    db.log_flight(
        flight_number="TW001",
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

    db.update_aircraft("N172AB", "N172TW", "C172", ["prop"], "student_pilot")
    print("After reg change:", db.get_aircraft("N172TW"))
    print("Flight follows reg:", db.list_flights()[0]["aircraft_registration"])

    pilot_before = db.get_pilot()
    db.delete_aircraft("N172TW")
    print("Still in fleet after delete:", db.get_aircraft("N172TW"))
    print("Flight kept after delete:", len(db.list_flights()) == 1)
    print("Pilot untouched after delete:", db.get_pilot() == pilot_before)
    print("Save check, deleted aircraft:", db.save_block_reason("N172TW"))
    try:
        db.add_aircraft("N172TW", "C172", ["prop"], "student_pilot")
    except ValueError as e:
        print("Reusing deleted reg refused:", e)

    db.update_pilot_profile("Test Pilot", simbrief_id="123456", vatsim_id="1234567",
                            ivao_id="", home_airport="klax")
    print("Profile saved:", {k: db.get_pilot()[k] for k in
                             ("name", "simbrief_id", "vatsim_id", "ivao_id", "home_airport")})
    for bad in ({"name": ""}, {"name": "X", "vatsim_id": "12a"}, {"name": "X", "home_airport": "LAX"}):
        try:
            db.update_pilot_profile(**bad)
        except ValueError as e:
            print("Profile refused:", e)
    print("Pilot stats untouched by profile save:", db.get_pilot()["total_hours_flown"] == pilot_before["total_hours_flown"])