"""Command-line interface.

    anim2d-fx list [--search TEXT]
    anim2d-fx show PRESET
    anim2d-fx generate PRESET --name fx_impact_01 --out DIR [--set key=value ...] [--variant FILE]
    anim2d-fx register EXPORT.json --id my_hit --name "My Hit" [--images DIR] [--animation NAME] [--replace]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .models.preset import PresetError
from .models.settings import load_variant, validate_settings
from .packaging.package import write_package
from .presets.library import KNOWN_GENERATORS, PresetLibrary, default_user_library_dir
from .templates.register import analyze_export, register_template


def _parse_sets(pairs: list[str]) -> dict[str, object]:
    values: dict[str, object] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--set expects key=value, got '{pair}'")
        key, raw = pair.split("=", 1)
        try:
            values[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            values[key.strip()] = raw
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="anim2d-fx", description="Anim2D FX Builder (Spine 3.8.75)")
    parser.add_argument("--library", type=Path, help="built-in preset library folder")
    parser.add_argument("--user-library", type=Path, help="user template library folder "
                        f"(default: {default_user_library_dir()})")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list presets")
    p_list.add_argument("--search", default="")

    p_show = sub.add_parser("show", help="show a preset's settings")
    p_show.add_argument("preset")

    p_gen = sub.add_parser("generate", help="generate an output package")
    p_gen.add_argument("preset")
    p_gen.add_argument("--name", help="instance name, e.g. fx_impact_01")
    p_gen.add_argument("--out", type=Path, required=True, help="destination folder")
    p_gen.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    p_gen.add_argument("--variant", type=Path, help="load settings from a saved variant first")

    p_reg = sub.add_parser("register", help="register a Spine 3.8 JSON export as a user template")
    p_reg.add_argument("export", type=Path, help="JSON exported from Spine 3.8 (Nonessential data on)")
    p_reg.add_argument("--id", required=True, help="template id, e.g. my_hit")
    p_reg.add_argument("--name", required=True, help="display name")
    p_reg.add_argument("--images", type=Path, help="images folder (default: from the export)")
    p_reg.add_argument("--animation", help="animation to use when the export has several")
    p_reg.add_argument("--category", default="custom")
    p_reg.add_argument("--description", default="")
    p_reg.add_argument("--replace", action="store_true", help="replace an existing user template (old one is backed up)")

    args = parser.parse_args(argv)
    user_root = args.user_library or default_user_library_dir()

    if args.command == "register":
        analysis = analyze_export(args.export, args.images, args.animation)
        for warning in analysis.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        if not analysis.ok:
            for error in analysis.errors:
                print(f"error: {error}", file=sys.stderr)
            if analysis.animations and len(analysis.animations) > 1 and not args.animation:
                print(f"animations: {', '.join(analysis.animations)}", file=sys.stderr)
            return 1
        try:
            preset = register_template(analysis, user_root, preset_id=args.id, name=args.name,
                                       category=args.category, description=args.description,
                                       replace=args.replace, known_generators=KNOWN_GENERATORS)
        except PresetError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        roles = ", ".join(f"{s.name}={s.role}" for s in analysis.slots if s.role != "keep") or "none"
        print(f"Registered template '{preset.id}' v{preset.version} in {preset.directory}")
        print(f"  {analysis.counts}; source duration {analysis.source_duration:.3f}s; tinted slots: {roles}")
        return 0

    library = PresetLibrary.load(args.library, user_root)
    for folder, problem in library.errors.items():
        print(f"warning: preset '{folder}' skipped: {problem}", file=sys.stderr)

    if args.command == "list":
        for s in library.list_presets(args.search):
            source = library.get(s.id).source
            print(f"{s.id:<20} {s.category:<10} {source:<8} {s.name} - {s.description}")
        return 0

    try:
        preset = library.get(args.preset)
    except KeyError as exc:
        print(f"error: {exc.args[0]}", file=sys.stderr)
        return 2

    if args.command == "show":
        print(f"{preset.name} ({preset.id} v{preset.version}) - {preset.description}")
        for spec in preset.parameters:
            bounds = f" [{spec.min}..{spec.max}]" if spec.min is not None else ""
            print(f"  {spec.name:<18} {spec.type:<8} default={spec.default}{bounds} {spec.unit}  {spec.description}")
        return 0

    raw: dict[str, object] = {}
    name = args.name or ""
    if args.variant:
        try:
            variant_name, raw = load_variant(args.variant, preset)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        name = name or variant_name
    raw.update(_parse_sets(args.set))
    settings, report = validate_settings(preset, name, raw)
    for warning in report.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if settings is None:
        for error in report.errors:
            print(f"error: {error}", file=sys.stderr)
        return 1

    result = write_package(preset, settings, args.out)
    for warning in result.report.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if not result.ok:
        for error in result.report.errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    stats = ", ".join(f"{v} {k}" for k, v in result.stats.items())
    print(f"Generated {result.output_dir} ({stats})")
    print("Spine editor import is not verified automatically; follow IMPORT_IN_SPINE.md in the package.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
