"""The log file and what happens on an unexpected error.

The built program has no console, so anything printed is lost. Instead, setup() writes to anim2d.log in
paths.logs_dir() (rotated at 1 MB, three old copies kept), and an error that
nothing caught is written there and shown in a message box that says what to do next, rather than the
program disappearing or doing nothing.
"""

from __future__ import annotations

import logging
import logging.handlers
import platform
import sys
import threading
import traceback
from pathlib import Path

from . import paths
from .resources import APP_NAME, VERSION, build_info, system_description

LOG_NAME = "anim2d.log"
log = logging.getLogger("anim2d")
_installed = False


def log_file() -> Path:
    return paths.logs_dir() / LOG_NAME


def setup(mode: str) -> Path | None:
    """Start logging to the log file (once per process). mode: 'editor' or 'fx', for the first line."""
    global _installed
    if _installed:
        return log_file()
    try:
        paths.logs_dir().mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(log_file(), maxBytes=1_000_000, backupCount=3,
                                                       encoding="utf-8", delay=True)
    except OSError:
        return None  # a log is a help, never a reason not to start
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    _installed = True
    from PySide6.QtCore import qVersion
    info = build_info()
    log.info("%s %s (%s, %s%s) on %s, Python %s, Qt %s", APP_NAME, VERSION, mode, info.get("channel"),
             f" {str(info.get('commit', ''))[:7]}" if info.get("commit") else "", system_description(),
             platform.python_version(), qVersion())
    return log_file()


def install_error_handler() -> None:
    """Errors nothing caught: logged, and shown on the screen when there is a window to show them in."""
    sys.excepthook = _unhandled
    threading.excepthook = lambda args: log.error("error in thread %s", args.thread and args.thread.name,
                                                   exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


def _unhandled(kind, value, tb) -> None:
    if issubclass(kind, KeyboardInterrupt):
        sys.__excepthook__(kind, value, tb)
        return
    log.critical("unexpected error", exc_info=(kind, value, tb))
    details = "".join(traceback.format_exception(kind, value, tb))
    from PySide6.QtWidgets import QApplication
    if QApplication.instance() is None:
        sys.__excepthook__(kind, value, tb)
        return
    show_error(f"{kind.__name__}: {value}", details)


def show_error(summary: str, details: str) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices, QGuiApplication
    from PySide6.QtWidgets import QApplication, QMessageBox
    box = QMessageBox(QMessageBox.Icon.Critical, APP_NAME, f"{APP_NAME} ran into a problem it didn't expect.",
                      parent=QApplication.activeWindow())
    box.setInformativeText("You can keep working, but save your project under a new name to be safe. "
                           "If it happens again, please report it with the log attached.\n\n" + summary)
    box.setDetailedText(details)
    folder = box.addButton("Open log folder", QMessageBox.ButtonRole.ActionRole)
    copy = box.addButton("Copy details", QMessageBox.ButtonRole.ActionRole)
    box.addButton(QMessageBox.StandardButton.Close)
    box.exec()
    if box.clickedButton() is folder:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.logs_dir())))
    elif box.clickedButton() is copy:
        QGuiApplication.clipboard().setText(details)
