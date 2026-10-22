<div align="center">

<img src="src/anim2d/assets/anim2d.ico" width="96" alt="Anim2D icon">

# Anim2D

**A free, open-source 2D skeletal animation editor and FX builder that reads and writes Spine data.**

[![CI](https://github.com/turtlegameworks/Anim2D/actions/workflows/ci.yml/badge.svg)](https://github.com/turtlegameworks/Anim2D/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/turtlegameworks/Anim2D?include_prereleases)](https://github.com/turtlegameworks/Anim2D/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078d4)

[Download](#install) · [Editor guide](docs/editor.md) · [FX presets](docs/fx_presets.md) · [Contributing](CONTRIBUTING.md)

</div>

![Anim2D editor](docs/screenshots/editor_editor.png)

Anim2D lets you rig and animate 2D characters with bones, meshes and constraints, then export them for
the official [Spine runtimes](https://esotericsoftware.com/spine-runtimes) (Unity, Godot, Cocos, Phaser,
Pixi, libGDX and more). It also comes with an FX builder that turns 37 ready-made effects into editable
skeletons you can tweak, insert into a character, or take into the Spine editor.

It is a single Windows program with two modes:

* **Anim2D**, the editor. Bones, slots, region and mesh attachments, IK / transform / path constraints,
  skins, weight painting, mesh tracing, a dopesheet and graph editor, crossfade preview, texture packing
  and GIF / MP4 / PNG-sequence export.
* **Anim2D FX Builder**. Pick a preset (impacts, slashes, explosions, coin bursts, hearts, level-ups,
  UI pings…), adjust it with live preview, and generate a Spine package or insert it straight into the
  editor.

## Features

| | |
| --- | --- |
| **Spine interchange** | Opens Spine 3.8 and 4.0 JSON and `.spine` projects (3.8.x, 4.2.x). Exports Spine 4.0 JSON or binary `.skel`, or 3.8 JSON, with an optional packed `.atlas` |
| **Rigging** | Bones with shear and inheritance, slots, draw order, skins with their own bones and constraints, IK, transform and path constraints, bounding boxes, clipping, points |
| **Meshes** | Mesh editing and tracing from alpha, weight painting, a Spine-style weights view, linked meshes, deform keys |
| **Animation** | Dopesheet and bezier graph editor, ease presets, retiming, motion paths, onion skinning, crossfade preview, events with audio, reference footage for rotoscoping |
| **Tools** | Auto-rig, physics baking, mirror, pose tools, frame sequences, PSD / OpenRaster import with live resync, command search, customisable shortcuts, themes |
| **Export** | Texture packer and unpacker, GIF, APNG, WebP, MP4, AVI, MOV, PSD and PNG / JPEG sequences, plus a Unity bridge that exports straight into a spine-unity project |
| **FX** | 37 parametric presets ([catalogue](docs/fx_presets.md)), your own templates registered from Spine exports ([round trip](docs/template_round_trip.md)), deterministic seeded generation |

Exports are checked against the official spine-ts runtimes in the test suite. See
[compatibility](docs/compatibility.md) for exactly what is and isn't verified yet (for example, import
into the Spine editor itself is a manual check).

## Install

1. Download `Anim2D-Setup-<version>.exe` from the [latest release](https://github.com/turtlegameworks/Anim2D/releases/latest).
2. Run it. It installs for your user only (no administrator rights) into
   `%LOCALAPPDATA%\Programs\Anim2D` and adds Start-menu shortcuts for **Anim2D** and
   **Anim2D FX Builder**, and desktop shortcuts if you want them.

The builds are not code-signed yet, so Windows SmartScreen may say *"Windows protected your PC"* the
first time. Choose **More info → Run anyway**. Each release lists SHA-256 checksums in
`SHA256SUMS.txt` if you want to verify the download.

To uninstall, use Windows **Settings → Apps → Installed apps**, or *Uninstall Anim2D* in the Start
menu. Your projects, FX templates (`Documents\Anim2D Templates`) and settings are kept.

There's also a portable `Anim2D.exe` on each release: one file, nothing installed.
`Anim2D.exe --fx-builder` opens the FX builder.

macOS and Linux builds aren't available yet. Running from source (below) may work there, but only
Windows is tested.

## Quick start

1. **File → Import Project…** opens a Spine JSON export or `.spine` project. Or start a new skeleton and
   use **File → Set Images Folder…**.
2. Build bones in **Setup** mode, attach images to slots, and switch to **Animate** (`Ctrl+Tab`) to key
   poses on the timeline.
3. **File → Save** writes an `.anim2d` project. **File → Export…** (`Ctrl+E`) writes data for your engine.
4. **FX → Insert FX into skeleton…** (`Ctrl+I`) drops a generated effect onto a bone at the current time.

The [editor guide](docs/editor.md) covers every panel, tool and shortcut.

## Run from source

You need Windows, [Python 3.11+](https://www.python.org/downloads/) (CI uses 3.11 and 3.13) and Git.
Node.js 22 is optional: it's only used by the runtime-parity tests.

```bash
git clone https://github.com/turtlegameworks/Anim2D.git
cd Anim2D
python -m venv .venv
.venv\Scripts\python -m pip install -e .[dev]
```

| Command | What it opens |
| --- | --- |
| `.venv\Scripts\anim2d` | the editor (`anim2d path\to\project.anim2d` opens a project) |
| `.venv\Scripts\anim2d-fx-builder` | the FX builder |
| `.venv\Scripts\anim2d-fx` | the FX command line (below) |

### FX command line

```bash
anim2d-fx list
anim2d-fx show impact_basic
anim2d-fx generate impact_basic --name fx_impact_01 --out output --set ray_count=12 --set primary_color=3aa0ff
anim2d-fx generate impact_basic --variant output\fx_impact_01\fx_impact_01.settings.json --name fx_impact_02 --out output
anim2d-fx register path\to\export.json --id my_hit --name "My Hit"
```

Each package holds the Spine JSON, its images, the settings that reproduce it, a generation report and
`IMPORT_IN_SPINE.md`. Existing folders are never overwritten. See [preset authoring](docs/preset_authoring.md)
to write your own presets.

### Tests

```bash
.venv\Scripts\python tools\fetch_spine_runtime.py
.venv\Scripts\python -m pytest
```

`fetch_spine_runtime.py` downloads Esoteric's spine-ts runtimes and example skeletons at pinned commits
(checked against SHA-256 hashes) for the parity tests; without Node.js or that step, those tests are
skipped. Tests that need `.spine` projects which can't be shipped here run only if you point
`ANIM2D_SPINE_EXAMPLES` at Spine's examples folder or `ANIM2D_SPINE_PROJECTS` at your own projects.

### Build the Windows program

```bash
.venv\Scripts\python -m pip install -e .[build]
.venv\Scripts\python tools\build_exe.py
```

This writes `dist\Anim2D.exe` and `dist\Anim2D-Setup-<version>.exe`. `tools\self_test.ps1` runs the
built program's own checks. CI does all of this on every push, and pushing a `v<version>` tag
publishes a release.

## Project layout

```text
src/anim2d/
  editor/          the editor: document model, viewport, panels, timeline, tools, exporters
  formats/         Spine 3.8 / 4.0 JSON, 4.0 binary, atlas packer, .spine project reader
  preview/         pose evaluator, constraint solver, mesh and Qt renderer
  generation/      FX generators and registry
  models/          FX effect model, parameters, presets, settings and variants
  presets/         preset library discovery
  spine_adapters/  Spine 3.8 serializer for FX packages
  templates/       registering Spine exports as FX templates
  packaging/       FX package and engine export
  validation/      output checks
  ui/              FX builder window
  preset_library/  the built-in presets (data and art only)
  assets/          icon
installer/         per-user Windows installer and uninstaller
tools/             build, self-test, runtime fetch, and art/icon generators
tests/             pytest suite (runs headless)
examples/          sample FX packages
docs/              guides, compatibility record, audit, screenshots
```

## Contributing

Bug reports, ideas and pull requests are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md). Every
pull request gets CI and an automated [CodeRabbit](https://coderabbit.ai) review. Known gaps are listed
in the [October 2026 audit](docs/audit-2026-10.md) and in the issues. To report a security problem, see
[SECURITY.md](SECURITY.md).

## License

Anim2D is released under the [MIT License](LICENSE). The presets' art is original and covered by the
same licence.

*Spine is a trademark of Esoteric Software LLC. Anim2D is an independent project, not affiliated with or
endorsed by Esoteric Software. It reads and writes the Spine data formats so your work can be used with
the Spine runtimes. Using those runtimes in your own product is subject to the
[Spine Runtimes License](https://esotericsoftware.com/spine-runtimes-license).*
