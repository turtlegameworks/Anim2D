# Changelog

All notable changes to Anim2D. Versions follow [semantic versioning](https://semver.org/). Before 1.0,
minor versions may change file formats, but older files keep opening.

## 0.16.0 (2026-10-23)

First public release, under the MIT License.

### Renamed

* The editor (previously *RigWorks 2D*) and the FX builder (previously *Spine FX Preset Builder*) are
  now **Anim2D** and **Anim2D FX Builder**: `Anim2D.exe`, `Anim2D-Setup-<version>.exe`, the `anim2d`
  Python package and the `anim2d`, `anim2d-fx-builder` and `anim2d-fx` commands.
* Projects are saved as `.anim2d`. Older `.rig2d` projects still open.
* Settings are copied once from the old names. FX templates in `Documents\Spine FX Templates` keep
  working until `Documents\Anim2D Templates` exists. `SPINE_FX_LIBRARY` and `SPINE_FX_USER_LIBRARY`
  still work as fallbacks for `ANIM2D_LIBRARY` and `ANIM2D_USER_LIBRARY`.
* Installing the Unity bridge script replaces the old `RigWorksBridge.cs`.

### Fixed

* Unpacking an atlas could write or read files outside the chosen folders through region or page names.
* Exporting with a packed atlas could overwrite existing page images even with overwrite off.
* Weighted meshes merged with **Import Data** or **Insert FX** could end up bound to the wrong bones.
* Animation clean-up could shorten an animation by removing its last key.
* **Save Project As** into another folder lost a relative audio folder.
* Portal Swirl failed validation at its shortest collapse time.
* A project file without skeleton data, or a preset with a non-numeric bound, crashed instead of giving
  an error.
* The uninstaller now removes only the files, shortcuts and registry entry the installer created.
* Installed copies (wheel) were missing the preset library and icon, and multimedia support wasn't
  declared as a dependency.

### Added

* Windows CI (lint, tests on Python 3.11 and 3.13, build and self-test) and tagged releases with
  checksums.
* Contributor guide, security policy, issue and pull request templates, CodeRabbit review config.
