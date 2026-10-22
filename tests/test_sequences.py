"""Image sequences: numbered images played as attachment keys."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from anim2d.editor.document import Document, EditorError  # noqa: E402
from anim2d.editor.sequences import add_image_sequence, find_sequences, frame_order  # noqa: E402


@pytest.fixture
def doc(tmp_path) -> Document:
    folder = tmp_path / "images"
    (folder / "fx").mkdir(parents=True)
    for i in range(1, 5):
        Image.new("RGBA", (10 + i, 10), (255, 0, 0, 255)).save(folder / "fx" / f"fire_{i:02d}.png")
    Image.new("RGBA", (8, 8), (0, 0, 255, 255)).save(folder / "logo.png")
    d = Document(images_dir=folder)
    d.add_slot("root", "flame")
    d.add_animation("burn")
    return d


def test_find_and_order():
    seqs = find_sequences(["fx/fire_10", "fx/fire_2", "fx/fire_1", "logo", "smoke1"])
    assert seqs == {"fx/fire_": ["fx/fire_1", "fx/fire_2", "fx/fire_10"]}, "number order, singletons left out"
    assert frame_order(4, "forward") == [0, 1, 2, 3]
    assert frame_order(4, "reverse") == [3, 2, 1, 0]
    assert frame_order(4, "pingpong") == [0, 1, 2, 3, 2, 1]


def test_sequence_keys_play_each_frame(doc):
    frames = find_sequences(doc.image_paths())["fx/fire_"]
    result = add_image_sequence(doc, "flame", frames, "burn", start=0.5, fps=10, cycles=2)
    assert result["keys"] == 8 and result["end"] == pytest.approx(1.3)
    ev = doc.evaluator("burn")
    shown = [next((i.attachment for i in ev.draw_items(0.5 + n / 10 + 0.01) if i.slot == "flame"), None)
             for n in range(8)]
    assert shown == ["fire_01", "fire_02", "fire_03", "fire_04"] * 2
    assert doc.validate()[0] == []
    again = add_image_sequence(doc, "flame", frames, "burn", start=2.0, fps=10, mode="pingpong", hold_last=False)
    assert again["attachments"] == result["attachments"], "attachments already in the slot are reused"
    keys = doc.animation("burn")["slots"]["flame"]["attachment"]
    assert keys[-1]["name"] is None, "the slot is cleared after the last frame"
    doc.undo()
    doc.undo()
    assert "slots" not in doc.animation("burn") and not doc.attachments("flame")
    with pytest.raises(EditorError, match="frame rate"):
        add_image_sequence(doc, "flame", frames, "burn", fps=0)


def test_dialog(doc):
    QApplication.instance() or QApplication([])
    from anim2d.editor.sequence_dialog import SequenceDialog
    dlg = SequenceDialog(doc, "flame", "burn", 0.25)
    dlg.mode.setCurrentIndex(2)
    dlg.cycles.setValue(2)
    c = dlg.choice()
    assert c["images"][0] == "fx/fire_01" and c["mode"] == "pingpong" and c["start"] == 0.25
    assert "12 frames" in dlg.info.text()
