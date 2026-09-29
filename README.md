# Tailwind ACARS

A flight-tracking and logbook app for flight simulation. Plan a flight with SimBrief, fly it in Microsoft Flight Simulator, Prepar3D or X-Plane, and Tailwind records the whole thing automatically: block times, route, altitude and speed profile, landing rate and flight hours. Every landing gets a grade, every flight gets a full debrief, and your profile builds up career stats as you go. It also tracks your rank.

Windows only. Free and open source ([AGPL-3.0](LICENSE)).

---

## Installing

1. Go to [**Releases**](https://github.com/deonjonker123/ACARS/releases) and download the latest `Tailwind-Setup-x.y.z.exe`.
2. Run it. It installs just for you, so there's no admin prompt. You can add a desktop shortcut during setup.
3. Start Tailwind. On first launch it asks you to fill in your pilot profile. Tailwind comes with a sample fleet. This can be updated or deleted and new aircraft can be added to suit your needs.

> Windows SmartScreen may warn you that the installer is from an unknown publisher, because it isn't code-signed. Click **More info → Run anyway**.

**Updating:** when a new version is out, the sidebar shows an *Update available* link. Download the new installer and run it over the top.

**Uninstalling:** use *Settings → Apps* in Windows. The uninstaller asks whether to delete your pilot data too. The default is to keep it.

---

## Getting started

1. **Settings.** Enter your pilot name and **SimBrief Pilot ID** (SimBrief → Account Settings → Pilot ID). If you fly online, also add your VATSIM or IVAO ID. A home airport is optional.
2. **Plan** your flight on [SimBrief](https://www.simbrief.com) as usual.
3. **Dispatch.** Tailwind loads your latest SimBrief plan. Pick an aircraft from the fleet and your network (Offline, VATSIM or IVAO), then dispatch.
5. **Fly.** Start the sim. Tailwind finds and connects to it automatically, with nothing to select. Follow the flight on the **Live Map**. The sidebar clock shows the current time in UTC.
6. **Park and submit.** Once you've parked and shut the engines down, the flight is complete. Choose:
   - **Submit Flight:** Tailwind checks the flight against the rules below and tells you whether it was **accepted** or **rejected**, with your landing grade. Press **View Debrief** to see how it went.
   - **Discard Flight:** throws the flight away. Nothing is logged, and nothing counts for or against you.

Changed your mind mid-flight? **Cancel Flight** stops tracking and clears the plan at any time before the flight is complete. Nothing is logged and there's no penalty.

### Simulators

| Sim | How Tailwind connects |
|---|---|
| MSFS 2020 / 2024 | SimConnect, automatic |
| Prepar3D | FSUIPC (free version is good enough, automatic. The sim must run on the same PC |
| X-Plane 11 / 12 | UDP on port 49000 (X-Plane's default), automatic. The sim must run on the same PC. |

---

## The rules

A flight is judged when you press **Submit**. It's **rejected** if:
- you parked anywhere other than the planned **destination or alternate**, or
- your hardest touchdown was harder than **-800 fpm**.

A rejected flight stays in your logbook, marked **REJECTED** with the reason and its full debrief, so you can see what went wrong. Its hours don't count toward your rank, it's left out of your averages and stats, and your rejection count goes up.

Also:
- **Landing rate** is sampled 20 times per second around touchdown, so what you see is what you actually hit. If you bounce, the hardest touchdown counts.
- **Online flights:** if you dispatch on VATSIM or IVAO, you must be connected to that network for at least half of the flight for it to count as online. If the network's data feed can't be reached, or your internet drops, that time isn't held against you. Your hours are always logged either way.
- **Crashes:** if the app, simulator or PC crashes mid-flight, Tailwind offers to **resume** the flight on next start. That includes a flight you'd landed but not yet submitted.

### Landing grade

Every landing is scored out of 100 and given a letter grade. You start at 100 and lose points for:

| | Full marks | Points off |
|---|---|---|
| Vertical speed | 0 to -200 fpm | -200 to -300: 10 · -300 to -400: 20 · -400 to -600: 35 · -600 to -800: 50 |
| G at touchdown | 1.3 G or less | up to 1.5 G: 5 · up to 1.8 G: 15 · over 1.8 G: 25 |
| Bank at touchdown | 3° or less | up to 5°: 5 · up to 8°: 10 · over 8°: 20 |
| Bounces | one touchdown | 10 per bounce |

**A** 90+ · **B** 80–89 · **C** 70–79 · **D** 60–69 · **F** below 60

Pitch and touchdown speed are shown in the debrief but not graded, because tailwheel and nosewheel aircraft land at very different attitudes. Your average grade and landing rate are shown in the header, e.g. **B · -164 fpm**.

### Ranks

Hours flown unlock ranks, and each rank unlocks more of the fleet.

| Rank | Hours |
|---|---|
| Student Pilot | 0 |
| Second Officer | 21 |
| First Officer | 51 |
| Captain | 101 |
| Senior Captain | 251 |
| ATP First Officer | 501 |
| ATP Captain | 1001 |
| ATP Senior Captain | 1501 |
| Executive Command Officer | 3000 |

---

## After the flight

### Debrief
Every submitted flight has a debrief. Open it from the popup after Submit, from **View** in the Logbook, or by double-clicking a flight in the Dashboard's history. It shows:
- **Header:** the ACCEPTED or REJECTED badge (with the reason), the flight, aircraft, date, network and landing grade.
- **Route:** the route you flew on the map, against the planned SimBrief route.
- **Flight profile:** altitude, IAS and ground speed over the whole flight.
- **Landing:** the grade's breakdown, showing where you lost points, plus pitch, touchdown speed and how many times you touched down.
- **Plan vs actual:** block time, fuel burned, distance and cruise altitude, against your SimBrief plan.
- **Timeline:** block out, takeoff, gear and flap changes (gear extended over its speed limit is flagged), touchdowns and block in, in UTC.

### Profile
Your career in numbers:
- **Totals:** flights, hours, distance, passengers, cargo and rejections.
- **Aircraft:** hours by type, your favourite type and your most-flown airframe.
- **Airports:** your most visited, plus how many airports, countries and continents you've flown to.
- **Landings:** landing rates and grades, and your trend over the last 20 flights.
- **Flight durations**, grouped 0–2, 2–4, 4–8, 8–16 and 16+ hours.
- **Records:** longest flight by distance and by time, and your softest landing.
- **Networks:** Offline vs VATSIM vs IVAO.
- **Activity:** flights per month.

Only accepted flights count toward your stats.

> Flights logged before v1.1 didn't record a track, timeline or touchdown details. Their debrief shows what there is, and they're graded on landing rate alone.

---

## Your data

Everything that's yours lives in:

```
%LOCALAPPDATA%\Tailwind ACARS
```

That includes the logbook database, fleet, aircraft photos and the log file. It's separate from the program, so reinstalling or updating never touches it. To start over as a new pilot, close Tailwind and delete `acars.db` from that folder. The About box (click the version number in the sidebar) has an **Open Data Folder** button.

If something goes wrong, `acars.log` in that folder is the first thing to send.

---

## Building from source

You need Python 3.11+ on Windows.

```bat
git clone https://github.com/deonjonker123/ACARS.git
cd ACARS
python -m venv .venv
.venv\Scripts\activate
pip install PySide6 SimConnect pyinstaller
```

Run it straight from source:

```bat
python main.py
```

Build the app and installer:

```bat
python build.py
```

- `dist\Tailwind\` holds the app (`Tailwind.exe` plus `_internal\`, which always go together).
- `dist\Tailwind-Setup-<version>.exe` is the installer. It's only built if [Inno Setup](https://jrsoftware.org/isdl.php) is installed.

Without the `SimConnect` package, the build supports X-Plane only.

The landing grade's numbers all live at the top of `core/landing_grade.py`. Change them there, and every logged flight is re-graded the next time Tailwind starts.

## Licence

Tailwind ACARS is free software under the **GNU Affero General Public License v3.0**. See [LICENSE](LICENSE). It comes with no warranty.

It's built on Qt / PySide6 (LGPL-3.0), Python-SimConnect (AGPL-3.0), MapLibre GL JS and OpenStreetMap data, among others. See [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt) for the full list.

Microsoft Flight Simulator, Prepar3D and X-Plane are trademarks of their respective owners. Tailwind ACARS isn't affiliated with or endorsed by Microsoft, Lockheed Martin, Laminar Research, Navigraph, VATSIM or IVAO.
