"""
core/pilot.py

Pure progression logic: given a pilot's total hours, determines their
current rank and which aircraft are unlocked. Reads from
data/fleet_and_ranks.json - no database, no UI, no sim connection.

This is deliberately hours-in, answers-out. The DB layer (next) is what
will supply the real "total_hours" number; for now this can be tested
standalone with any number you like.

Usage:
    progress = PilotProgress(total_hours=137)
    progress.current_rank          # -> rank dict
    progress.next_rank             # -> rank dict, or None if at the top
    progress.hours_to_next_rank    # -> float, or None if at the top
    progress.unlocked_aircraft     # -> list of aircraft dicts
    progress.locked_aircraft       # -> list of (aircraft dict, rank dict) needed to unlock it
"""

import json
import os

_DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "fleet_and_ranks.json"
)


def load_fleet_data(path=_DATA_PATH):
    """Load and lightly validate the fleet/ranks JSON file."""
    with open(path, "r") as f:
        data = json.load(f)

    ranks = sorted(data["ranks"], key=lambda r: r["order"])
    aircraft = data["aircraft"]

    rank_ids = {r["id"] for r in ranks}
    for a in aircraft:
        if a["unlock_rank"] not in rank_ids:
            raise ValueError(f"Aircraft '{a['name']}' references unknown rank '{a['unlock_rank']}'")

    return ranks, aircraft


class PilotProgress:
    def __init__(self, total_hours, data_path=_DATA_PATH):
        self.total_hours = total_hours
        self.ranks, self.aircraft = load_fleet_data(data_path)

        self.current_rank = self._compute_current_rank()
        self.next_rank = self._compute_next_rank()
        self.hours_to_next_rank = (
            round(self.next_rank["min_hours"] - self.total_hours, 2)
            if self.next_rank else None
        )

        self.unlocked_aircraft = self._compute_unlocked()
        self.locked_aircraft = self._compute_locked()

    # ---------------- internal computation ----------------

    def _compute_current_rank(self):
        # Ranks are sorted ascending by min_hours - the current rank is the
        # highest one whose threshold we've met or passed.
        current = self.ranks[0]
        for rank in self.ranks:
            if self.total_hours >= rank["min_hours"]:
                current = rank
            else:
                break
        return current

    def _compute_next_rank(self):
        for rank in self.ranks:
            if rank["min_hours"] > self.total_hours:
                return rank
        return None  # already at the top rank

    def _compute_unlocked(self):
        unlocked_rank_ids = {
            r["id"] for r in self.ranks if self.total_hours >= r["min_hours"]
        }
        return [a for a in self.aircraft if a["unlock_rank"] in unlocked_rank_ids]

    def _compute_locked(self):
        unlocked_rank_ids = {
            r["id"] for r in self.ranks if self.total_hours >= r["min_hours"]
        }
        rank_by_id = {r["id"]: r for r in self.ranks}
        locked = []
        for a in self.aircraft:
            if a["unlock_rank"] not in unlocked_rank_ids:
                locked.append((a, rank_by_id[a["unlock_rank"]]))
        return locked

    # ---------------- convenience lookups ----------------

    def is_unlocked(self, aircraft_id):
        """Check whether a specific aircraft (by id) is unlocked at current hours."""
        return any(a["id"] == aircraft_id for a in self.unlocked_aircraft)

    def unlocked_by_category(self, category):
        """Filter unlocked aircraft to one category, e.g. 'prop', 'airliner', 'bizjet', 'cargo'."""
        return [a for a in self.unlocked_aircraft if category in a["categories"]]

    def summary(self):
        """A plain-dict snapshot, handy for printing or feeding straight to a UI."""
        return {
            "total_hours": self.total_hours,
            "current_rank": self.current_rank["name"],
            "next_rank": self.next_rank["name"] if self.next_rank else None,
            "hours_to_next_rank": self.hours_to_next_rank,
            "unlocked_count": len(self.unlocked_aircraft),
            "locked_count": len(self.locked_aircraft),
        }