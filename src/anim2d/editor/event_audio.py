"""Audio event playback: while the timeline or the Preview plays, events with an audio path play it.

Spine stores an event's sound as a path relative to the skeleton's audio folder, plus volume and
balance (key values override the event's). The audio folder is a project setting
(doc.project_settings["audio_dir"], relative to the project file); without one, an "audio" folder
next to the images folder is used, then the images folder itself. Qt plays WAV, MP3 and OGG.
Balance (left/right) is kept in the data but Qt's player has no pan control, so it isn't heard.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .document import Document

AUDIO_EXTENSIONS = (".wav", ".mp3", ".ogg", ".flac", ".m4a")


def audio_dir(doc: Document) -> Path | None:
    setting = doc.project_settings.get("audio_dir")
    if setting:
        path = Path(setting)
        if not path.is_absolute() and doc.path:
            path = doc.path.parent / path
        return path
    if doc.images_dir:
        sibling = doc.images_dir.parent / "audio"
        return sibling if sibling.is_dir() else doc.images_dir
    return None


def set_audio_dir(doc: Document, folder: Path | None) -> None:
    if folder is None:
        doc.project_settings.pop("audio_dir", None)
    else:
        folder = Path(folder)
        if doc.path:
            try:
                folder = Path(os.path.relpath(folder, doc.path.parent))
            except ValueError:  # another drive
                pass
        doc.project_settings["audio_dir"] = folder.as_posix()
    doc.dirty = True


def audio_file(doc: Document, path: str) -> Path | None:
    """The sound file for an event's audio path (the extension may be left out)."""
    folder = audio_dir(doc)
    if not path or folder is None:
        return None
    file = folder / path
    if file.is_file():
        return file
    for ext in AUDIO_EXTENSIONS:
        if file.with_suffix(ext).is_file():
            return file.with_suffix(ext)
    return None


def crossed(doc: Document, animation: str | None, before: float, now: float) -> list[dict[str, Any]]:
    """Event keys with audio that the playhead passed going from `before` to `now`: (before, now],
    or, when it wrapped around a loop (now < before), (before, end] and [0, now].
    Returns [{'name', 'audio', 'volume', 'balance', 'time'}] in play order."""
    if not animation or animation not in doc.data["animations"]:
        return []
    events = doc.data.get("events", {})
    out = []
    for key in sorted(doc.event_keys(animation), key=lambda k: k.get("time", 0)):
        t = key.get("time", 0)
        hit = before < t <= now if now >= before else (t > before or t <= now)
        if not hit:
            continue
        event = events.get(key.get("name"), {})
        audio = key.get("audio", event.get("audio"))
        if not audio:
            continue
        out.append({"name": key.get("name"), "audio": audio, "time": t,
                    "volume": key.get("volume", event.get("volume", 1)),
                    "balance": key.get("balance", event.get("balance", 0))})
    if now < before:  # the ones after `before` play first
        out.sort(key=lambda e: (e["time"] <= now, e["time"]))
    return out


class EventAudio:
    """Plays the sounds of events the playhead passes. Keeps a few players so sounds can overlap."""

    POOL = 6

    def __init__(self, doc: Document):
        self.doc = doc
        self.enabled = True
        self._players: list[tuple[Any, Any]] = []
        self._next = 0
        self.played: list[str] = []  # audio paths, newest last (for tests and the status bar)

    def advance(self, animation: str | None, before: float, now: float, speed: float = 1.0) -> list[dict[str, Any]]:
        if not self.enabled or speed <= 0:
            return []
        fired = crossed(self.doc, animation, before, now)
        for e in fired:
            self.play(e["audio"], e["volume"])
        return fired

    def play(self, path: str, volume: float = 1.0) -> bool:
        file = audio_file(self.doc, path)
        if file is None:
            return False
        self.played.append(path)
        del self.played[:-50]
        player, output = self._player()
        if player is None:
            return False
        from PySide6.QtCore import QUrl
        output.setVolume(max(0.0, min(1.0, float(volume))))
        player.setSource(QUrl.fromLocalFile(str(file)))
        player.play()
        return True

    def stop(self) -> None:
        for player, _ in self._players:
            player.stop()

    def _player(self):
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        except ImportError:
            return None, None
        if len(self._players) < self.POOL:
            output = QAudioOutput()
            player = QMediaPlayer()
            player.setAudioOutput(output)
            self._players.append((player, output))
            return player, output
        pair = self._players[self._next]
        self._next = (self._next + 1) % self.POOL
        pair[0].stop()
        return pair
