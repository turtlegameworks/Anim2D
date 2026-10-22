"""Export an effect straight for game engines: skeleton data (JSON or binary) plus a packed atlas.

The normal package is meant for importing into the Spine editor (Spine 3.8 JSON and loose PNGs).
Runtimes (spine-unity, spine-godot, spine-cocos2dx, spine-phaser, spine-pixi …) need the skeleton data
and a texture atlas instead, so this generates the package in a temporary folder and exports it
through Anim2D's exporter, which writes <name>.json (or .skel), <name>.atlas and the page PNGs.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..formats.atlas import PackSettings
from ..formats.spine40 import STAMP_38, STAMP_40
from ..models.preset import PresetDefinition
from ..models.settings import EffectSettings
from .package import GenerationResult, write_package

ENGINE_NOTES = """# {name} for game engines

* `{data}`: the skeleton data (Spine {version} {kind}), animation `{animation}`.
* `{atlas}` and its PNG page(s): the texture atlas{pma}.

Load them with the official Spine runtime for your engine, version {version}.x:

* **Unity (spine-unity):** drop the folder into `Assets`; spine-unity makes the SkeletonData asset.
  It needs the atlas named `*.atlas.txt` (choose "Unity" when exporting).
* **Godot, Cocos2d-x, Phaser, PixiJS, libGDX, Unreal and others:** load `{data}` with `{atlas}`
  as the runtime's documentation shows.

The effect is one-shot: play `{animation}` once (or loop it) where you need it. It starts hidden
and hides itself at the end.
"""


@dataclass
class EngineExport:
    version: str = "4.0"  # "4.0" or "3.8"
    binary: bool = False  # .skel instead of .json (4.0 only)
    unity: bool = False  # atlas named .atlas.txt, as spine-unity needs
    premultiply_alpha: bool = False
    scale: float = 1.0
    max_page: int = 2048


def export_for_engine(preset: PresetDefinition, settings: EffectSettings, destination: Path,
                      options: EngineExport | None = None) -> GenerationResult:
    """Write <destination>/<name>_runtime/ with the data, atlas and pages. Never overwrites."""
    from ..editor.document import Document, EditorError  # the Anim2D exporter
    o = options or EngineExport()
    name = settings.instance_name
    destination = Path(destination)
    final_dir = destination / f"{name}_runtime"
    result = GenerationResult(ok=False)
    if final_dir.exists():
        result.report.errors.append(f"'{final_dir}' already exists. Choose a different instance name or remove "
                                    f"the old folder yourself.")
        return result
    if o.binary and o.version != "4.0":
        result.report.errors.append("Binary (.skel) export is Spine 4.0 only; choose JSON for Spine 3.8")
        return result
    work = Path(tempfile.mkdtemp(prefix="fx_engine_"))
    staging = destination / f".{name}_runtime.tmp-{uuid.uuid4().hex[:8]}"
    try:
        package = write_package(preset, settings, work)
        result.report.extend(package.report)
        result.stats = package.stats
        if not package.ok:
            return result
        doc, notes = Document.import_spine_json(package.output_dir / f"{name}.json", package.output_dir / "images")
        result.report.warnings.extend(notes)
        staging.mkdir(parents=True)
        out = staging / f"{name}{'.skel' if o.binary else '.json'}"
        atlas = PackSettings(premultiply_alpha=o.premultiply_alpha, scale=o.scale, max_width=o.max_page,
                             max_height=o.max_page, atlas_extension=".atlas.txt" if o.unity else ".atlas")
        try:
            warnings = doc.export_spine_json(out, stamp=STAMP_40 if o.version == "4.0" else STAMP_38,
                                             atlas=atlas, binary=o.binary, nonessential=False, pretty=False)
        except EditorError as exc:
            result.report.errors.append(str(exc))
            return result
        result.report.warnings.extend(warnings)
        (staging / "README_ENGINE.md").write_text(ENGINE_NOTES.format(
            name=name, data=out.name, atlas=f"{name}{atlas.atlas_extension}", version=o.version,
            kind="binary" if o.binary else "JSON", animation=f"{name}_play",
            pma=", premultiplied alpha" if o.premultiply_alpha else ""), encoding="utf-8")
        staging.rename(final_dir)
        result.ok = True
        result.output_dir = final_dir
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
