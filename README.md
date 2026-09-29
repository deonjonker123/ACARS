# Tailwind ACARS

A flight-tracking and logbook app for the **Tailwind** virtual airline. Plan a flight with SimBrief, fly it in Microsoft Flight Simulator, Prepar3D or X-Plane, and Tailwind records the whole thing automatically: block times, route, landing rate and flight hours. It also tracks your rank.

Windows only. Free and open source ([AGPL-3.0](LICENSE)).

---

## Installing

1. Go to [**Releases**](https://github.com/deonjonker123/ACARS/releases) and download the latest `Tailwind-Setup-x.y.z.exe`.
2. Run it. It installs just for you, so there's no admin prompt. You can add a desktop shortcut during setup.
3. Start Tailwind. On first launch it asks you to fill in your pilot profile.

> Windows SmartScreen may warn you that the installer is from an unknown publisher, because it isn't code-signed. Click **More info → Run anyway**.

**Updating:** when a new version is out, the sidebar shows an *Update available* link. Download the new installer and run it over the top. Your logbook is kept.

**Uninstalling:** use *Settings → Apps* in Windows. The uninstaller asks whether to delete your pilot data too. The default is to keep it.

---

## Getting started

1. **Settings.** Enter your pilot name and **SimBrief Pilot ID** (SimBrief → Account Settings → Pilot ID). If you fly online, also add your VATSIM or IVAO ID. A home airport is optional.
2. **Plan** your flight on [SimBrief](https://www.simbrief.com) as usual.
3. **Dispatch.** Tailwind loads your latest SimBrief plan. Pick an aircraft from the fleet and your network (Offline, VATSIM or IVAO), then dispatch.
4. **Fly.** Start the sim. Tailwind finds and connects to it automatically, with nothing to select. Follow the flight on the **Live Map**.
5. **Land and park.** When the flight ends, it's logged in your logbook.

### Simulators

| Sim | How Tailwind connects |
|---|---|
| MSFS 2020 / 2024, Prepar3D | SimConnect, automatic |
| X-Plane 11 / 12 | UDP on port 49000 (X-Plane's default), automatic. The sim must run on the same PC. |

---

## The rules

- **Hard landings:** a touchdown harder than **-800 fpm** rejects the flight. It stays in the logbook as rejected, its hours don't count, and your rejection count goes up.
- **Landing rate** is sampled 20 times per second around touchdown, so what you see is what you actually hit.
- **Online flights:** if you dispatch on VATSIM or IVAO, you must be connected to that network for at least half of the flight for it to count as online. If the network's data feed can't be reached, or your internet drops, that time isn't held against you. Your hours are always logged either way.
- **Crashes:** if the app or the PC crashes mid-flight, Tailwind offers to **resume** the flight on next start.

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

### Releasing

1. Bump `APP_VERSION` in `version.py`.
2. Run `python build.py`.
3. On GitHub, create a release tagged `vX.Y.Z` and attach `Tailwind-Setup-X.Y.Z.exe`.

Running copies pick up the new release from its tag on their next start.

---

## Licence

Tailwind ACARS is free software under the **GNU Affero General Public License v3.0**. See [LICENSE](LICENSE). It comes with no warranty.

It's built on Qt / PySide6 (LGPL-3.0), Python-SimConnect (AGPL-3.0), MapLibre GL JS and OpenStreetMap data, among others. See [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt) for the full list.

Microsoft Flight Simulator, Prepar3D and X-Plane are trademarks of their respective owners. Tailwind ACARS isn't affiliated with or endorsed by Microsoft, Lockheed Martin, Laminar Research, Navigraph, VATSIM or IVAO.