"""Import Spine editor project files (.spine) from Spine 3.8.x and 4.2.x.

A .spine file is a raw-deflate stream holding a header, the editor version and a Kryo-style
object graph: an object is a field count followed by (tag, value) pairs, and objects, strings,
lists and so on are written once and referenced afterwards. Spine does not publish the
format; the field tables in schema38/schema42 were worked out from projects and their JSON
and binary exports, and the import is checked against those exports.

load_spine_project() returns Spine JSON data: the 3.8 layout for 3.8 projects, and the 4.0
layout for 4.2 projects (their animations keep 4.x per-channel curves).
"""

from __future__ import annotations

import zlib
from pathlib import Path
from typing import Any

from . import reader as kp
from . import schema38, schema42
from .convert import convert

SUPPORTED = ("3.8", "4.2")


class SpineProjectError(ValueError):
    pass


def read_version(raw: bytes) -> str:
    r = kp.Reader(raw, 4)
    return r.kstring().strip()


def inflate(path: Path) -> bytes:
    try:
        return zlib.decompressobj(-15).decompress(Path(path).read_bytes())
    except (OSError, zlib.error) as exc:
        raise SpineProjectError(f"{Path(path).name} is not a Spine project file ({exc})") from None


def parse(raw: bytes) -> tuple[dict[str, Any], str]:
    """The project object (skeletons and fps) and the editor version."""
    try:
        version = read_version(raw)
    except (IndexError, UnicodeDecodeError):
        raise SpineProjectError("not a Spine project file") from None
    major = ".".join(version.split(".")[:2])
    if major not in SUPPORTED:
        raise SpineProjectError(f"Spine {version} projects are not supported yet (supported: 3.8.x and 4.2.x). "
                                "Export JSON from Spine and use File > Import Project with the .json instead.")
    try:
        if major == "4.2":
            r = kp.Reader(raw, 4, schema42.SCHEMA, schema42.CLASSES)
            r.kstring()
            r.varint()
            schema42.configure(r)
            project = kp.read_project42(r)
            project.setdefault(2, 30.0)  # 4.2 keeps the fps elsewhere; keys are in frames at 30 fps
        else:
            r = kp.Reader(raw, 4, schema38.SCHEMA, schema38.CLASSES)
            r.kstring()
            if raw[0] >= 0x11:
                r.varint()
            project = kp.read_project(r)
    except (kp.Unknown, IndexError, KeyError, AssertionError, UnicodeDecodeError, TypeError) as exc:
        raise SpineProjectError(f"could not read this Spine {version} project ({exc})") from None
    if 0 not in project or not project[0]:
        raise SpineProjectError("the project has no skeleton")
    return project, version


def load_spine_project(path: Path) -> tuple[dict[str, Any], list[str], str]:
    """(Spine JSON data, notes, editor version) for the project's first skeleton."""
    path = Path(path)
    project, version = parse(inflate(path))
    notes: list[str] = []
    skeletons = project[0]
    if len(skeletons) > 1:
        notes.append(f"The project has {len(skeletons)} skeletons; the first one was imported.")
    data = convert(project, version)
    data.pop("_anims", None)
    notes += data.pop("_notes", [])
    if version.startswith("4."):
        from ..spine40 import upgrade
        anims = data.pop("_animations40")
        data["animations"] = {}
        data = upgrade(data)
        data["animations"] = anims["animations"]
        notes += anims["notes"]
        constraints = [c for kind in ("ik", "transform", "path") for c in data.get(kind, [])]
        if any("order" not in c for c in constraints):  # 4.2 keeps the order elsewhere: list order
            for i, c in enumerate(constraints):
                c["order"] = i
        notes.append("Spine 4.2 projects: the constraint order and physics constraints are not imported.")
    return data, notes, version
