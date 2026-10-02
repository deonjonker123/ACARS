"""
Auto connector: uses whichever sim is running - MSFS / P3D / FSX (FSUIPC,
connectors/fsuipc.py) or X-Plane (connectors/xplane.py) - with nothing to
set up. Same interface as the individual connectors, so
core/flight_session.py just uses this one.

While no sim is connected, each connect() call (the app retries every
poll) looks for:
  1. X-Plane            - (re)sends its UDP subscriptions and checks
                          whether it has answered; never waits for it
  2. MSFS / P3D / FSX   - FSUIPC (FSUIPC7 for MSFS), if its window is there
The first one found is used, and logged.

FSUIPC being there isn't proof a flight is: FSUIPC7 runs on its own and
can be left open next to another sim. So while the FSUIPC connector has no
data (its read() returns None until the sim is ready to fly), X-Plane is
still checked on every read() and takes over as soon as it answers.

When the connected sim goes away the connector drops it and goes back to
searching, so switching sims needs no app restart: X-Plane's read()
raises once nothing has arrived for a few seconds, FSUIPC's once its
window stops answering.

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
from connectors.fsuipc import FSUIPCConnector

SEARCHING = "Searching for a running sim..."


class AutoConnector:
    def __init__(self):
        self._xplane = XPlaneConnector()
        self._fsuipc = FSUIPCConnector()
        self._active = None

    @property
    def SIM_NAME(self):
        return self._active.SIM_NAME if self._active is not None else SEARCHING

    def connect(self):
        if self._active is not None:
            return

        if self._try_xplane():
            return

        try:
            self._fsuipc.connect()
        except Exception:
            self._quietly_disconnect(self._fsuipc)
        else:
            self._use(self._fsuipc)
            return

        raise ConnectionError("No sim found - looking for MSFS, P3D / FSX (FSUIPC) and X-Plane.")

    def read(self):
        if self._active is None:
            raise ConnectionError("Not connected to a sim.")
        try:
            data = self._active.read()
        except Exception:
            self._drop()
            raise

        if data is None and self._active is self._fsuipc and self._try_xplane():
            return self._xplane.read()
        return data

    def _try_xplane(self):
        """Switches to X-Plane if it's answering (never waits). An FSUIPC
        connection without data gives way to it."""
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
            print(f"[auto] {time.strftime('%H:%M:%S')} Disconnected from {self._active.SIM_NAME}.")
            self._quietly_disconnect(self._active)
        self._quietly_disconnect(self._xplane)
        self._active = None

    def is_connected(self):
        return self._active is not None

    def _use(self, connector):
        self._active = connector
        print(f"[auto] {time.strftime('%H:%M:%S')} Connected: {connector.SIM_NAME}")

    def _drop(self):
        print(f"[auto] {time.strftime('%H:%M:%S')} Lost {self._active.SIM_NAME} - searching for a sim again.")
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