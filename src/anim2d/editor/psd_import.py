"""PSD import: every layer becomes an image, a slot and an attachment placed where it sits in the PSD.

Like Spine's PhotoshopToSpine script: layers keep their stacking (bottom layer drawn first), the
canvas centre becomes the skeleton origin, and the layer images go into a folder named after the
PSD inside the images folder. The layers are read here (8-bit RGB/grayscale, raw or PackBits), so no
extra package is needed.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import Any

from PIL import Image

from .document import Document, EditorError


def _safe(name: str) -> str:
    return re.sub(r"[^\w\-. ]+", "_", name).strip(" .") or "layer"


def _unpack_bits(data: bytes, size: int) -> bytes:
    out = bytearray()
    i = 0
    while len(out) < size and i < len(data):
        n = data[i]
        i += 1
        if n < 128:
            out += data[i:i + n + 1]
            i += n + 1
        elif n > 128:
            out += bytes([data[i]]) * (257 - n)
            i += 1
    return bytes(out[:size]).ljust(size, bytes(1))


def _channel(f, width: int, height: int, length: int) -> bytes:
    """One layer channel's pixels (raw or PackBits)."""
    end = f.tell() + length
    compression = struct.unpack(">H", f.read(2))[0]
    if width * height == 0:
        f.seek(end)
        return b""
    if compression == 0:
        data = f.read(width * height)
    elif compression == 1:
        counts = struct.unpack(f">{height}H", f.read(2 * height))
        data = b"".join(_unpack_bits(f.read(c), width) for c in counts)
    else:
        raise EditorError("This PSD uses ZIP-compressed layers; save it without compression (or as PSD from Photoshop)")
    f.seek(end)
    return data


def psd_layers(path: Path) -> tuple[tuple[int, int], list[tuple[str, tuple[int, int, int, int], Image.Image]]]:
    """((canvas width, height), [(layer name, (left, top, right, bottom), RGBA image)]) bottom first.
    Hidden layers are kept (Spine's script imports them too); empty layers and group markers are left out."""
    with open(path, "rb") as f:
        header = f.read(26)
        if header[:4] != b"8BPS":
            raise EditorError(f"{Path(path).name} is not a Photoshop file")
        version, channels, height, width, depth, mode = struct.unpack(">H6xHIIHH", header[4:])
        if version != 1:
            raise EditorError("Large documents (PSB) are not supported")
        if depth != 8 or mode not in (1, 3):
            raise EditorError("Only 8-bit RGB or grayscale PSDs can be imported")
        for _ in range(2):  # colour mode data, image resources
            f.seek(struct.unpack(">I", f.read(4))[0], 1)
        struct.unpack(">I", f.read(4))  # layer and mask section length
        info_length = struct.unpack(">I", f.read(4))[0]
        if not info_length:
            raise EditorError(f"{Path(path).name} has no layers (flattened PSDs can't be split)")
        count = abs(struct.unpack(">h", f.read(2))[0])
        records = []
        for _ in range(count):
            top, left, bottom, right, n = struct.unpack(">iiiiH", f.read(18))
            chans = [struct.unpack(">hI", f.read(6)) for _ in range(n)]
            f.read(12)  # blend signature, blend mode, opacity, clipping, flags, filler
            extra_end = f.tell() + 4 + struct.unpack(">I", f.read(4))[0]
            f.seek(struct.unpack(">I", f.read(4))[0], 1)  # mask
            f.seek(struct.unpack(">I", f.read(4))[0], 1)  # blending ranges
            raw = f.read(1)[0]
            name = f.read(raw).decode("latin-1")
            f.read(-(raw + 1) % 4)
            group = False
            while f.tell() + 12 <= extra_end:
                sig, key, size = struct.unpack(">4s4sI", f.read(12))
                if sig not in (b"8BIM", b"8B64"):
                    break
                block = f.read(size + size % 2)
                if key == b"luni":
                    chars = struct.unpack(">I", block[:4])[0]
                    name = block[4:4 + 2 * chars].decode("utf-16-be")
                elif key in (b"lsct", b"lsdk"):
                    group = True
            f.seek(extra_end)
            records.append((name, (left, top, right, bottom), chans, group))
        out = []
        for name, (left, top, right, bottom), chans, group in records:
            w, h = right - left, bottom - top
            planes = {cid: _channel(f, w if cid != -2 else 0, h if cid != -2 else 0, length) for cid, length in chans}
            if group or w <= 0 or h <= 0:
                continue
            def band(cid, default):
                data = planes.get(cid)
                return Image.frombytes("L", (w, h), data) if data else Image.new("L", (w, h), default)
            gray = band(0, 0)
            rgb = [gray, gray, gray] if mode == 1 else [band(0, 0), band(1, 0), band(2, 0)]
            img = Image.merge("RGBA", (*rgb, band(-1, 255)))
            if img.getbbox() is None:
                continue
            out.append((name, (left, top, right, bottom), img))
    if not out:
        raise EditorError(f"{Path(path).name} has no layers with pixels")
    return (width, height), out


