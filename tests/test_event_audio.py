"""Audio event playback: which sounds play as the playhead moves, and finding the files."""

from __future__ import annotations

import os
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from anim2d.editor.document import Document  # noqa: E402
from anim2d.editor.event_audio import EventAudio, audio_dir, audio_file, crossed, set_audio_dir  # noqa: E402


def _doc(tmp_path) -> Document:
    images, audio = tmp_path / "images", tmp_path / "audio"
    images.mkdir()
    audio.mkdir()
    with wave.open(str(audio / "step.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 800)
    doc = Document(images_dir=images, path=tmp_path / "p.rig2d")
    doc.add_animation("walk")
    doc.add_event("step", audio="step", volume=0.5)
    doc.add_event("mark")  # no sound
    anim = doc.animation("walk")
    anim["events"] = [{"name": "step"}, {"time": 0.5, "name": "mark"}, {"time": 0.8, "name": "step", "volume": 0.9}]
    return doc


def test_crossed(tmp_path):
    doc = _doc(tmp_path)
    assert [e["time"] for e in crossed(doc, "walk", -1e-6, 0.1)] == [0]
    assert crossed(doc, "walk", 0.1, 0.7) == [], "events without audio stay quiet"
    hit = crossed(doc, "walk", 0.7, 0.8)
    assert hit[0]["volume"] == 0.9 and hit[0]["audio"] == "step", "key values override the event's"
    assert [e["time"] for e in crossed(doc, "walk", 0.7, 0.05)] == [0.8, 0], "a loop wrap plays both, in order"
    assert crossed(doc, None, 0, 1) == []


def test_audio_folder(tmp_path):
    doc = _doc(tmp_path)
    assert audio_dir(doc) == tmp_path / "audio", "the audio folder next to images is found"
    assert audio_file(doc, "step") == tmp_path / "audio" / "step.wav", "the extension can be left out"
    other = tmp_path / "sfx"
    other.mkdir()
    set_audio_dir(doc, other)
    assert doc.project_settings["audio_dir"] == "sfx" and audio_dir(doc) == other
    assert audio_file(doc, "step") is None


def test_player_and_timeline(tmp_path):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    doc = _doc(tmp_path)
    player = EventAudio(doc)
    assert player.play("step") and player.played == ["step"]
    player.enabled = False
    assert player.advance("walk", -1, 1) == []
    player.stop()

    from anim2d.editor.state import EditorState
    from anim2d.editor.timeline import TimelinePanel
    state = EditorState(doc)
    state.animation = "walk"
    panel = TimelinePanel(state)
    heard = []
    panel.audio.play = lambda path, volume=1.0: heard.append((path, volume)) or True
    panel._heard = 0.7
    panel._start_time = 0.85
    panel._clock.restart()
    panel._tick()
    assert heard == [("step", 0.9), ("step", 0.5)], "past the end (0.8 s) it loops: the 0.8 key, then the one at 0"
