"""
core/landing_grade.py

The landing grade: a score out of 100 and a letter (A, B, C, D, F), from
the touchdown the tracker recorded (core/flight_state.py). No Qt - the
logbook stores the result, the debrief and header show it.

Scoring: start at 100 and take off points per part. The bands below are
the whole formula - tweak them freely. Each band is (worse than, points
off): the biggest matching deduction applies. A part that wasn't recorded
(e.g. bank on a flight logged before 1.1) isn't scored - no points off,
shown as "Not recorded".

    Vertical speed   -200 fpm or better: full marks
    G at touchdown   1.3 G or less: full marks
    Bank             3 degrees or less: full marks
    Bounces          points off per extra touchdown

Pitch and touchdown speed are shown in the debrief but not scored: a
tailwheel and a nosewheel aircraft touch down at very different attitudes.

Usage:
    grade = grade_landing(vs=-164, g=1.21, bank=1.8, bounces=0)
    grade["score"], grade["letter"]    # 100, "A"
    grade["parts"]                     # per-part breakdown for the debrief
    grade = grade_flight(log_data, landing_vs)   # from a logged flight
"""

VS_BANDS = [(-200, 10), (-300, 20), (-400, 35), (-600, 50)]
G_BANDS = [(1.3, 5), (1.5, 15), (1.8, 25)]
BANK_BANDS = [(3, 5), (5, 10), (8, 20)]
POINTS_PER_BOUNCE = 10

LETTERS = [(90, "A"), (80, "B"), (70, "C"), (60, "D"), (0, "F")]

def formula_signature():
    """A fingerprint of the formula above - core/db.py re-grades every
    logged flight when it changes, so tweaking a band updates old grades."""
    return repr((VS_BANDS, G_BANDS, BANK_BANDS, POINTS_PER_BOUNCE, LETTERS))

def letter_for(score):
    """Score (0-100) -> "A", "B", "C", "D" or "F"; None for no score."""
    if score is None:
        return None
    for minimum, letter in LETTERS:
        if score >= minimum:
            return letter
    return "F"


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _vs_deduction(vs):
    points = 0
    for limit, off in VS_BANDS:
        if vs < limit:
            points = off
    return points


def _above_deduction(value, bands):
    points = 0
    for limit, off in bands:
        if value > limit:
            points = off
    return points


def grade_landing(vs, g=None, bank=None, bounces=None):
    """The grade for one landing, or None without a vertical speed.

    Returns {"score", "letter", "parts": [{"key", "label", "value",
    "text", "points_off"}, ...]}. A part whose value is None wasn't
    recorded and has points_off None."""
    vs = _number(vs)
    if vs is None:
        return None
    g, bank, bounces = _number(g), _number(bank), _number(bounces)

    parts = [
        {"key": "vs", "label": "Vertical speed", "value": vs,
         "text": f"{vs:,.0f} fpm", "points_off": _vs_deduction(vs)},
        {"key": "g", "label": "G at touchdown", "value": g,
         "text": f"{g:.2f} G" if g is not None else "Not recorded",
         "points_off": _above_deduction(g, G_BANDS) if g is not None else None},
        {"key": "bank", "label": "Bank", "value": bank,
         "text": f"{bank:.1f}°" if bank is not None else "Not recorded",
         "points_off": _above_deduction(bank, BANK_BANDS) if bank is not None else None},
        {"key": "bounces", "label": "Bounces", "value": bounces,
         "text": f"{int(bounces)}" if bounces is not None else "Not recorded",
         "points_off": int(bounces) * POINTS_PER_BOUNCE if bounces is not None else None},
    ]
    score = max(0, 100 - sum(p["points_off"] or 0 for p in parts))
    return {"score": score, "letter": letter_for(score), "parts": parts}


def grade_flight(log_data, landing_vs=None):
    """The grade for a logged flight, from its stored detail (log_data) and
    landing rate. Flights logged before 1.1 have no touchdown details, so
    they're graded on vertical speed only - their stored "landing_g" was
    the flight's peak G, not the touchdown's."""
    log_data = log_data or {}
    landing = log_data.get("landing")
    vs = landing_vs if _number(landing_vs) is not None else log_data.get("landing_vs")
    if not isinstance(landing, dict):
        return grade_landing(vs)
    return grade_landing(
        vs if _number(vs) is not None else landing.get("vs"),
        g=landing.get("g"),
        bank=landing.get("bank"),
        bounces=log_data.get("bounces"),
    )


if __name__ == "__main__":
    for args in [(-164, 1.21, 1.8, 0), (-250, 1.35, 2.0, 0), (-320, 1.6, 4.0, 1),
                 (-450, 1.9, 9.0, 2), (-90, None, None, None), (-800, 1.2, 1, 0)]:
        grade = grade_landing(*args)
        offs = ", ".join(f"{p['label']} {p['text']} (-{p['points_off']})" if p["points_off"] is not None
                         else f"{p['label']} {p['text']}" for p in grade["parts"])
        print(f"{grade['letter']} {grade['score']:>3}  {offs}")
    print("old flight:", grade_flight({"landing_g": 2.4}, -180)["letter"])
    print("no rate:", grade_flight({}, None))