def ora_layers(path: Path) -> tuple[tuple[int, int], list[tuple[str, tuple[int, int, int, int], Image.Image]]]:
    """OpenRaster (.ora, saved by Krita and GIMP): a zip with stack.xml and one PNG per layer.
    Same result as psd_layers: canvas size and layers bottom first."""
    import io
    import xml.etree.ElementTree as ET
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("stack.xml"))
            size = (int(root.get("w", 0)), int(root.get("h", 0)))
            out = []

            def walk(stack, dx: int, dy: int) -> None:
                for node in stack:  # the first child is the top layer
                    x, y = dx + int(float(node.get("x", 0))), dy + int(float(node.get("y", 0)))
                    if node.tag == "stack":
                        walk(node, x, y)
                    elif node.tag == "layer" and node.get("src"):
                        with Image.open(io.BytesIO(z.read(node.get("src")))) as img:
                            img = img.convert("RGBA")
                        if img.getbbox() is None:
                            continue
                        out.append((node.get("name") or Path(node.get("src")).stem,
                                    (x, y, x + img.width, y + img.height), img))
            stack = root.find("stack")
            walk(stack if stack is not None else [], 0, 0)
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise EditorError(f"{Path(path).name} is not a readable OpenRaster file: {exc}") from None
    if not out:
        raise EditorError(f"{Path(path).name} has no layers with pixels")
    return size, out[::-1]


LAYER_FILES = "Layered images (*.psd *.ora)"


def read_layers(path: Path):
    """Canvas size and layers (bottom first) of a .psd or .ora file."""
    if Path(path).suffix.lower() == ".ora":
        return ora_layers(path)
    return psd_layers(path)


def _sources(doc: Document) -> dict[str, Any]:
    return doc.project_settings.setdefault("layer_sources", {})


def _place(doc: Document, slot: str, att: str, box, canvas, scale: float) -> None:
    left, top, right, bottom = box
    width, height = canvas
    cx, cy = (left + right) / 2 - width / 2, height / 2 - (top + bottom) / 2
    fields = {"x": round(cx * scale, 3), "y": round(cy * scale, 3)}
    if scale != 1:
        fields.update(scaleX=scale, scaleY=scale)
    doc.set_attachment(slot, att, record=False, **fields)


def import_psd(doc: Document, path: Path, bone: str | None = None, scale: float = 1.0,
               record: bool = True) -> dict[str, Any]:
    """Add a .psd/.ora file's layers as slots on `bone` (default the root). Remembers which slot and
    image each layer became, so resync_layers can update them when the file changes.
    Returns {'slots': [...], 'folder': ...}."""
    path = Path(path).resolve()
    if not doc.images_dir:
        raise EditorError("Set an images folder first: the layer images are saved there")
    bone = bone or doc.root_name
    doc.bone(bone)
    canvas, layers = read_layers(path)
    folder = _safe(path.stem)
    target = doc.images_dir / folder
    target.mkdir(parents=True, exist_ok=True)
    if record:
        doc.checkpoint(f"Import {path.name}")
    source = {"folder": folder, "bone": bone, "scale": scale, "layers": {}}
    used: set[str] = set()
    slots = []
    for name, box, img in layers:
        slots.append(_add_layer(doc, source, name, box, img, canvas, target, used))
    _sources(doc)[str(path)] = source
    doc.dirty = True
    doc.notify()
    return {"slots": slots, "folder": folder}


