"""Local test builds of Anim2D, for trying a change in the real program before it goes into a pull request.

    .venv\\Scripts\\python tools\\dev_build.py            build, then open it
    .venv\\Scripts\\python tools\\dev_build.py --fx       build, then open the FX Builder
    .venv\\Scripts\\python tools\\dev_build.py --no-open  build only
    .venv\\Scripts\\python tools\\dev_build.py --list     the test builds there are
    .venv\\Scripts\\python tools\\dev_build.py --clean    delete them all

It only runs when you run it. Each build goes into its own folder,

    dev-builds\\<date>-<time>-<commit>[-dirty]\\Anim2D-Dev.exe   (+ build_info.json, build.log)

and dev-builds\\ is ignored by git. The newest few are kept; a local post-commit hook (installed the
first time, never committed) deletes them all when you commit, since they belong to the code before.

A test build is its own program: Anim2D-Dev.exe, nothing installed, no shortcuts, its own settings
(HKCU\\Software\\Anim2D-Dev) and FX templates (Documents\\Anim2D Dev Templates), and it never checks
for or installs updates. So it runs next to an installed Anim2D, even at the same time, without
changing it. It says DEV BUILD at the bottom right, [DEV] in the title and on its icon.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_exe import BUILD, ICON, PACKAGE, QT_EXCLUDES, ROOT, pyinstaller  # noqa: E402

BUILDS = ROOT / "dev-builds"
WORK = BUILD / "dev"
PROGRAM = "Anim2D-Dev"
KEEP = 3
HOOK_MARKER = "# anim2d dev builds"
FOLDER = re.compile(r"^\d{8}-\d{6}-")  # the start of every build folder's name
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Anim2D"


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def source_state() -> dict:
    """What is being built: commit, branch and whether there are uncommitted changes."""
    return {"commit": git("rev-parse", "HEAD"), "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(git("status", "--porcelain", "--untracked-files=no"))}


def folders() -> list[Path]:
    """Every folder this script made (finished, failed or half-deleted), newest first: found by its
    <date>-<time>-<commit> name, so nothing it left behind is ever lost track of."""
    if not BUILDS.is_dir():
        return []
    return sorted((p for p in BUILDS.iterdir() if p.is_dir() and FOLDER.match(p.name)),
                  key=lambda p: p.name, reverse=True)


def builds() -> list[Path]:
    """Finished test builds (with their program), newest first. Failed builds don't count."""
    return [p for p in folders() if (p / f"{PROGRAM}.exe").is_file()]


def remove(targets: list[Path], quiet: bool = False) -> int:
    """Delete build folders. The program goes first: if it's still running (Windows keeps it locked),
    the folder is left whole, to be removed next time."""
    removed = 0
    for folder in targets:
        try:
            (folder / f"{PROGRAM}.exe").unlink(missing_ok=True)
        except OSError:
            if not quiet:
                print(f"  kept {folder.name}: it's still running")
            continue
        shutil.rmtree(folder, ignore_errors=True)
        removed += not folder.exists()
    return removed


def dev_icon(target: Path) -> Path:
    """The app icon with an amber DEV tag across the bottom."""
    from PIL import Image, ImageDraw, ImageFont
    with Image.open(ICON) as ico:
        ico.size = max(ico.ico.sizes())
        base = ico.convert("RGBA").resize((256, 256))
    draw = ImageDraw.Draw(base)
    draw.rounded_rectangle((10, 168, 246, 246), radius=18, fill=(240, 160, 32, 255), outline=(27, 27, 27, 255),
                           width=6)
    try:
        font = ImageFont.truetype("arialbd.ttf", 62)
    except OSError:
        font = ImageFont.load_default()
    draw.text((128, 207), "DEV", fill=(27, 27, 27, 255), font=font, anchor="mm")
    target.parent.mkdir(parents=True, exist_ok=True)
    base.save(target, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return target


def official_install() -> tuple[Path | None, str | None, bool]:
    """The installed (official) Anim2D: its folder, version, and whether it is running now."""
    folder = version = None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            folder = Path(winreg.QueryValueEx(key, "InstallLocation")[0])
            version = str(winreg.QueryValueEx(key, "DisplayVersion")[0])
    except (OSError, ImportError):
        pass
    tasks = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Anim2D.exe", "/FO", "CSV", "/NH"],
                           capture_output=True, text=True)
    return folder, version, '"Anim2D.exe"' in tasks.stdout


