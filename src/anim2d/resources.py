"""Product identity and the files shipped inside the package (preset library, icon), in source,
installed and built form. Settings namespaces live here too, with a one-time copy from the names the
app had before it became Anim2D.

A build made by tools/dev_build.py is on the "dev" channel (see build_info()): it keeps its own
settings and FX templates and never updates itself, so it can sit next to an installed Anim2D
without touching it, and it is labelled DEV BUILD wherever it shows.
"""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path

from . import __version__

APP_NAME = "Anim2D"
FX_APP_NAME = "Anim2D FX Builder"
ORGANIZATION = "Anim2D"
DEV_ORGANIZATION = "Anim2D-Dev"  # settings of local test builds, apart from the real app's
VERSION = __version__
REPOSITORY = "turtlegameworks/Anim2D"  # GitHub owner/name: releases, issues, update checks
HOMEPAGE = f"https://github.com/{REPOSITORY}"  # source, issues and releases
WEBSITE = "https://turtlegameworks.github.io/Anim2D"  # the product site (GitHub Pages, tools/build_site.py)
DOCS_URL = f"{WEBSITE}/docs/"

# (new application name, (old organization, old application)) for QSettings
_SETTINGS = {
    "editor": ("Anim2D", ("RigWorks", "RigWorks2D")),
    "fx": ("Anim2DFXBuilder", ("SpineFXBuilder", "SpineFXBuilder")),
}


def bundle_root() -> Path:
    """The anim2d package folder; in a PyInstaller build, its copy inside the unpacked bundle."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "anim2d"
    return Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def build_info() -> dict:
    """What the build recorded about itself (build_info.json next to the package data): channel
    ("release" or "dev"), commit, branch, dirty, built. Running from source: channel "source"."""
    if not getattr(sys, "frozen", False):
        return {"channel": "source"}
    try:
        info = json.loads((bundle_root() / "build_info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = {}
    if not isinstance(info, dict):
        info = {}
    info.setdefault("channel", "release")  # official builds don't carry the file
    return info


def system_description() -> str:
    """'Windows 10.0.26100 (AMD64)', 'macOS 15.1 (arm64)': for the log and bug reports."""
    import platform
    if sys.platform == "darwin":
        return f"macOS {platform.mac_ver()[0]} ({platform.machine()})"
    if sys.platform == "win32":
        return f"Windows {platform.version()} ({platform.machine()})"
    return f"{platform.system()} {platform.release()} ({platform.machine()})"


def is_dev_build() -> bool:
    return build_info().get("channel") == "dev"


def dev_label() -> str:
    """'DEV BUILD · abc1234' for test builds, '' otherwise."""
    if not is_dev_build():
        return ""
    info = build_info()
    commit = str(info.get("commit") or "")[:7]
    return "DEV BUILD" + (f" · {commit}" if commit else "") + (" · uncommitted changes" if info.get("dirty") else "")


def icon_path() -> Path:
    if is_dev_build() and (bundle_root() / "assets" / "anim2d-dev.ico").is_file():
        return bundle_root() / "assets" / "anim2d-dev.ico"
    return bundle_root() / "assets" / "anim2d.ico"


def apply_app_icon(app) -> None:
    from PySide6.QtGui import QIcon
    path = icon_path()
    if path.is_file():
        app.setWindowIcon(QIcon(str(path)))


def settings(which: str = "editor"):
    """QSettings for the editor ("editor") or the FX builder ("fx"). The first time the new namespace is
    used, values saved under the previous name are copied over; the old ones are left in place. Test
    builds use their own namespace and start from defaults, so they never change the real app's."""
    from PySide6.QtCore import QSettings
    name, (old_org, old_app) = _SETTINGS[which]
    if is_dev_build():
        return QSettings(DEV_ORGANIZATION, name)
    store = QSettings(ORGANIZATION, name)
    if not store.allKeys() and not store.value("_migrated", False, bool):
        old = QSettings(old_org, old_app)
        for key in old.allKeys():
            store.setValue(key, old.value(key))
        store.setValue("_migrated", True)
    return store
