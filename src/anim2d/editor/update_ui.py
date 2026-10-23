"""Update checks in the editor: a daily check in the background, Help > Check for Updates, the
"Update available" dialog and the download-and-install hand-off. The work is in ..updates; this
module only runs it off the UI thread and shows the results.

Settings (in the editor's QSettings): updates/auto_check, updates/last_check (ISO time),
updates/skipped (a version the user chose to skip), updates/last_error.
"""

from __future__ import annotations

import os
import traceback
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QMessageBox, QProgressDialog, QPushButton,
                               QTextBrowser, QVBoxLayout)

from .. import updates
from ..resources import APP_NAME, HOMEPAGE, VERSION

STARTUP_DELAY_MS = 8000  # let the window settle before going to the network
# Jobs emit from a pool thread; their results always run on the UI thread, which owns the signals.
QUEUED = Qt.ConnectionType.QueuedConnection


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)


class _Job(QRunnable):
    """Runs work() on the thread pool; done/failed arrive on the UI thread."""

    def __init__(self, work):
        super().__init__()
        self.work = work
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.work(self)
        except updates.UpdateError as exc:
            self.signals.failed.emit(str(exc))
            return
        except Exception as exc:  # never let a bug in the update check take the editor down
            traceback.print_exc()
            self.signals.failed.emit(f"Unexpected problem: {type(exc).__name__}: {exc}")
            return
        self.signals.done.emit(result)


