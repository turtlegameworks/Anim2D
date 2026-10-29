"""Builds the macOS app and its disk image with PyInstaller, on a Mac.

    .venv/bin/python -m pip install -e ".[build]"
    .venv/bin/python tools/build_mac.py [--dist DIR]

Output (for the Mac it runs on: Apple silicon or Intel; build on each for both):
  dist/Anim2D.app                                   the program (FX → Open FX Builder opens the FX Builder)
  dist/Anim2D-<version>-macOS-AppleSilicon.dmg      disk image: Anim2D.app and a link to Applications
  dist/Anim2D-<version>-macOS-Intel.dmg

The app is signed ad hoc (Apple silicon won't run unsigned code), not with a Developer ID, so the first
time it is opened macOS asks to confirm (see README, Install). Verify a build with tools/self_test.py,
which can run it from inside the disk image.
"""

from __future__ import annotations

import platform
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_exe import BUILD, DIST, PROGRAM_DATA, QT_EXCLUDES, VERSION, launcher_script, pyinstaller  # noqa: E402

BUNDLE_ID = "com.turtlegameworks.anim2d"
MINIMUM_MACOS = "14.0"  # PySide6 6.12's wheels are built for macOS 14
PROJECT_TYPE = f"{BUNDLE_ID}.project"


def arch() -> str:
    """'AppleSilicon' or 'Intel': the processor this Python (and so the build) is for."""
    return "AppleSilicon" if platform.machine() == "arm64" else "Intel"


def disk_image_name(version: str = VERSION, for_arch: str | None = None) -> str:
    return f"Anim2D-{version}-macOS-{for_arch or arch()}.dmg"


def make_icns(target: Path) -> Path:
    """The app icon as .icns, drawn at 1024 px by tools/make_icon.py."""
    from make_icon import draw
    target.parent.mkdir(parents=True, exist_ok=True)
    draw().save(target, format="ICNS")
    return target


def info_plist(app: Path) -> dict:
    """Version, minimum macOS, Retina and the .anim2d project type, added to PyInstaller's Info.plist."""
    path = app / "Contents" / "Info.plist"
    with open(path, "rb") as f:
        info = plistlib.load(f)
    info.update({
        "CFBundleName": "Anim2D",
        "CFBundleDisplayName": "Anim2D",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": MINIMUM_MACOS,
        "LSApplicationCategoryType": "public.app-category.graphics-design",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        "NSHumanReadableCopyright": "Anim2D contributors, MIT License",
        "UTExportedTypeDeclarations": [{
            "UTTypeIdentifier": PROJECT_TYPE,
            "UTTypeDescription": "Anim2D project",
            "UTTypeConformsTo": ["public.data"],
            "UTTypeTagSpecification": {"public.filename-extension": ["anim2d", "rig2d"]},
        }],
        "CFBundleDocumentTypes": [{
            "CFBundleTypeName": "Anim2D project",
            "CFBundleTypeRole": "Editor",
            "LSHandlerRank": "Owner",
            "LSItemContentTypes": [PROJECT_TYPE],
        }],
    })
    with open(path, "wb") as f:
        plistlib.dump(info, f)
    return info


def sign(app: Path) -> None:
    """Ad hoc signature over the whole bundle (editing Info.plist broke PyInstaller's), then a check."""
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)], check=True)


def make_dmg(app: Path, dmg: Path) -> Path:
    """A compressed disk image holding the app and a link to /Applications to drag it onto."""
    staging = BUILD / "dmg"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    subprocess.run(["ditto", str(app), str(staging / app.name)], check=True)  # keeps links and signatures
    (staging / "Applications").symlink_to("/Applications")
    dmg.unlink(missing_ok=True)
    command = ["hdiutil", "create", "-volname", f"Anim2D {VERSION}", "-srcfolder", str(staging), "-ov",
               "-fs", "HFS+", "-format", "UDZO", str(dmg)]
    for attempt in range(4):  # hdiutil on CI machines sometimes reports the device busy
        if subprocess.run(command).returncode == 0:
            break
        time.sleep(5 * (attempt + 1))
    else:
        raise SystemExit(f"hdiutil couldn't create {dmg}")
    subprocess.run(["hdiutil", "verify", str(dmg)], check=True)
    shutil.rmtree(staging, ignore_errors=True)
    return dmg


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("tools/build_mac.py builds the macOS app and has to run on a Mac.")
    dist = Path(sys.argv[sys.argv.index("--dist") + 1]).resolve() if "--dist" in sys.argv else DIST
    icns = make_icns(BUILD / "Anim2D.icns")
    app = pyinstaller("Anim2D", launcher_script(), data=PROGRAM_DATA, excludes=QT_EXCLUDES, distpath=dist,
                      icon=icns, extra=["--osx-bundle-identifier", BUNDLE_ID])
    info_plist(app)
    sign(app)
    print(f"built {app}")
    dmg = make_dmg(app, dist / disk_image_name())
    print(f"built {dmg}")


if __name__ == "__main__":
    raise SystemExit(main())
