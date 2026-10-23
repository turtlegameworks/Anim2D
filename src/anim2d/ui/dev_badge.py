"""The DEV BUILD label on local test builds (tools/dev_build.py): an amber tag at the right end of the
status bar, with the commit and build time in its tooltip. Official builds and source runs show nothing."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QMainWindow

from .. import resources

STYLE = ("QLabel { background: #f0a020; color: #1b1b1b; font-weight: bold; border-radius: 3px;"
         " padding: 1px 8px; margin: 1px 2px; }")


def title_suffix() -> str:
    """' [DEV]' for window titles of test builds."""
    return " [DEV]" if resources.is_dev_build() else ""


def add_dev_badge(window: QMainWindow) -> QLabel | None:
    if not resources.is_dev_build():
        return None
    info = resources.build_info()
    badge = QLabel(resources.dev_label())
    badge.setObjectName("devBadge")
    badge.setStyleSheet(STYLE)
    lines = ["Local test build: not an official release.",
             "It keeps its own settings and FX templates and never updates itself."]
    for key, label in (("commit", "Commit"), ("branch", "Branch"), ("built", "Built")):
        if info.get(key):
            lines.append(f"{label}: {info[key]}")
    if info.get("dirty"):
        lines.append("Built with uncommitted changes.")
    badge.setToolTip("\n".join(lines))
    window.statusBar().addPermanentWidget(badge)
    return badge
