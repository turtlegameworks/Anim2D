"""Runs a built program's own build checks (editor and FX builder) and fails if either reports a problem.

    python tools/self_test.py dist/Anim2D.app [--output selftest]
    python tools/self_test.py dist/Anim2D-<version>-macOS-AppleSilicon.dmg --arch AppleSilicon
    python tools/self_test.py dist/Anim2D.exe

Given a disk image, it mounts it read-only and checks what a user gets: Anim2D.app next to a link to
Applications, the app's version, minimum macOS and signature, and that it is built for --arch; then it
runs the checks on the app inside the image. tools/self_test.ps1 does the same for the Windows program.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from anim2d import __version__ as VERSION  # noqa: E402

TIMEOUT = 300
ARCHS = {"AppleSilicon": "arm64", "Intel": "x86_64"}


def executable(program: Path) -> Path:
    """The file to run: the program itself, or an .app bundle's main executable."""
    if program.suffix == ".app":
        with open(program / "Contents" / "Info.plist", "rb") as f:
            return program / "Contents" / "MacOS" / plistlib.load(f)["CFBundleExecutable"]
    return program


def run_checks(program: Path, output: Path) -> list[str]:
    """Both self-tests; the problems found (empty when both passed)."""
    problems = []
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for mode in ("editor", "fx"):
        folder = (output / mode).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        args = [str(executable(program)), *(["--fx-builder"] if mode == "fx" else []), "--self-test", str(folder)]
        try:
            done = subprocess.run(args, env=env, timeout=TIMEOUT, capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            problems.append(f"{mode} self-test timed out")
            continue
        report = folder / "report.json"
        if not report.is_file():
            problems.append(f"{mode} self-test wrote no report (exit code {done.returncode})\n{done.stderr[-4000:]}")
            continue
        result = json.loads(report.read_text(encoding="utf-8"))
        if done.returncode != 0 or not result.get("ok"):
            problems.append(f"{mode} self-test failed:\n{report.read_text(encoding='utf-8')}")
        else:
            print(f"{mode} self-test passed")
    return problems


def check_app(app: Path, arch: str | None) -> list[str]:
    """What the macOS app bundle says about itself, and that it is intact and built for arch."""
    problems = []
    with open(app / "Contents" / "Info.plist", "rb") as f:
        info = plistlib.load(f)
    if info.get("CFBundleShortVersionString") != VERSION:
        problems.append(f"the app says version {info.get('CFBundleShortVersionString')}, not {VERSION}")
    if not info.get("LSMinimumSystemVersion"):
        problems.append("the app doesn't name a minimum macOS version")
    if not info.get("CFBundleDocumentTypes"):
        problems.append("the app doesn't declare the .anim2d project type")
    signed = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], capture_output=True, text=True)
    if signed.returncode != 0:
        problems.append(f"the app's signature doesn't verify: {signed.stderr.strip()}")
    if arch:
        found = subprocess.run(["lipo", "-archs", str(executable(app))], capture_output=True, text=True).stdout.split()
        if ARCHS[arch] not in found:
            problems.append(f"the app is built for {' '.join(found) or 'nothing'}, not {ARCHS[arch]} ({arch})")
    print(f"app: version {info.get('CFBundleShortVersionString')}, macOS {info.get('LSMinimumSystemVersion')}+, "
          f"signature {'ok' if signed.returncode == 0 else 'BROKEN'}")
    return problems


@contextmanager
def mounted(dmg: Path):
    """The disk image attached read-only at a temporary mount point, detached afterwards."""
    point = Path(tempfile.mkdtemp(prefix="anim2d-dmg-"))
    subprocess.run(["hdiutil", "attach", str(dmg), "-readonly", "-nobrowse", "-noautoopen", "-mountpoint",
                    str(point)], check=True, capture_output=True)
    try:
        yield point
    finally:
        subprocess.run(["hdiutil", "detach", str(point), "-force"], capture_output=True)
        try:
            point.rmdir()
        except OSError:
            pass


def check_dmg(volume: Path) -> list[str]:
    problems = []
    if not (volume / "Anim2D.app").is_dir():
        problems.append("the disk image has no Anim2D.app")
    link = volume / "Applications"
    if not (link.is_symlink() and os.readlink(link) == "/Applications"):
        problems.append("the disk image has no link to Applications to drag the app onto")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("program", type=Path, help="Anim2D.exe, Anim2D.app or a macOS disk image")
    parser.add_argument("--output", type=Path, default=Path("selftest"), help="where the checks write")
    parser.add_argument("--arch", choices=sorted(ARCHS), help="macOS: the processor the app must be built for")
    args = parser.parse_args()
    program = args.program.resolve()
    if program.suffix == ".dmg":
        with mounted(program) as volume:
            app = volume / "Anim2D.app"
            problems = check_dmg(volume)
            if not problems:
                problems = check_app(app, args.arch) + run_checks(app, args.output)
    else:
        problems = check_app(program, args.arch) if program.suffix == ".app" else []
        problems += run_checks(program, args.output)
    for problem in problems:
        print(f"::error::{problem}" if os.environ.get("GITHUB_ACTIONS") else f"FAILED: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