def _add_layer(doc: Document, source: dict[str, Any], name: str, box, img, canvas, target: Path,
               used: set[str]) -> str:
    base = _safe(name)
    file = base
    n = 2
    taken = used | {Path(f).stem.lower() for f in (v["image"] for v in source["layers"].values())}
    while file.lower() in taken:
        file, n = f"{base}_{n}", n + 1
    used.add(file.lower())
    img.save(target / f"{file}.png")
    image = f"{source['folder']}/{file}"
    bone = source["bone"] if doc.has_bone(source["bone"]) else doc.root_name
    slot = doc.add_slot(bone, doc.unique_name(base, {s["name"] for s in doc.slots}), record=False)
    att = doc.add_attachment(slot, image, record=False, set_visible=True)
    _place(doc, slot, att, box, canvas, source["scale"])
    key = name if name not in source["layers"] else f"{name}#{len(source['layers'])}"
    source["layers"][key] = {"slot": slot, "attachment": att, "image": image, "box": list(box)}
    return slot


def resync_layers(doc: Document, path: Path, record: bool = True) -> dict[str, list[str]]:
    """Read the layered file again: changed layers get their new pixels and size, and move by as much
    as they moved in the file (so positions you changed in Anim2D, bones, weights and keys stay).
    New layers are added as slots. Layers gone from the file are left alone (and listed).
    Returns {'updated': [...], 'added': [...], 'missing': [...]}."""
    path = Path(path).resolve()
    source = _sources(doc).get(str(path))
    if source is None:
        raise EditorError(f"{path.name} wasn't imported into this project (File > Import PSD / OpenRaster)")
    if not any(doc.has_slot(info["slot"]) for info in source["layers"].values()):
        del _sources(doc)[str(path)]  # the import was undone or its slots deleted: stop following the file
        raise EditorError(f"None of {path.name}'s layers are in the project any more; import it again to follow it")
    canvas, layers = read_layers(path)
    target = doc.images_dir / source["folder"]
    target.mkdir(parents=True, exist_ok=True)
    if record:
        doc.checkpoint(f"Update layers from {path.name}")
    result = {"updated": [], "added": [], "missing": []}
    seen = set()
    used: set[str] = set()
    for name, box, img in layers:
        info = source["layers"].get(name)
        seen.add(name)
        if info is None or not doc.has_slot(info["slot"]) or info["attachment"] not in doc.attachments(info["slot"]):
            source["layers"].pop(name, None)
            result["added"].append(_add_layer(doc, source, name, box, img, canvas, target, used))
            continue
        img.save(doc.images_dir / f"{info['image']}.png")
        old = info["box"]
        moved = ((box[0] + box[2]) - (old[0] + old[2])) / 2, ((box[1] + box[3]) - (old[1] + old[3])) / 2
        att = doc.attachments(info["slot"])[info["attachment"]]
        scale = source["scale"]
        fields = {"width": img.width, "height": img.height}
        if moved != (0, 0):  # image space is y down
            fields.update(x=round(att.get("x", 0) + moved[0] * scale, 3), y=round(att.get("y", 0) - moved[1] * scale, 3))
        doc.set_attachment(info["slot"], info["attachment"], record=False, **fields)
        info["box"] = list(box)
        result["updated"].append(info["slot"])
    result["missing"] = [name for name in source["layers"] if name not in seen]
    doc.dirty = True
    doc.notify()
    return result


def layer_sources(doc: Document) -> list[Path]:
    """The layered files this project was imported from (for watching them)."""
    return [Path(p) for p in doc.project_settings.get("layer_sources", {})]
