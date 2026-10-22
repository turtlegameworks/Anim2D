"""Draw-order keys (made by moving slots in Animate mode) and the Events window."""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from anim2d.editor.document import Document, EditorError  # noqa: E402
from anim2d.editor.main_window import EditorWindow  # noqa: E402

RUNTIME = Path(__file__).parent / "runtime"
CORE40 = RUNTIME / "vendor" / "v40" / "spine-core-4.0.js"
NODE = shutil.which("node")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def doc() -> Document:
    d = Document()
    for i in range(8):
        d.add_slot("root", f"s{i}")
    d.add_animation("a")
    return d


def test_draw_order_keys(doc, tmp_path):
    rng = random.Random(3)
    orders = {}
    for t in (0.0, 0.5, 1.0):
        order = [s["name"] for s in doc.slots]
        rng.shuffle(order)
        doc.set_draw_order_key("a", t, order)
        orders[t] = order
    for t, order in orders.items():
        assert doc.draw_order_at("a", t) == order
    doc.move_slot_keyed("a", 0.25, "s3", 2)
    at = doc.draw_order_at("a", 0.25)
    before = orders[0.0]
    assert at.index("s3") == min(len(at) - 1, before.index("s3") + 2)
    doc.set_draw_order_key("a", 0.5, [s["name"] for s in doc.slots])
    assert {"time": 0.5} in doc.animation("a")["drawOrder"], "back to the setup order: a key without offsets"
    with pytest.raises(EditorError, match="every slot"):
        doc.set_draw_order_key("a", 0.1, ["s0"])
    assert doc.validate()[0] == []
    if NODE and CORE40.is_file():  # the official 4.0 runtime rebuilds the same orders
        out = tmp_path / "x.json"
        doc.export_spine_json(out, copy_images=False)
        r = subprocess.run([NODE, str(RUNTIME / "check_spine38.js"), str(CORE40), str(out), "--anim=a", "--full",
                            "0", "0.25", "0.5", "1"], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr[-800:]
        for sample in json.loads(r.stdout)["samples"]:
            assert sample["drawOrder"] == doc.draw_order_at("a", sample["time"])


def test_events(doc):
    doc.add_event("hit")
    doc.set_event("hit", int=3, string="boom", audio="hit.wav", volume=0.5)
    assert doc.data["events"]["hit"] == {"int": 3, "string": "boom", "audio": "hit.wav", "volume": 0.5}
    doc.key_event("a", "hit", 0.4)
    doc.key_event("a", "hit", 0.1)
    doc.set_event_key("a", 1, int=3, float=2.5)
    key = doc.event_keys("a")[1]
    assert "int" not in key and key["float"] == 2.5, "only values that differ from the event are kept"
    doc.set_event_key("a", 1, time=0.05)
    assert [k["time"] for k in doc.event_keys("a")] == [0.05, 0.1]
    doc.rename_event("hit", "impact")
    assert {k["name"] for k in doc.event_keys("a")} == {"impact"} and "impact" in doc.data["events"]
    doc.delete_event_key("a", 0)
    assert len(doc.event_keys("a")) == 1
    assert doc.validate()[0] == []
    doc.delete_event("impact")
    assert "events" not in doc.animation("a") and not doc.data["events"]
    doc.undo()
    assert doc.event_keys("a")


def test_windows(doc, qapp):
    doc.add_event("hit")
    doc.dirty = False
    win = EditorWindow(doc, settings_store=QSettings(tempfile.mktemp(suffix=".ini"), QSettings.Format.IniFormat))
    win.show()
    st = win.state
    st.set_animation("a")
    st.set_mode("animate")
    st.set_time(0.3)
    panel = win.events
    panel.events.selectRow(0)
    panel.key_event()
    assert doc.event_keys("a") == [{"time": 0.3, "name": "hit"}]
    panel.keys.item(0, 2).setText("7")  # int column of the key
    assert doc.event_keys("a")[0]["int"] == 7
    panel.events.item(0, 3).setText("bang")  # string column of the event
    assert doc.data["events"]["hit"]["string"] == "bang"
    st.set_time(0)
    panel.go_to(0)
    assert st.time == pytest.approx(0.3)
    win.tree.events_requested.emit()
    assert win.events_dock.isVisible()
    order_item = [win.tree.tree.topLevelItem(i) for i in range(win.tree.tree.topLevelItemCount())]
    assert any("animated" in i.text(0) for i in order_item), "Animate mode shows the animated draw order"
    doc.move_slot_keyed("a", 0.3, "s0", 3)
    assert doc.animation("a")["drawOrder"]
    doc.dirty = False
    win.close()