class UpdateController(QObject):
    """One per editor window. status_changed carries a line for the Settings > Updates page."""

    status_changed = Signal(str)
    available = Signal(object)  # updates.Release

    def __init__(self, window, store):
        super().__init__(window)
        self.window = window
        self.store = store
        self.release: updates.Release | None = None
        self.checking = False
        self._jobs: list[_Job] = []  # keep running jobs (and their signals) alive

    # settings

    @property
    def auto_check(self) -> bool:
        return str(self.store.value("updates/auto_check", "true")).lower() in ("true", "1")

    @auto_check.setter
    def auto_check(self, value: bool) -> None:
        self.store.setValue("updates/auto_check", bool(value))

    @property
    def skipped(self) -> str:
        return str(self.store.value("updates/skipped", "") or "")

    @skipped.setter
    def skipped(self, version: str) -> None:
        if version:
            self.store.setValue("updates/skipped", version)
        else:
            self.store.remove("updates/skipped")

    def status(self) -> str:
        if self.checking:
            return "Checking for updates…"
        if self.release is not None:
            return f"{APP_NAME} {self.release.version} is available (you have {VERSION})."
        error = self.store.value("updates/last_error")
        last = self.store.value("updates/last_check")
        when = _describe_time(last) if last else None
        if error:
            return f"The last check didn't work: {error}"
        return f"You have the latest version ({VERSION}). Last checked {when}." if when else \
            "Not checked yet."

    # checking

    def schedule_startup_check(self) -> None:
        """A check a few seconds after start, at most once a day, for installed and portable builds
        (running from source or under tests never goes to the network by itself)."""
        if self.auto_check and updates.install_kind() != "source" and \
                updates.check_due(self.store.value("updates/last_check")):
            # self as context: the timer is dropped if the window closes first
            QTimer.singleShot(STARTUP_DELAY_MS, self, lambda: self.check(manual=False))

    def check(self, manual: bool = True) -> None:
        if self.checking:
            return
        self.checking = True
        self.status_changed.emit(self.status())
        job = _Job(lambda _job: updates.fetch_latest())
        job.signals.done.connect(lambda release: self._checked(release, manual), QUEUED)
        job.signals.failed.connect(lambda message: self._check_failed(message, manual), QUEUED)
        self._start(job)

    def _start(self, job: _Job) -> None:
        self._jobs.append(job)
        job.signals.done.connect(lambda *_: self._finished(job), QUEUED)
        job.signals.failed.connect(lambda *_: self._finished(job), QUEUED)
        QThreadPool.globalInstance().start(job)

    def _finished(self, job: _Job) -> None:
        if job in self._jobs:
            self._jobs.remove(job)

    def _checked(self, release: updates.Release, manual: bool) -> None:
        self.checking = False
        self.store.setValue("updates/last_check", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self.store.remove("updates/last_error")
        newer = updates.is_newer(release.version)
        self.release = release if newer else None
        self.status_changed.emit(self.status())
        if not newer:
            if manual:
                QMessageBox.information(self.window, "Check for Updates",
                                        f"You have the latest version of {APP_NAME} ({VERSION}).")
            return
        self.available.emit(release)
        if manual or release.version != self.skipped:
            dialog = UpdateDialog(self, release, self.window)
            dialog.exec()
            dialog.deleteLater()

    def _check_failed(self, message: str, manual: bool) -> None:
        self.checking = False
        self.store.setValue("updates/last_error", message)
        self.status_changed.emit(self.status())
        if manual:
            QMessageBox.warning(self.window, "Check for Updates", f"Couldn't check for updates.\n\n{message}")

    # installing

    def install(self, release: updates.Release, parent=None) -> None:
        """Download, verify and hand over to the installer (installed copies), or save the new portable
        program next to this one (portable copies)."""
        parent = parent or self.window
        kind = updates.install_kind()
        asset = updates.installer_asset(release) if kind == "installed" else updates.portable_asset(release)
        if kind == "source" or asset is None:
            self.open_page(release)
            return
        if kind == "installed" and not self.window._confirm_discard():  # save or discard before closing
            return
        folder = None if kind == "installed" else _portable_folder()
        progress = QProgressDialog(f"Downloading {asset.name}…", "Cancel", 0, 1000, parent)
        progress.setWindowTitle(f"Updating {APP_NAME}")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        cancelled = {"flag": False}
        progress.canceled.connect(lambda: cancelled.update(flag=True))

        def work(job: _Job) -> Path:
            sums = updates.fetch_checksums(release)
            if asset.name not in sums:
                raise updates.UpdateError(f"{asset.name} isn't listed in the release's checksums, so it "
                                          "can't be verified.")

            def report(done: int, total: int) -> bool:
                job.signals.progress.emit(done, total)
                return not cancelled["flag"]
            return updates.download(asset, sums[asset.name], folder, report)

        def show_progress(done: int, total: int) -> None:
            progress.setValue(int(1000 * done / max(1, total)))
            progress.setLabelText(f"Downloading {asset.name}…  {done / 1048576:.1f} of {total / 1048576:.1f} MB")

        def finish() -> bool:
            """Close the progress window; True if the user had pressed Cancel. (Closing it emits
            canceled too, so read the flag first.)"""
            by_user = cancelled["flag"]
            progress.canceled.disconnect()
            progress.close()
            return by_user

        def downloaded(path) -> None:
            finish()
            self._downloaded(release, Path(path), kind)

        def failed(message: str) -> None:
            if not finish():
                QMessageBox.warning(parent, f"Updating {APP_NAME}", f"The update couldn't be downloaded.\n\n{message}")

        job = _Job(work)
        job.signals.progress.connect(show_progress, QUEUED)
        job.signals.done.connect(downloaded, QUEUED)
        job.signals.failed.connect(failed, QUEUED)
        self._start(job)

    def _downloaded(self, release: updates.Release, path: Path, kind: str) -> None:
        if kind == "portable":
            box = QMessageBox(QMessageBox.Icon.Information, f"{APP_NAME} {release.version} downloaded",
                              f"Saved {path.name} in\n{path.parent}\n\nClose this copy and start the new one. "
                              "You can delete the old program afterwards.", parent=self.window)
            folder = box.addButton("Open folder", QMessageBox.ButtonRole.ActionRole)
            box.addButton(QMessageBox.StandardButton.Ok)
            box.exec()
            if box.clickedButton() is folder:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
            return
        if not self.window._confirm_discard():  # anything changed while it downloaded: save, or keep working
            return
        try:
            updates.start_installer(path, updates.install_dir(), os.getpid())
        except updates.UpdateError as exc:
            QMessageBox.warning(self.window, f"Updating {APP_NAME}", str(exc))
            return
        updates.clean_cache(keep=path)
        self.window.doc.dirty = False  # just saved or discarded, so closing doesn't ask again
        from PySide6.QtWidgets import QApplication
        QApplication.instance().closeAllWindows()  # the installer waits for this process to end

    def open_page(self, release: updates.Release | None = None) -> None:
        page = (release.page if release and release.page else f"{HOMEPAGE}/releases/latest")
        QDesktopServices.openUrl(QUrl(page))


def _portable_folder() -> Path:
    """Next to the running program if that folder is writable, else Downloads."""
    here = updates.install_dir()
    if os.access(here, os.W_OK):
        return here
    return Path.home() / "Downloads"


def _describe_time(iso: object) -> str | None:
    try:
        when = datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone().strftime("%d %b %Y, %H:%M")


class UpdateDialog(QDialog):
    """What's new in the release, and what this copy can do about it."""

    def __init__(self, controller: UpdateController, release: updates.Release, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Update available")
        self.resize(560, 460)
        self.controller, self.release = controller, release
        kind = updates.install_kind()
        title = QLabel(f"<b style='font-size:15px'>{APP_NAME} {release.version} is available</b>")
        sub = QLabel(f"You have {VERSION}." + (f" Released {release.published[:10]}." if release.published else ""))
        sub.setStyleSheet("color: #8f8f8f;")
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(release.notes.strip() or "_No release notes._")
        hint = QLabel({
            "installed": f"Install update downloads it, closes {APP_NAME} (asking to save first) and restarts it "
                         "on the new version.",
            "portable": "This is the portable program, so the new version is downloaded next to it for you to "
                        "start instead.",
            "source": "You're running from source: update with git pull, or download a release.",
        }[kind])
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8f8f8f;")
        buttons = QDialogButtonBox()
        main_text = {"installed": "Install update", "portable": "Download", "source": "Open release page"}[kind]
        main = buttons.addButton(main_text, QDialogButtonBox.ButtonRole.AcceptRole)
        main.setDefault(True)
        if kind != "source":
            page = buttons.addButton("Release page", QDialogButtonBox.ButtonRole.HelpRole)
            page.clicked.connect(lambda: controller.open_page(release))
        skip = QPushButton("Skip this version")
        later = QPushButton("Remind me later")
        buttons.addButton(skip, QDialogButtonBox.ButtonRole.RejectRole)
        buttons.addButton(later, QDialogButtonBox.ButtonRole.RejectRole)
        main.clicked.connect(self._main)
        skip.clicked.connect(self._skip)
        later.clicked.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(sub)
        layout.addWidget(notes, 1)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    def _main(self) -> None:
        self.accept()
        self.controller.install(self.release, self.parent())

    def _skip(self) -> None:
        self.controller.skipped = self.release.version
        self.reject()
