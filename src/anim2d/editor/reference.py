"""Reference footage behind the viewport, for rotoscoping: a video, an image sequence or one image.

A video is decoded once in the background (Qt Multimedia, scaled down to at most MAX_SIDE pixels) and
the frame nearest the timeline's time is shown. An image sequence (a folder of numbered images, or
one of them) plays at the project's frame rate. Settings live in the project:
doc.project_settings["reference"].
"""

from __future__ import annotations

import bisect
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QImage

VIDEO = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv", ".gif"}
IMAGES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tga", ".tif", ".tiff"}
MAX_SIDE = 1280  # decoded frames are scaled to fit, to keep memory reasonable
MAX_FRAMES = 3000
FILTER = "Reference footage (" + " ".join(f"*{e}" for e in sorted(VIDEO | IMAGES)) + ")"


@dataclass
class ReferenceSettings:
    path: str = ""
    on: bool = True
    x: float = 0.0  # world position of the footage centre
    y: float = 0.0
    scale: float = 1.0  # world units per footage pixel
    opacity: float = 0.5
    start: float = 0.0  # footage time shown at animation time 0 (seconds)
    speed: float = 1.0  # footage seconds per animation second
    front: bool = False  # draw over the skeleton instead of behind it
    flip: bool = False  # mirror horizontally

    @classmethod
    def of(cls, doc) -> "ReferenceSettings":
        raw = doc.project_settings.get("reference", {})
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in names})

    def store(self, doc) -> None:
        doc.project_settings["reference"] = asdict(self)
        doc.dirty = True


_NUMBER = re.compile(r"^(.*?)(\d+)$")


def sequence_files(path: Path) -> list[Path]:
    """The numbered images that go with path (a folder or one image of the sequence), in order."""
    path = Path(path)
    if path.is_dir():
        files = [p for p in path.iterdir() if p.suffix.lower() in IMAGES]
    elif path.suffix.lower() in IMAGES:
        m = _NUMBER.match(path.stem)
        if not m:
            return [path]
        files = [p for p in path.parent.iterdir() if p.suffix.lower() == path.suffix.lower()
                 and (pm := _NUMBER.match(p.stem)) and pm.group(1) == m.group(1)]
    else:
        return []

    def number(p: Path) -> tuple[int, str]:
        m = _NUMBER.match(p.stem)
        return (int(m.group(2)) if m else 0, p.name)
    return sorted(files, key=number)


def _fit(image: QImage) -> QImage:
    if max(image.width(), image.height()) > MAX_SIDE:
        image = image.scaled(QSize(MAX_SIDE, MAX_SIDE), Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    return image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


class ReferenceMedia(QObject):
    """Frames of the reference footage with their times; loads videos in the background."""

    progress = Signal(int)  # frames decoded so far
    loaded = Signal()
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path: Path | None = None
        self.times: list[float] = []  # seconds, ascending
        self.frames: list[QImage] = []
        self.size = QSize()  # full size of the footage (before scaling down)
        self.duration = 0.0
        self.loading = False
        self._player = None

    def clear(self) -> None:
        self._stop()
        self.path, self.times, self.frames, self.size, self.duration = None, [], [], QSize(), 0.0

    def load(self, path: Path, fps: float = 30.0) -> None:
        self.clear()
        path = Path(path)
        self.path = path
        if path.suffix.lower() in VIDEO:
            self._load_video(path)
            return
        files = sequence_files(path)
        if not files:
            self.failed.emit(f"{path.name}: not a video, image or image folder")
            return
        for i, file in enumerate(files[:MAX_FRAMES]):
            image = QImage(str(file))
            if image.isNull():
                continue
            if self.size.isEmpty():
                self.size = image.size()
            self.times.append(i / fps)
            self.frames.append(_fit(image))
        if not self.frames:
            self.failed.emit(f"{path.name}: no readable images")
            return
        self.duration = len(self.frames) / fps if len(self.frames) > 1 else 0.0
        self.loaded.emit()

    def _load_video(self, path: Path) -> None:
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
        self.loading = True
        player = QMediaPlayer(self)
        audio = QAudioOutput(self)
        audio.setMuted(True)
        player.setAudioOutput(audio)
        sink = QVideoSink(self)
        player.setVideoSink(sink)
        self._player = (player, sink, audio)

        def frame(f) -> None:
            if not f.isValid() or f.startTime() < 0 or len(self.frames) >= MAX_FRAMES:
                return
            image = f.toImage()
            if image.isNull():
                return
            if self.size.isEmpty():
                self.size = image.size()
            t = f.startTime() / 1e6
            if self.times and t <= self.times[-1]:
                return
            self.times.append(t)
            self.frames.append(_fit(image))
            if len(self.frames) % 10 == 0:
                self.progress.emit(len(self.frames))

        def status(st) -> None:
            if st == QMediaPlayer.MediaStatus.EndOfMedia:
                self._finish()
            elif st == QMediaPlayer.MediaStatus.InvalidMedia:
                self._stop()
                self.failed.emit(f"{path.name}: this video can't be read")

        sink.videoFrameChanged.connect(frame)
        player.mediaStatusChanged.connect(status)
        player.errorOccurred.connect(lambda _e, msg: (self._stop(), self.failed.emit(f"{path.name}: {msg}")))
        player.setSource(QUrl.fromLocalFile(str(path)))
        player.setPlaybackRate(2.0)  # decode faster than real time; every frame still arrives
        player.play()

    def _finish(self) -> None:
        duration = (self._player[0].duration() / 1000) if self._player else 0.0
        self._stop()
        if not self.frames:
            self.failed.emit(f"{self.path.name}: no frames could be decoded")
            return
        self.duration = max(duration, self.times[-1])
        self.loaded.emit()

    def _stop(self) -> None:
        self.loading = False
        if self._player is not None:
            player, sink, audio = self._player
            self._player = None
            for signal in (sink.videoFrameChanged, player.mediaStatusChanged, player.errorOccurred):
                try:
                    signal.disconnect()
                except (RuntimeError, TypeError):
                    pass
            player.stop()
            player.setSource(QUrl())  # let the decoder thread finish before the objects go
            player.setVideoSink(None)
            player.deleteLater()
            sink.deleteLater()
            audio.deleteLater()

    def frame_at(self, t: float) -> QImage | None:
        """The frame showing at footage time t (clamped to the footage)."""
        if not self.frames:
            return None
        i = bisect.bisect_right(self.times, max(0.0, t)) - 1
        return self.frames[max(0, min(i, len(self.frames) - 1))]

    def frame_index(self, t: float) -> int:
        if not self.frames:
            return -1
        return max(0, min(bisect.bisect_right(self.times, max(0.0, t)) - 1, len(self.frames) - 1))
