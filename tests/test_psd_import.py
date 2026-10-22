"""PSD import: layers become slots and attachments in PSD stacking order."""

from __future__ import annotations

import pytest
from PIL import Image

from anim2d.editor.document import Document, EditorError
from anim2d.editor.media_export import MediaExport, write_psd
from anim2d.editor.psd_import import import_psd


def test_import_psd(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    top = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    top.paste((255, 0, 0, 255), (0, 0, 10, 10))
    body = Image.new("RGBA", (40, 20), (0, 0, 255, 255))
    psd = tmp_path / "hero.psd"
    s = MediaExport()
    s.transparent = True
    write_psd([top, body], ["head", "body"], psd, s)

    doc = Document(images_dir=images)
    result = import_psd(doc, psd)
    assert result["slots"] == ["body", "head"], "bottom layer is drawn first"
    assert Image.open(images / "hero" / "head.png").getpixel((0, 0)) == (255, 0, 0, 255)
    assert Image.open(images / "hero" / "head.png").getpixel((20, 15))[3] == 0
    assert Image.open(images / "hero" / "body.png").getpixel((20, 15)) == (0, 0, 255, 255)
    assert doc.attachments("head")["head"]["path"] == "hero/head"
    assert doc.validate()[0] == []
    doc.undo()
    assert not any(s["name"] == "head" for s in doc.slots)


def test_needs_images_folder(tmp_path):
    with pytest.raises(EditorError, match="images folder"):
        import_psd(Document(), tmp_path / "x.psd")
