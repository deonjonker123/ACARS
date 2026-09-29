"""
build.py

Builds the standalone Windows app: dist/Tailwind/Tailwind.exe (plus the
files it needs, in the same folder). Run from the project folder with the
venv active:

    pip install pyinstaller        (once)
    python build.py

What it does:
  1. Makes build/app_icon.ico from assets/app_icon.png - one .ico holding
     16 to 256 px versions, so the icon is sharp everywhere Windows shows it.
  2. Writes build/version_info.txt from version.py, so the .exe's
     Properties -> Details show the app version.
  3. Runs PyInstaller: windowed (no console), a folder build (fast start -
     a single-file .exe would unpack ~400 MB of Qt on every launch), with
     the read-only files bundled: assets/ (except uploaded aircraft photos)
     and data/fleet_and_ranks.json + data/airports.csv.

Your logbook, fleet, photos and log are NOT part of the build - they live
in %LOCALAPPDATA%\\Tailwind ACARS (core/paths.py), so rebuilding never
touches them. Each build replaces dist/Tailwind completely.
"""

import importlib.util
import os
import struct
import sys

from version import APP_VERSION

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
BUILD_DIR = os.path.join(PROJECT_DIR, "build")
DIST_DIR = os.path.join(PROJECT_DIR, "dist")
APP_NAME = "Tailwind"
ICON_SOURCE = os.path.join(PROJECT_DIR, "assets", "app_icon.png")
ICON_FILE = os.path.join(BUILD_DIR, "app_icon.ico")
VERSION_FILE = os.path.join(BUILD_DIR, "version_info.txt")
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
ASSETS_SKIP = {"aircraft"}
DATA_FILES = ("fleet_and_ranks.json", "airports.csv")


def pack_ico(images):
    """[(size, png_bytes), ...] -> the bytes of a .ico file (PNG-compressed
    entries, which Windows has supported since Vista)."""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, data = b"", b""
    for size, png in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        data += png
        offset += len(png)
    return header + entries + data


def make_icon():
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QImage

    source = QImage(ICON_SOURCE)
    if source.isNull():
        sys.exit(f"Couldn't read {ICON_SOURCE}")
    images = []
    for size in ICON_SIZES:
        scaled = source.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        scaled.save(buffer, "PNG")
        buffer.close()
        images.append((size, bytes(data)))
    with open(ICON_FILE, "wb") as f:
        f.write(pack_ico(images))
    print(f"  icon:     {ICON_FILE} ({len(images)} sizes)")


def make_version_file():
    parts = [int(p) for p in APP_VERSION.split(".")[:3]]
    parts += [0] * (4 - len(parts))
    numbers = tuple(parts)
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '{APP_NAME}'),
      StringStruct('FileDescription', '{APP_NAME} ACARS'),
      StringStruct('FileVersion', '{APP_VERSION}'),
      StringStruct('InternalName', '{APP_NAME}'),
      StringStruct('OriginalFilename', '{APP_NAME}.exe'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{APP_VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"  version:  {VERSION_FILE} ({APP_VERSION})")


def pyinstaller_args():
    def add_data(source, target):
        return ["--add-data", f"{source}{os.pathsep}{target}"]

    args = [
        os.path.join(PROJECT_DIR, "main.py"),
        "--name", APP_NAME,
        "--windowed",
        "--noconfirm",
        "--clean",
        "--icon", ICON_FILE,
        "--version-file", VERSION_FILE,
        "--distpath", DIST_DIR,
        "--workpath", os.path.join(BUILD_DIR, "pyinstaller"),
        "--specpath", BUILD_DIR,
        "--paths", PROJECT_DIR,
        "--hidden-import", "connectors.msfs",
        "--hidden-import", "connectors.xplane",
    ]

    assets_dir = os.path.join(PROJECT_DIR, "assets")
    for name in sorted(os.listdir(assets_dir)):
        if name in ASSETS_SKIP:
            continue
        source = os.path.join(assets_dir, name)
        target = os.path.join("assets", name) if os.path.isdir(source) else "assets"
        args += add_data(source, target)

    for name in DATA_FILES:
        source = os.path.join(PROJECT_DIR, "data", name)
        if not os.path.exists(source):
            sys.exit(f"Missing {source} - it has to be bundled with the app.")
        args += add_data(source, "data")

    if importlib.util.find_spec("SimConnect") is not None:
        args += ["--collect-all", "SimConnect"]
    else:
        print("  note:     SimConnect library not installed - the build will support X-Plane only")
    return args


def main():
    if importlib.util.find_spec("PyInstaller") is None:
        sys.exit("PyInstaller isn't installed. Run:  pip install pyinstaller")
    os.makedirs(BUILD_DIR, exist_ok=True)

    print(f"Building {APP_NAME} {APP_VERSION}")
    make_icon()
    make_version_file()

    import PyInstaller.__main__
    PyInstaller.__main__.run(pyinstaller_args())

    exe = os.path.join(DIST_DIR, APP_NAME, f"{APP_NAME}.exe")
    folder = os.path.dirname(exe)
    size_mb = sum(os.path.getsize(os.path.join(root, f))
                  for root, _, files in os.walk(folder) for f in files) / 1e6
    print(f"\nDone: {exe}")
    print(f"      folder size {size_mb:,.0f} MB - copy or shortcut the whole '{APP_NAME}' folder, "
          f"not just the .exe.")


if __name__ == "__main__":
    main()