def install_hook(quiet: bool = False) -> None:
    """A post-commit hook in this clone (not part of the repository) that deletes the test builds."""
    hooks = Path(git("rev-parse", "--git-path", "hooks") or ".git/hooks")
    hooks = hooks if hooks.is_absolute() else ROOT / hooks
    hook = hooks / "post-commit"
    python = Path(sys.executable).as_posix()
    body = f'{HOOK_MARKER}: a commit makes the local test builds out of date\n"{python}" tools/dev_build.py --clean --quiet || true\n'
    if hook.is_file():
        text = hook.read_text(encoding="utf-8", errors="replace")
        if HOOK_MARKER in text:
            return
        # Ours goes first, right after the #! line, so an `exit` in the existing hook can't skip it.
        first, _, rest = text.partition("\n") if text.startswith("#!") else ("#!/bin/sh", "", text)
        hook.write_text(f"{first}\n{body}\n{rest}", encoding="utf-8")
    else:
        hooks.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    if not quiet:
        print(f"Installed {hook.relative_to(ROOT) if hook.is_relative_to(ROOT) else hook}: test builds are "
              "deleted when you commit")


def launch(exe: Path, fx: bool) -> None:
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: a GUI program on its own
    subprocess.Popen([str(exe), *(["--fx-builder"] if fx else [])], cwd=str(exe.parent), close_fds=True,
                     creationflags=flags)


def build(fx: bool, open_after: bool, keep: int) -> int:
    state = source_state()
    short = state["commit"][:7] or "nogit"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = BUILDS / f"{stamp}-{short}{'-dirty' if state['dirty'] else ''}"
    work = WORK / folder.name
    folder.mkdir(parents=True)
    info = {"channel": "dev", **state, "built": datetime.now().isoformat(timespec="seconds")}
    (work / "anim2d").mkdir(parents=True)
    (work / "anim2d" / "build_info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    (folder / "build_info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    icon = dev_icon(work / "anim2d-dev.ico")
    launcher = work / f"{PROGRAM}.py"
    launcher.write_text("from anim2d.launcher import main\nraise SystemExit(main())\n", encoding="utf-8")

    print(f"Building {PROGRAM} from {state['branch'] or 'detached HEAD'} @ {short}"
          f"{' with uncommitted changes' if state['dirty'] else ''}...")
    started = time.monotonic()
    failed = False
    with open(folder / "build.log", "w", encoding="utf-8") as log:
        try:
            exe = pyinstaller(PROGRAM, launcher, data=[(PACKAGE / "preset_library", "anim2d/preset_library"),
                                                       (PACKAGE / "assets", "anim2d/assets"),
                                                       (icon, "anim2d/assets"),
                                                       (work / "anim2d" / "build_info.json", "anim2d")],
                              excludes=QT_EXCLUDES, distpath=folder, icon=icon, work=work, log=log)
        except subprocess.CalledProcessError:
            failed = True
        finally:
            shutil.rmtree(work, ignore_errors=True)
    if failed:
        (folder / "build_info.json").unlink(missing_ok=True)
        marked = folder.with_name(folder.name + "-failed")
        folder.rename(marked)
        print(f"Build failed; see {(marked / 'build.log').relative_to(ROOT)}")
        return 1
    print(f"Built {exe.relative_to(ROOT)} ({exe.stat().st_size / 1048576:.0f} MB) in {time.monotonic() - started:.0f} s")

    old = builds()[keep:]
    if old:
        print(f"Removed {remove(old)} older test build(s); keeping the newest {keep}")
    install_hook()

    installed, version, running = official_install()
    if installed:
        print(f"Official Anim2D {version or ''} is installed in {installed}"
              f"{' and running' if running else ''}; the test build doesn't touch it.")
    elif running:
        print("An official Anim2D is running; the test build doesn't touch it.")
    else:
        print("No official Anim2D is installed.")
    if open_after:
        launch(exe, fx)
        print(f"Opened the {'FX Builder' if fx else 'editor'} test build.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local Anim2D test builds")
    parser.add_argument("--fx", action="store_true", help="open the FX Builder instead of the editor")
    parser.add_argument("--no-open", action="store_true", help="don't open the build")
    parser.add_argument("--keep", type=int, default=KEEP, help=f"test builds to keep (default {KEEP})")
    parser.add_argument("--list", action="store_true", help="list the test builds")
    parser.add_argument("--clean", action="store_true", help="delete every test build")
    parser.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.list:
        finished = builds()
        for folder in folders():
            if folder not in finished:
                print(f"{folder.name}  (no program: failed or partly removed)")
                continue
            try:
                info = json.loads((folder / "build_info.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                info = {}
            print(f"{folder.name}  {info.get('branch', '')}  built {info.get('built', '')}")
        if not folders():
            print("No test builds.")
        return 0
    if args.clean:
        found = folders()
        removed = remove(found, args.quiet)
        shutil.rmtree(WORK, ignore_errors=True)
        if not args.quiet:
            print(f"Removed {removed} test build(s)." if found else "No test builds to remove.")
        return 0
    return build(args.fx, not args.no_open, max(1, args.keep))


if __name__ == "__main__":
    raise SystemExit(main())
