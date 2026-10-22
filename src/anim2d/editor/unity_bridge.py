"""Unity bridge: export the skeleton straight into a Unity project's Assets for spine-unity.

spine-unity imports a skeleton from <name>.json, <name>.atlas.txt and the atlas page PNGs in one
folder, and makes the SkeletonData asset itself. The bridge writes exactly those (packing the atlas),
optionally after every save, and touches a stamp file that a small editor script (installed into
the Unity project on request) watches to run AssetDatabase.Refresh, so Unity picks the change up
without having to be brought to the front.

Settings live in the project file: doc.project_settings["unity"].
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from ..formats.atlas import PackSettings
from ..formats.spine40 import STAMP_38, STAMP_40
from .document import Document, EditorError

STAMP_FILE = Path("Library") / "Anim2D" / "refresh.txt"  # outside Assets: Unity never imports it
SCRIPT_FILE = Path("Assets") / "Editor" / "Anim2DBridge.cs"
LEGACY_SCRIPT_FILE = Path("Assets") / "Editor" / "RigWorksBridge.cs"  # installed before the rename
LEGACY_SCRIPT_HEADER = "// Written by RigWorks 2D:"

SCRIPT = """// Written by Anim2D: refreshes the AssetDatabase when Anim2D exports into this project,
// so the change shows up without switching to Unity. Safe to delete.
#if UNITY_EDITOR
using System;
using System.IO;
using UnityEditor;

[InitializeOnLoad]
static class Anim2DBridge
{
    const string Stamp = "Library/Anim2D/refresh.txt";
    static DateTime seen = File.Exists(Stamp) ? File.GetLastWriteTimeUtc(Stamp) : DateTime.MinValue;
    static double next;

    static Anim2DBridge() { EditorApplication.update += Poll; }

    static void Poll()
    {
        if (EditorApplication.timeSinceStartup < next) return;
        next = EditorApplication.timeSinceStartup + 0.5;
        if (!File.Exists(Stamp)) return;
        var stamp = File.GetLastWriteTimeUtc(Stamp);
        if (stamp <= seen || EditorApplication.isCompiling || EditorApplication.isUpdating) return;
        seen = stamp;
        AssetDatabase.Refresh();
    }
}
#endif
"""


@dataclass
class UnitySettings:
    project: str = ""  # the Unity project folder (with Assets/ and ProjectSettings/)
    folder: str = "Assets/Spine"  # where in the project; the skeleton gets a subfolder of its own
    version: str = "4.0"  # data version for the spine-unity runtime: "4.0" or "3.8"
    premultiply_alpha: bool = True  # spine-unity's default materials expect PMA textures
    on_save: bool = False  # export every time the project is saved
    scale: float = 1.0

    @classmethod
    def of(cls, doc: Document) -> "UnitySettings":
        raw = doc.project_settings.get("unity", {})
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in names})

    def store(self, doc: Document) -> None:
        doc.project_settings["unity"] = asdict(self)
        doc.dirty = True


def is_unity_project(folder: Path) -> bool:
    folder = Path(folder)
    return (folder / "Assets").is_dir() and (folder / "ProjectSettings" / "ProjectVersion.txt").is_file()


def detect_runtime(project: Path) -> str | None:
    """The spine-unity runtime version in the project ("4.0", "3.8", "4.2" …), if it is installed."""
    project = Path(project)
    candidates = [*project.glob("Assets/**/spine-unity/version.txt"), *project.glob("Assets/Spine/version.txt"),
                  *project.glob("Packages/com.esotericsoftware.spine.spine-unity*/package.json"),
                  *project.glob("Library/PackageCache/com.esotericsoftware.spine.spine-unity*/package.json")]
    for file in candidates:
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if file.suffix == ".json":
            try:
                text = str(json.loads(text).get("version", ""))
            except json.JSONDecodeError:
                pass
        m = re.search(r"(\d)\.(\d+)", text)
        if m:
            return f"{m.group(1)}.{m.group(2)}"
    manifest = project / "Packages" / "manifest.json"
    if manifest.is_file():
        m = re.search(r'"com\.esotericsoftware\.spine\.spine-unity"\s*:\s*"[^"]*?(\d)\.(\d+)', manifest.read_text("utf-8"))
        if m:
            return f"{m.group(1)}.{m.group(2)}"
    return None


def target_folder(doc: Document, s: UnitySettings) -> Path:
    if not s.project:
        raise EditorError("Choose the Unity project folder first")
    project = Path(s.project)
    if not is_unity_project(project):
        raise EditorError(f"{project} is not a Unity project (it needs Assets/ and ProjectSettings/)")
    folder = Path(s.folder.replace("\\", "/").strip("/") or "Assets")
    if folder.is_absolute() or folder.parts[0] != "Assets" or ".." in folder.parts:
        raise EditorError("The folder must be inside Assets/ (for example Assets/Spine)")
    return project / folder / _skeleton_file_name(doc)


def _skeleton_file_name(doc: Document) -> str:
    base = doc.path.stem if doc.path else "skeleton"
    if len(doc.skeleton_names()) > 1:
        base = doc.skeleton_name
    return re.sub(r"[^\w\-. ]+", "_", base).strip(" .") or "skeleton"


def export_to_unity(doc: Document, s: UnitySettings | None = None) -> tuple[Path, list[str]]:
    """Write <name>.json, <name>.atlas.txt and the page PNGs into the Unity project and ask Unity to
    refresh. Returns (the .json written, warnings)."""
    s = s or UnitySettings.of(doc)
    folder = target_folder(doc, s)
    folder.mkdir(parents=True, exist_ok=True)
    name = folder.name
    out = folder / f"{name}.json"
    stamp = STAMP_38 if s.version.startswith("3") else STAMP_40
    atlas = PackSettings(premultiply_alpha=s.premultiply_alpha, scale=s.scale, atlas_extension=".atlas.txt",
                         max_width=4096, max_height=4096)
    warnings = doc.export_spine_json(out, stamp=stamp, overwrite=True, atlas=atlas)
    runtime = detect_runtime(Path(s.project))
    if runtime and runtime[:3] != s.version[:3]:
        warnings.append(f"The Unity project has spine-unity {runtime}, but the data is exported for {s.version}")
    touch_refresh(Path(s.project))
    return out, warnings


def touch_refresh(project: Path) -> None:
    stamp = Path(project) / STAMP_FILE
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(f"{time.time()}\n", encoding="utf-8")


def install_refresh_script(project: Path) -> Path:
    """Put the auto-refresh editor script into the Unity project (Assets/Editor/Anim2DBridge.cs)."""
    if not is_unity_project(project):
        raise EditorError(f"{project} is not a Unity project")
    script = Path(project) / SCRIPT_FILE
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(SCRIPT, encoding="utf-8")
    _remove_legacy_script(Path(project))
    return script


def _remove_legacy_script(project: Path) -> None:
    """Remove the pre-rename refresh script (and its .meta) so only one runs; only if it is ours."""
    old = project / LEGACY_SCRIPT_FILE
    try:
        if not old.read_text(encoding="utf-8").startswith(LEGACY_SCRIPT_HEADER):
            return
    except (OSError, UnicodeDecodeError):
        return
    old.unlink(missing_ok=True)
    old.with_name(old.name + ".meta").unlink(missing_ok=True)
