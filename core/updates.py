"""
core/updates.py

Checks GitHub for a newer release of the app. No Qt - the UI runs it in the
background once per startup (ui/main_window.py) and shows a link in the
sidebar if there is one.

A release is what you publish on GitHub under Releases, tagged with the
version (e.g. "v1.0.4" or "1.0.4"). Drafts and pre-releases are ignored -
GitHub's "latest release" only ever returns a full release.

Usage:
    update = check_for_update("1.0.3")
    # -> {"version": "1.0.4", "url": "https://github.com/.../releases/tag/v1.0.4"}
    # or None: up to date, no releases yet, or GitHub couldn't be reached
"""

import json
import re
import urllib.error
import urllib.request

REPO = "deonjonker123/ACARS"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
TIMEOUT_SECONDS = 10

_VERSION_PATTERN = re.compile(r"^\s*v?(\d+(?:\.\d+)*)\s*$", re.IGNORECASE)


def parse_version(text):
    """ "v1.0.4" / "1.0.4" -> (1, 0, 4); None if it isn't a plain version."""
    match = _VERSION_PATTERN.match(str(text or ""))
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(candidate, current):
    """True if version string `candidate` is newer than `current`
    (1.0.10 > 1.0.9; 1.1 == 1.1.0)."""
    a, b = parse_version(candidate), parse_version(current)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def latest_release(timeout=TIMEOUT_SECONDS):
    """GitHub's latest full release as {"version", "url"}, or None if there
    isn't one or GitHub can't be reached. Never raises."""
    request = urllib.request.Request(LATEST_RELEASE_API, headers={
        "User-Agent": "Tailwind-ACARS",
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    tag = data.get("tag_name") or ""
    if parse_version(tag) is None:
        return None
    return {
        "version": ".".join(str(n) for n in parse_version(tag)),
        "url": data.get("html_url") or RELEASES_PAGE,
    }


def check_for_update(current_version, timeout=TIMEOUT_SECONDS):
    """The latest release if it's newer than current_version, else None."""
    release = latest_release(timeout)
    if release and is_newer(release["version"], current_version):
        return release
    return None


if __name__ == "__main__":
    from version import APP_VERSION
    print(f"This version: {APP_VERSION}")
    print(f"Latest release on GitHub: {latest_release()}")
    print(f"Update available: {check_for_update(APP_VERSION)}")