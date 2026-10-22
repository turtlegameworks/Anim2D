"""Product identity and the files shipped inside the package (preset library, icon), in source,
installed and built form. Settings namespaces live here too, with a one-time copy from the names the
app had before it became Anim2D."""

from __future__ import annotations

import sys
from pathlib import Path

from . import __version__

APP_NAME = "Anim2D"
FX_APP_NAME = "Anim2D FX Builder"
ORGANIZATION = "Anim2D"
VERSION = __version__

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


def icon_path() -> Path:
    return bundle_root() / "assets" / "anim2d.ico"


def apply_app_icon(app) -> None:
    from PySide6.QtGui import QIcon
    path = icon_path()
    if path.is_file():
        app.setWindowIcon(QIcon(str(path)))


def settings(which: str = "editor"):
    """QSettings for the editor ("editor") or the FX builder ("fx"). The first time the new namespace is
    used, values saved under the previous name are copied over; the old ones are left in place."""
    from PySide6.QtCore import QSettings
    name, (old_org, old_app) = _SETTINGS[which]
    store = QSettings(ORGANIZATION, name)
    if not store.allKeys() and not store.value("_migrated", False, bool):
        old = QSettings(old_org, old_app)
        for key in old.allKeys():
            store.setValue(key, old.value(key))
        store.setValue("_migrated", True)
    return store
