"""Ctrl+click multi-bone selection, the Show bones toggle (Ctrl+H) and Playblast."""

from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QPointF, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from anim2d.editor import playblast as pb  # noqa: E402
from anim2d.editor.document import Document, EditorError  # noqa: E402
from anim2d.editor.main_window import EditorWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    doc = Document()
    doc.add_bone("root", "a", x=-80, y=40, rotation=0, length=60)
    doc.add_bone("root", "b", x=80, y=40, rotation=90, length=60)
    doc.add_bone("a", "a2", x=60, y=0, length=40)
    doc.add_animation("walk")
    doc.set_bone_key("walk", "a", "rotate", 0.0, value=0)
    doc.set_bone_key("walk", "a", "rotate", 0.5, value=45)
    doc.dirty = False
    w = EditorWindow(doc, settings_store=QSettings(tempfile.mktemp(suffix=".ini"), QSettings.Format.IniFormat))
    w.resize(1200, 850)
    w.show()
    w.viewport.frame_all()
    qapp.processEvents()
    yield w
    w.doc.dirty = False
    w.close()


def click(widget, pos: QPointF, mods=Qt.KeyboardModifier.NoModifier):
    for kind, buttons in ((QMouseEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
                          (QMouseEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton)):
        QApplication.sendEvent(widget, QMouseEvent(kind, pos, widget.mapToGlobal(pos), Qt.MouseButton.LeftButton,
                                                   buttons, mods))


def bone_point(win, bone: str, along: float = 0.5) -> QPointF:
    from anim2d.editor import affine
    m = win.state.evaluator().bone_world(win.state.time)[bone]
    length = win.doc.bone(bone).get("length", 0)
    return win.viewport.to_screen(*affine.apply(m, length * along, 0))


def test_state_toggle_bone(win):
    st = win.state
    st.select("bone", "a")
    st.toggle_bone("b")
    assert st.selected_bones() == ["b", "a"]
    st.toggle_bone("b")
    assert st.selected_bones() == ["a"]
    st.toggle_bone("b")
    st.select("bone", "a")  # a plain selection drops the others
    assert st.selected_bones() == ["a"]
    st.toggle_bone("a")
    assert st.selection.empty


def test_ctrl_click_in_viewport_adds_bones(win):
    vp = win.viewport
    click(vp, bone_point(win, "a"))
    assert win.state.selected_bones() == ["a"]
    click(vp, bone_point(win, "b"), Qt.KeyboardModifier.ControlModifier)
    assert set(win.state.selected_bones()) == {"a", "b"}
    click(vp, bone_point(win, "b"), Qt.KeyboardModifier.ControlModifier)
    assert win.state.selected_bones() == ["a"]


def test_rotate_moves_every_selected_bone(win):
    st = win.state
    st.select("bone", "a")
    st.toggle_bone("b")
    before = {b: st.evaluator().bone_locals(0)[b][2] for b in ("a", "b")}
    vp = win.viewport
    ox, oy = vp.state.evaluator().bone_world(0)["b"][4:6]
    assert vp.begin_drag(ox + 50, oy, "rotate")
    vp.drag_to(ox, oy + 50)
    vp.end_drag()
    after = {b: st.evaluator().bone_locals(0)[b][2] for b in ("a", "b")}
    for b in ("a", "b"):
        assert after[b] - before[b] == pytest.approx(90, abs=0.5)
    win.doc.undo()  # one undo step for the group
    assert st.evaluator().bone_locals(0)["a"][2] == pytest.approx(before["a"])


def test_translate_does_not_move_a_child_twice(win):
    st = win.state
    st.select("bone", "a")
    st.toggle_bone("a2")
    x_child = st.doc.bone("a2").get("x", 0)
    vp = win.viewport
    assert vp.begin_drag(0, 0, "translate")
    vp.drag_to(10, 0)
    vp.end_drag()
    assert st.doc.bone("a2").get("x", 0) == pytest.approx(x_child)  # moved with its parent only
    assert st.doc.bone("a")["x"] == pytest.approx(-70)


def test_key_and_delete_selected_bones(win):
    st = win.state
    st.set_mode("animate")
    st.set_animation("walk")
    st.select("bone", "b")
    st.toggle_bone("a2")
    st.key_selected_bone(("rotate",))
    bones = st.doc.animation("walk")["bones"]
    assert "rotate" in bones["b"] and "rotate" in bones["a2"]
    st.set_mode("setup")
    st.select("bone", "a")
    st.toggle_bone("b")
    win.delete_selected()
    assert not st.doc.has_bone("a") and not st.doc.has_bone("b") and not st.doc.has_bone("a2")
    win.doc.undo()
    assert st.doc.has_bone("a") and st.doc.has_bone("b")


def test_show_bones_toggle(win):
    assert win.state.view_options["bones"]["visible"]
    win.show_bones_action.trigger()
    assert not win.state.view_options["bones"]["visible"]
    assert not win.bones_button_action.isChecked()
    assert win.viewport.pick(bone_point(win, "a")) is None or win.viewport.pick(bone_point(win, "a"))[0] != "bone"
    win.bones_button_action.trigger()
    assert win.state.view_options["bones"]["visible"] and win.show_bones_action.isChecked()


def test_playblast_formats(win, tmp_path):
    st = win.state
    mode, time = st.mode, st.time
    base = dict(folder=str(tmp_path), animation="walk", scale=25, fps=10)
    files = pb.playblast(win.viewport, pb.PlayblastSettings(format="png", name="seq", **base))
    assert len(files) == 6 and all(f.is_file() for f in files)  # frames 0..5 of a 0.5 s animation at 10 fps
    w, h = pb.capture_size(win.viewport, pb.PlayblastSettings(scale=25))
    assert Image.open(files[0]).size == (w, h)
    for fmt in ("gif", "avi", "mov", "webp", "apng", "jpeg"):
        out = pb.playblast(win.viewport, pb.PlayblastSettings(format=fmt, name=f"clip_{fmt}", **base))
        assert out[0].is_file() and out[0].stat().st_size > 0, fmt
    gif = Image.open(tmp_path / "clip_gif.gif")
    assert gif.n_frames == 6
    custom = pb.playblast(win.viewport, pb.PlayblastSettings(format="png", name="part", range="custom", start=2,
                                                             end=3, **base))
    assert len(custom) == 2
    with pytest.raises(EditorError):  # no overwrite unless asked
        pb.playblast(win.viewport, pb.PlayblastSettings(format="gif", name="clip_gif", **base))
    assert (st.mode, st.time) == (mode, time) and win.viewport.capture is None


def test_playblast_dialog_runs(win, tmp_path):
    from anim2d.editor.playblast_dialog import PlayblastDialog
    dialog = PlayblastDialog(win.viewport, win, store=win.store)
    dialog.folder.setText(str(tmp_path))
    dialog.name.setText("dlg")
    dialog.format.setCurrentIndex(dialog.format.findData("gif"))
    dialog.scale.setCurrentIndex(dialog.scale.findData(25))
    dialog.open_after.setChecked(False)
    assert "dlg.gif" in dialog.path_label.text()
    dialog.run()
    assert (tmp_path / "dlg.gif").is_file()
    again = PlayblastDialog(win.viewport, win, store=win.store)  # the choices are remembered
    assert again.format.currentData() == "gif" and again.folder.text() == str(tmp_path)


def _old_way(win, s):
    """What the playblast wrote before it streamed: every frame painted first, converted with
    to_pil and handed to the media export's writers."""
    from anim2d.editor import media_export as mx
    images = [img.copy() for img in pb.frames(win.viewport, s.animation, s)]
    _, _, fps = pb.frame_range(win.doc, s.animation, s)
    settings = pb._media_settings(s, fps)
    return [mx.to_pil(i) for i in images], settings


@pytest.mark.parametrize("fmt", ["avi", "mov", "gif", "webp", "apng", "png", "jpeg"])
def test_streamed_playblast_writes_the_same_bytes(win, tmp_path, fmt):
    from anim2d.editor import media_export as mx
    s = pb.PlayblastSettings(format=fmt, folder=str(tmp_path / "new"), name="clip", animation="walk", scale=25,
                             fps=10, repeat=2, bones=True, grid=True)
    files = pb.playblast(win.viewport, s)
    frames, settings = _old_way(win, s)
    assert len(frames) == 12
    ref = tmp_path / f"ref{files[0].suffix}"
    if fmt in ("png", "jpeg"):
        assert len(files) == 12
        for f, img in zip(files, frames):
            flat = mx.flatten(img, "#000000")
            if fmt == "jpeg":
                flat.save(ref, "JPEG", quality=settings.jpeg_quality)
            else:
                flat.save(ref, "PNG", compress_level=6)
            assert f.read_bytes() == ref.read_bytes(), f.name
    else:
        getattr(mx, f"write_{fmt}")(frames, ref, settings)
        assert files[0].read_bytes() == ref.read_bytes()


@pytest.mark.parametrize("fmt", ["png", "mov", "avi", "gif"])
def test_cancelling_a_playblast_leaves_no_files(win, tmp_path, fmt):
    st = win.state
    before = (st.mode, st.animation, st.time)
    s = pb.PlayblastSettings(format=fmt, folder=str(tmp_path), name="cut", animation="walk", scale=25, fps=20)
    with pytest.raises(EditorError, match="cancelled"):
        pb.playblast(win.viewport, s, lambda done, total: done < 4)
    assert list(tmp_path.iterdir()) == []
    assert (st.mode, st.animation, st.time) == before and win.viewport.capture is None


def test_frames_are_painted_as_they_are_written(win, tmp_path, monkeypatch):
    """Streaming: the writer never has more than a few painted frames waiting."""
    painted, written = [], []
    real_frames = pb.frames

    def counting(*args, **kwargs):
        for image in real_frames(*args, **kwargs):
            painted.append(1)
            yield image
    monkeypatch.setattr(pb, "frames", counting)
    real_add = pb.mx.MovWriter.add

    def add(self, sample):
        written.append(len(painted))
        real_add(self, sample)
    monkeypatch.setattr(pb.mx.MovWriter, "add", add)
    s = pb.PlayblastSettings(format="mov", folder=str(tmp_path), name="m", animation="walk", scale=25, fps=60)
    pb.playblast(win.viewport, s)
    assert len(written) == 31
    assert max(p - i for i, p in enumerate(written)) <= 2 * pb._workers() + 1


def test_mp4_playblast_streams_frames(win, tmp_path, qapp):
    """Like the dialog, the progress callback runs the event loop while frames paint, so the
    encoder's ready signal can arrive inside the frame generator: it must not send twice."""
    def progress(done, total):
        qapp.processEvents()
        return True
    from PySide6.QtMultimedia import QMediaFormat
    encode = QMediaFormat.ConversionMode.Encode
    if QMediaFormat.FileFormat.MPEG4 not in QMediaFormat().supportedFileFormats(encode) or \
            not QMediaFormat(QMediaFormat.FileFormat.MPEG4).supportedVideoCodecs(encode):
        pytest.skip("Qt Multimedia can't encode MP4 video on this system")  # asked, not guessed from an error
    s = pb.PlayblastSettings(format="mp4", folder=str(tmp_path), name="v", animation="walk", scale=25, fps=30)
    files = pb.playblast(win.viewport, s, progress)  # any failure fails the test
    assert files[0].is_file() and files[0].stat().st_size > 0


@pytest.mark.parametrize("kind", ["avi", "mov"])
def test_a_writer_that_fails_to_finish_leaves_no_file(tmp_path, monkeypatch, kind):
    from anim2d.editor import media_export as mx
    writer = mx.AviWriter if kind == "avi" else mx.MovWriter

    def failing_close(self, hold=0):
        self.file.write(b"partial")
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(writer, "close", failing_close)
    frames = [Image.new("RGBA", (8, 6), (255, 0, 0, 255))] * 3
    out = tmp_path / f"clip.{kind}"
    with pytest.raises(OSError):
        getattr(mx, f"write_{kind}")(frames, out, mx.MediaExport(kind=kind, fps=10, encoding="mjpeg" if kind == "avi" else "png"))
    assert not out.exists()


@pytest.mark.parametrize("kind", ["avi", "mov"])
def test_aborting_removes_the_file_even_when_closing_fails(tmp_path, kind):
    """A write that keeps failing (a full disk) also fails when abort() closes the file: the file
    is still removed, and abort() raises nothing over the error that caused it."""
    from anim2d.editor import media_export as mx
    out = tmp_path / f"clip.{kind}"
    writer = (mx.AviWriter if kind == "avi" else mx.MovWriter)(out, 8, 6, mx.MediaExport(kind=kind, fps=10))
    writer.add(b"frame")
    real = writer.file

    class Full:
        def write(self, data):
            raise OSError(28, "No space left on device")

        def seek(self, *args):
            raise OSError(28, "No space left on device")

        def close(self):
            real.close()  # the descriptor goes, but flushing what is buffered fails
            raise OSError(28, "No space left on device")
    writer.file = Full()
    with pytest.raises(OSError):
        writer.close()
    writer.abort()
    assert not out.exists()


def test_filesystem_failures_are_editor_errors(win, tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    s = pb.PlayblastSettings(format="png", folder=str(blocker / "sub"), name="x", animation="walk", scale=25, fps=10)
    with pytest.raises(EditorError, match="Cannot write the playblast"):
        pb.playblast(win.viewport, s)

    def full(*args):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(pb, "_save_frame", full)
    s = pb.PlayblastSettings(format="png", folder=str(tmp_path / "out"), name="x", animation="walk", scale=25, fps=10)
    with pytest.raises(EditorError, match="No space left"):
        pb.playblast(win.viewport, s)
    assert not any((tmp_path / "out").glob("*.png"))
    assert win.viewport.capture is None


def test_the_dialog_notes_follow_repeat_and_version(win, tmp_path):
    from anim2d.editor.playblast_dialog import PlayblastDialog
    dialog = PlayblastDialog(win.viewport, win, store=win.store)
    dialog.format.setCurrentIndex(dialog.format.findData("apng"))
    dialog.scale.setCurrentIndex(dialog.scale.findData(200))
    dialog.repeat.setValue(1)
    before = dialog.path_label.text()
    dialog.repeat.setValue(50)
    assert dialog.path_label.text() != before and "keeps every frame in memory" in dialog.path_label.text()

    from anim2d.editor import unity_bridge
    from anim2d.editor.unity_dialog import UnityDialog
    project = tmp_path / "Unity"
    (project / "Assets" / "Spine").mkdir(parents=True)
    (project / "ProjectSettings").mkdir()
    (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3")
    (project / "Assets" / "Spine" / "version.txt").write_text("This Spine-Unity runtime works with data exported from Spine Editor version: 4.2.xx")
    assert unity_bridge.detect_runtime(project) == "4.2"
    unity = UnityDialog(win.doc, win)
    unity.version.setCurrentText("4.0")
    unity.project.setText(str(project))
    assert "Choose spine-unity version 4.2" in unity.info.text()
    unity.version.setCurrentText("4.2")
    assert "Choose spine-unity version" not in unity.info.text()


def test_memory_note_for_formats_that_keep_every_frame(win):
    s = pb.PlayblastSettings(format="apng", animation="walk", fps=30, scale=100)
    w, h = pb.capture_size(win.viewport, s)
    assert pb.memory_needed(win.doc, "walk", s, win.viewport) == pb.frame_count(win.doc, "walk", s) * w * h * 8
    for fmt in ("mp4", "mov", "avi", "png", "jpeg"):
        assert pb.memory_needed(win.doc, "walk", pb.PlayblastSettings(format=fmt, animation="walk"), win.viewport) == 0


def test_show_bones_shortcut_avoids_the_mac_hide_app_key():
    import sys
    from anim2d.editor import main_window
    assert main_window.BONES_SHORTCUT == ("Meta+H" if sys.platform == "darwin" else "Ctrl+H")


def test_ctrl_click_off_the_bones_works_like_a_click(win):
    """Ctrl+click only gathers bones: on empty space it clears the selection as a plain click does."""
    vp = win.viewport
    click(vp, bone_point(win, "a"))
    click(vp, QPointF(5, 5), Qt.KeyboardModifier.ControlModifier)
    assert win.state.selection.empty and win.state.selected_bones() == []
