"""Export an animation as GIF, animated WebP, APNG or MP4 video.

Frames come from the editor's renderer (the same drawing as the viewport). GIF, WebP and APNG
are written with Pillow; MP4 (H.264) with Qt Multimedia's recorder fed frame by frame
(QVideoFrameInput), which uses the encoder Windows provides. Video has no transparency, so MP4
frames are drawn over the background colour.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QEventLoop, QSize, QTimer, QUrl
from PySide6.QtGui import QColor, QImage

from ..preview.qt_renderer import render_image, world_bounds
from .document import Document, EditorError
from .render import _cache

FORMATS = {"gif": "GIF", "webp": "Animated WebP", "apng": "Animated PNG", "mp4": "MP4 video (H.264)"}
SUFFIX = {"gif": ".gif", "webp": ".webp", "apng": ".png", "mp4": ".mp4"}


def render_animation(doc: Document, animation: str, size: int = 512, fps: float | None = None,
                     background: QColor | None = None, repeat: int = 1) -> tuple[list[QImage], float]:
    """Frames of the animation (played `repeat` times), framed once for all frames. Returns (frames, fps)."""
    if animation not in doc.data["animations"]:
        raise EditorError(f"No animation named '{animation}'")
    fps = float(fps or doc.fps)
    if not 1 <= fps <= 120:
        raise EditorError("Frames per second must be between 1 and 120")
    evaluator = doc.evaluator(animation)
    duration = evaluator.duration
    count = max(1, int(math.floor(duration * fps + 1e-6)))  # the last frame equals the first when looping
    if duration <= 0:
        count = 1
    bounds = world_bounds(evaluator, samples=max(16, count * 2))
    if bounds.isEmpty():
        raise EditorError(f"'{animation}' shows no images to render")
    cache = _cache(doc)
    frames = [render_image(evaluator, cache, i / fps, size, background, bounds) for i in range(count)]
    return frames * max(1, repeat), fps


def _to_pil(image: QImage) -> Image.Image:
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return Image.frombuffer("RGBA", (rgba.width(), rgba.height()), bytes(rgba.constBits()), "raw", "RGBA",
                            rgba.bytesPerLine(), 1).copy()


def write_gif(frames: list[QImage], path: Path, fps: float, loop: bool = True) -> None:
    images = [_to_pil(f) for f in frames]
    converted = []
    for img in images:  # palette with one transparent index for fully transparent pixels
        alpha = img.getchannel("A")
        p = img.convert("RGB").quantize(colors=255, method=Image.Quantize.MEDIANCUT)
        mask = alpha.point(lambda a: 255 if a < 128 else 0)
        p.paste(255, mask=mask)
        converted.append(p)
    converted[0].save(path, save_all=True, append_images=converted[1:], duration=round(1000 / fps),
                      loop=0 if loop else 1, transparency=255, disposal=2, optimize=False)


def write_webp(frames: list[QImage], path: Path, fps: float, loop: bool = True, lossless: bool = True) -> None:
    images = [_to_pil(f) for f in frames]
    images[0].save(path, save_all=True, append_images=images[1:], duration=round(1000 / fps),
                   loop=0 if loop else 1, lossless=lossless, quality=90)


def write_apng(frames: list[QImage], path: Path, fps: float, loop: bool = True) -> None:
    images = [_to_pil(f) for f in frames]
    images[0].save(path, format="PNG", save_all=True, append_images=images[1:], duration=round(1000 / fps),
                   loop=0 if loop else 1, disposal=1)


def write_mp4(frames: list[QImage], path: Path, fps: float, timeout: float = 120.0) -> None:
    """H.264 MP4 through Qt Multimedia (frames must be opaque; their size is rounded to even)."""
    from PySide6.QtMultimedia import (QMediaCaptureSession, QMediaFormat, QMediaRecorder, QVideoFrame,
                                      QVideoFrameFormat, QVideoFrameInput)
    width, height = frames[0].width() // 2 * 2, frames[0].height() // 2 * 2
    images = [f.copy(0, 0, width, height).convertToFormat(QImage.Format.Format_RGBX8888) for f in frames]
    frame_format = QVideoFrameFormat(QSize(width, height), QVideoFrameFormat.PixelFormat.Format_RGBX8888)
    frame_format.setStreamFrameRate(fps)
    source = QVideoFrameInput(frame_format)
    session = QMediaCaptureSession()
    session.setVideoFrameInput(source)
    recorder = QMediaRecorder()
    session.setRecorder(recorder)
    media = QMediaFormat(QMediaFormat.FileFormat.MPEG4)
    media.setVideoCodec(QMediaFormat.VideoCodec.H264)
    recorder.setMediaFormat(media)
    recorder.setVideoResolution(QSize(width, height))
    recorder.setVideoFrameRate(fps)
    recorder.setQuality(QMediaRecorder.Quality.VeryHighQuality)
    recorder.setOutputLocation(QUrl.fromLocalFile(str(Path(path).resolve())))
    loop = QEventLoop()
    state = {"next": 0, "error": None, "stopping": False}
    step = 1_000_000 / fps  # microseconds

    def send() -> None:
        while state["next"] < len(images):
            i = state["next"]
            frame = QVideoFrame(images[i])
            frame.setStartTime(int(i * step))
            frame.setEndTime(int((i + 1) * step))
            if not source.sendVideoFrame(frame):
                return  # the recorder is busy: readyToSendVideoFrame calls again
            state["next"] += 1
        if not state["stopping"]:
            state["stopping"] = True
            recorder.stop()

    def on_state(new_state) -> None:
        if new_state == QMediaRecorder.RecorderState.StoppedState and state["stopping"]:
            loop.quit()

    def on_error(error, text) -> None:
        state["error"] = text or str(error)
        loop.quit()

    source.readyToSendVideoFrame.connect(send)
    recorder.recorderStateChanged.connect(on_state)
    recorder.errorOccurred.connect(on_error)
    QTimer.singleShot(int(timeout * 1000), loop.quit)
    recorder.record()
    QTimer.singleShot(0, send)
    loop.exec()
    if state["error"]:
        raise EditorError(f"Video export failed: {state['error']}")
    if state["next"] < len(images) or recorder.recorderState() != QMediaRecorder.RecorderState.StoppedState:
        recorder.stop()
        raise EditorError("Video export did not finish (the video encoder did not accept the frames)")
    if not Path(path).is_file() or Path(path).stat().st_size == 0:
        raise EditorError("Video export wrote no file; this system may lack an H.264 encoder")


def export_animation(doc: Document, animation: str, path: Path, fmt: str, size: int = 512,
                     fps: float | None = None, background: QColor | None = None, repeat: int = 1,
                     loop: bool = True, overwrite: bool = False) -> Path:
    """Render and write one file. fmt: gif, webp, apng or mp4. Returns the file written."""
    if fmt not in FORMATS:
        raise EditorError(f"Unknown format '{fmt}'")
    path = Path(path)
    if path.suffix.lower() != SUFFIX[fmt]:
        path = path.with_suffix(SUFFIX[fmt])
    if path.exists() and not overwrite:
        raise EditorError(f"{path.name} already exists")
    if fmt == "mp4" and background is None:
        background = QColor("#202020")  # video has no transparency
    frames, fps = render_animation(doc, animation, size, fps, background, repeat)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "gif":
        write_gif(frames, path, fps, loop)
    elif fmt == "webp":
        write_webp(frames, path, fps, loop)
    elif fmt == "apng":
        write_apng(frames, path, fps, loop)
    else:
        write_mp4(frames, path, fps)
    return path
