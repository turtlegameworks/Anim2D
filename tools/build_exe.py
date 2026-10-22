"""Builds the Windows program and its installer with PyInstaller.

    .venv\Scripts\python -m pip install -e .[build]
    .venv\Scripts\python tools\build_exe.py [--dist DIR]

Output:
  dist\Anim2D.exe                    the program (both tools; --fx-builder opens the FX builder)
  dist\Anim2D-Setup-<version>.exe    per-user installer with Start-menu/desktop shortcuts and uninstaller

Verify a build: Anim2D.exe --self-test <folder>   /   Anim2D.exe --fx-builder --self-test <folder>
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from anim2d import __version__ as VERSION  # noqa: E402

SEP = ";" if sys.platform == "win32" else ":"
PACKAGE = ROOT / "src" / "anim2d"
ICON = PACKAGE / "assets" / "anim2d.ico"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
# Qt modules the program never imports; excluding them keeps it smaller.
QT_EXCLUDES = ["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml", "PySide6.QtQuick",
               "PySide6.Qt3DCore", "PySide6.QtPdf", "tkinter", "pytest"]  # QtMultimedia: MP4 export


def pyinstaller(name: str, script: Path, *, data: list[tuple[Path, str]], excludes: list[str],
                distpath: Path) -> Path:
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
            "--name", name, "--icon", str(ICON), "--paths", str(ROOT / "src"),
            "--distpath", str(distpath), "--workpath", str(BUILD / "pyinstaller" / name), "--specpath", str(BUILD)]
    for source, dest in data:
        args += ["--add-data", f"{source}{SEP}{dest}"]
    for module in excludes:
        args += ["--exclude-module", module]
    subprocess.run([*args, str(script)], check=True, cwd=ROOT)
    return distpath / f"{name}.exe"


def main() -> None:
    # --dist DIR writes the program and installer elsewhere (e.g. while dist\Anim2D.exe is running).
    dist = Path(sys.argv[sys.argv.index("--dist") + 1]).resolve() if "--dist" in sys.argv else DIST
    launchers = BUILD / "launchers"
    launchers.mkdir(parents=True, exist_ok=True)
    program_script = launchers / "Anim2D.py"
    program_script.write_text("from anim2d.launcher import main\nraise SystemExit(main())\n", encoding="utf-8")
    # Resources go where anim2d.resources.bundle_root() looks for them: <bundle>/anim2d/...
    program = pyinstaller("Anim2D", program_script,
                          data=[(PACKAGE / "preset_library", "anim2d/preset_library"),
                                (PACKAGE / "assets", "anim2d/assets")],
                          excludes=QT_EXCLUDES, distpath=dist)
    print(f"built {program}")

    version_file = BUILD / "VERSION"
    version_file.write_text(VERSION + "\n", encoding="utf-8")
    setup_script = ROOT / "installer" / "anim2d_setup.py"
    small = ["PySide6", "PIL", "numpy", "pytest"]
    uninstaller = pyinstaller("uninstaller", setup_script, data=[(ICON, "payload"), (version_file, "payload")],
                              excludes=small, distpath=BUILD / "uninstaller")
    setup = pyinstaller(f"Anim2D-Setup-{VERSION}", setup_script,
                        data=[(program, "payload"), (uninstaller, "payload"), (ICON, "payload"),
                              (version_file, "payload")],
                        excludes=small, distpath=dist)
    print(f"built {setup}")


if __name__ == "__main__":
    raise SystemExit(main())
