"""Where Anim2D keeps things, following each system's rule: your work in Documents, the app's own files
where the system keeps them.

    Documents\\Anim2D\\Projects       default place to save projects
    Documents\\Anim2D\\FX Templates   templates registered from Spine exports
    Documents\\Anim2D\\FX Output      packages the FX Builder generates
    <app data>\\updates              downloaded updates (anim2d.updates)
    <logs>                           anim2d.log, for bug reports (anim2d.logs)

App data is %LOCALAPPDATA%\\Anim2D on Windows and ~/Library/Application Support/Anim2D on macOS, with
the logs in its logs folder on Windows and in ~/Library/Logs/Anim2D on macOS (where Console finds them).
Settings stay where Qt keeps them: the registry (HKCU\\Software\\Anim2D) on Windows, a .plist in
~/Library/Preferences on macOS. Local test builds
(tools/dev_build.py) use "Anim2D Dev" / "Anim2D-Dev" in place of "Anim2D", so they never share files
with an installed copy. Documents is the real one, OneDrive redirection included. Folders from before
0.17.1 ("Documents\\Anim2D Templates", "Documents\\Anim2D FX Output") are moved here once.

installer/anim2d_setup.py can't import this and repeats the few paths it removes on uninstall: keep
them in step.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_FOLDERID_DOCUMENTS = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"


def _dev() -> bool:
    from .resources import is_dev_build
    return is_dev_build()


def documents() -> Path:
    """The user's Documents folder: as Windows has it (it may be redirected, e.g. to OneDrive), else
    ~/Documents."""
    return known_documents() or Path.home() / "Documents"


def known_documents() -> Path | None:
    """Documents from Windows (SHGetKnownFolderPath), or None where that isn't available."""
    if hasattr(ctypes, "windll"):
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]

        guid = GUID()
        ctypes.windll.ole32.CLSIDFromString(_FOLDERID_DOCUMENTS, ctypes.byref(guid))
        found = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(found)) == 0:
            try:
                return Path(found.value)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(found)
    return None


def user_root() -> Path:
    """Documents\\Anim2D: everything that is the user's own work."""
    return documents() / ("Anim2D Dev" if _dev() else "Anim2D")


def projects_dir() -> Path:
    return user_root() / "Projects"


def templates_dir() -> Path:
    return user_root() / "FX Templates"


def fx_output_dir() -> Path:
    return user_root() / "FX Output"


def _app_name() -> str:
    return "Anim2D-Dev" if _dev() else "Anim2D"


def app_data() -> Path:
    """The app's own files, which it can recreate or that only help support: %LOCALAPPDATA%\\Anim2D on
    Windows, ~/Library/Application Support/Anim2D on macOS (and $XDG_DATA_HOME/Anim2D elsewhere)."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / _app_name()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / _app_name()


def updates_dir() -> Path:
    return app_data() / "updates"


def logs_dir() -> Path:
    """%LOCALAPPDATA%\\Anim2D\\logs on Windows, ~/Library/Logs/Anim2D on macOS."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / _app_name()
    return app_data() / "logs"


def legacy_folders() -> dict[Path, list[Path]]:
    """Folders from before 0.17.1, by the folder that replaces them (most recent name first)."""
    docs = documents()
    home_docs = Path.home() / "Documents"
    roots = [docs] if docs == home_docs else [docs, home_docs]
    if _dev():
        return {templates_dir(): [r / "Anim2D Dev Templates" for r in roots]}
    return {templates_dir(): [r / name for r in roots for name in ("Anim2D Templates", "Spine FX Templates")],
            fx_output_dir(): [docs / "Anim2D FX Output"]}


def migrate() -> list[tuple[Path, Path]]:
    """Move folders from before 0.17.1 to their new place, once: only when the new one doesn't exist,
    never merging or overwriting. A folder that can't be moved (in use, another drive) stays where it
    is, and the app keeps using it. Returns the moves made."""
    moved = []
    for new, olds in legacy_folders().items():
        if new.exists():
            continue
        for old in olds:
            if not old.is_dir():
                continue
            try:
                new.parent.mkdir(parents=True, exist_ok=True)
                old.rename(new)
            except OSError as exc:
                log.warning("couldn't move %s to %s: %s", old, new, exc)
            else:
                log.info("moved %s to %s", old, new)
                moved.append((old, new))
            break
    return moved


def current(new: Path) -> Path:
    """new, or the pre-0.17.1 folder it replaces if that one is still there and new isn't (see migrate)."""
    if not new.exists():
        for old in legacy_folders().get(new, []):
            if old.is_dir():
                return old
    return new
