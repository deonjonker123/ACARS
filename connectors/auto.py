"""
Auto connector: uses whichever sim is running - MSFS / P3D (SimConnect,
connectors/msfs.py) or X-Plane (connectors/xplane.py) - with nothing to
set up. Same interface as the individual connectors, so
core/flight_session.py just uses this one.

While no sim is connected, each connect() call (the app retries every
poll) looks for:
  1. X-Plane    - (re)sends its UDP subscriptions and checks whether it has
                  answered; never waits for it
  2. MSFS / P3D - tries to open SimConnect (fails fast if neither is running)
The first one found is used.

Opening SimConnect isn't proof MSFS / P3D is running - other software can
accept the connection too, and then no data ever comes. So until the
SimConnect connection has sent real data, X-Plane is still checked on
every read() and takes over as soon as it answers.

When the connected sim goes away the connector drops it and goes back to
searching, so switching sims needs no app restart:
  - X-Plane: its read() raises once nothing has arrived for a few seconds.
  - MSFS / P3D: SimConnect doesn't always report a closed sim, so a
    SimConnect connection that has returned no data for NO_DATA_DROP_S is
    dropped too (if MSFS is still running it's simply picked up again).

SIM_NAME is the connected sim's name, or "Searching for a running sim..."

Usage:
    connector = AutoConnector()
    connector.connect()       # raises ConnectionError while no sim is found
    data = connector.read()   # like the individual connectors; raises
                              # ConnectionError when the sim went away
    connector.disconnect()
"""

import time

from connectors.xplane import XPlaneConnector

NO_DATA_DROP_S = 10.0
SEARCHING = "Searching for a running sim..."


class AutoConnector:
    def __init__(self):
        self._xplane = XPlaneConnector()
        self._msfs = None
        self._msfs_unavailable = None
        self._active = None
        self._last_data = None
        self._msfs_proven = False

    @property
    def SIM_NAME(self):
        return self._active.SIM_NAME if self._active is not None else SEARCHING

    def connect(self):
        if self._active is not None:
            return

        if self._try_xplane():
            return

        msfs = self._msfs_connector()
        if msfs is not None:
            try:
                msfs.connect()
            except Exception:
                self._quietly_disconnect(msfs)
            else:
                self._use(msfs)
                return

        raise ConnectionError("No sim found - looking for MSFS / P3D and X-Plane.")

    def read(self):
        if self._active is None:
            raise ConnectionError("Not connected to a sim.")
        try:
            data = self._active.read()
        except Exception:
            self._drop()
            raise

        now = time.monotonic()
        if data is not None:
            self._last_data = now
            if self._active is self._msfs:
                self._msfs_proven = True
        elif self._active is self._msfs and not self._msfs_proven and self._try_xplane():
            return self._xplane.read()
        elif self._active is self._msfs and now - self._last_data > NO_DATA_DROP_S:
            self._drop()
            raise ConnectionError("No data from MSFS / P3D - looking for a sim again.")
        return data

    def _try_xplane(self):
        """Switches to X-Plane if it's answering (never waits). A SimConnect
        connection that hasn't sent any data yet gives way to it."""
        try:
            self._xplane.connect()
        except ConnectionError:
            return False
        if self._active is not None and self._active is not self._xplane:
            self._quietly_disconnect(self._active)
        self._use(self._xplane)
        return True

    def disconnect(self):
        if self._active is not None:
            self._quietly_disconnect(self._active)
        self._quietly_disconnect(self._xplane)
        self._active = None

    def is_connected(self):
        return self._active is not None

    def _msfs_connector(self):
        """The MSFS connector, loaded on first use. None if the SimConnect
        library isn't installed - X-Plane still works then."""
        if self._msfs is None and self._msfs_unavailable is None:
            try:
                from connectors.msfs import MSFSConnector
                self._msfs = MSFSConnector()
            except ImportError as e:
                self._msfs_unavailable = str(e)
                print(f"[auto] MSFS / P3D support unavailable ({e}) - X-Plane only.")
        return self._msfs

    def _use(self, connector):
        self._active = connector
        self._last_data = time.monotonic()
        self._msfs_proven = False

    def _drop(self):
        self._quietly_disconnect(self._active)
        self._active = None

    @staticmethod
    def _quietly_disconnect(connector):
        try:
            connector.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    connector = AutoConnector()
    print("Looking for a running sim... (Ctrl+C to stop)")
    try:
        while True:
            try:
                connector.connect()
                data = connector.read()
                state = "waiting for data" if data is None else (
                    f"{data.get('title')!r} at {data.get('latitude'):.4f}, {data.get('longitude'):.4f}")
            except ConnectionError as e:
                state = str(e)
            print(f"  [{connector.SIM_NAME}] {state}")
            time.sleep(1)
    except KeyboardInterrupt:
        connector.disconnect()