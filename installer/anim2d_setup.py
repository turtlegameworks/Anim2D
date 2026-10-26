"""Anim2D per-user installer and uninstaller (Windows, no admin rights needed).

Built into Anim2D-Setup-<version>.exe by tools/build_exe.py, with the program
(Anim2D.exe) embedded. Installing copies the program, creates Start-menu (and optional
desktop) shortcuts for the apps chosen (Anim2D, the Anim2D FX Builder, or both: they are one
program, so the choice decides which apps get shortcuts), and registers an uninstaller under
"Installed apps". Everything created is recorded in install.json so the uninstaller removes
exactly that and nothing else (user projects, templates and settings are kept).

    Anim2D-Setup.exe                      install wizard
    Anim2D-Setup.exe --silent [--target DIR] [--apps editor,fx] [--no-desktop] [--launch]
    Anim2D-Setup.exe --update --target DIR [--wait-pid PID] [--launch]
    "Uninstall Anim2D.exe" --uninstall [--silent] [--remove-settings] [--remove-templates]

--update is what the app's "Install update" runs: it waits for the app to exit, keeps the apps and
shortcuts chosen last time, shows only a small progress window, and restarts Anim2D.

Test-only options: --start-menu-dir DIR --desktop-dir DIR --registry-key NAME --no-register
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

APP_NAME = "Anim2D"
PROGRAM = "Anim2D.exe"
UNINSTALLER = "Uninstall Anim2D.exe"
REGISTRY_KEY = "Anim2D"
UNINSTALL_ROOT = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
MANIFEST = "install.json"
NO_WINDOW = 0x08000000
# id: (shortcut name, program arguments, description)
APPS = {
    "editor": (APP_NAME, "", "2D skeletal animation editor: rig, animate and export for the Spine runtimes"),
    "fx": ("Anim2D FX Builder", "--fx-builder", "Generate editable FX (impacts, coins, sparkles…) from presets"),
}
SHORTCUTS = [(name, args) for name, args, _ in APPS.values()]
IN_USE_WAIT = 15.0  # seconds to keep retrying a file that's still in use (the app closing)
# What the app keeps outside the install folder, removed only when asked. Projects and generated FX
# packages are never touched.
SETTINGS_KEYS = [  # under HKEY_CURRENT_USER\Software (Qt keeps QSettings here); (key, parent to tidy)
    ("Anim2D", None),
    ("RigWorks\\RigWorks2D", "RigWorks"),  # names before the rename
    ("SpineFXBuilder\\SpineFXBuilder", "SpineFXBuilder"),
]


class SetupError(Exception):
    pass


def _read_version() -> str:
    """The version the build embedded (payload/VERSION); from source, anim2d.__version__."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    embedded = base / "payload" / "VERSION"
    if embedded.is_file():
        return embedded.read_text(encoding="utf-8").strip()
    init = Path(__file__).resolve().parents[1] / "src" / "anim2d" / "__init__.py"
    for line in init.read_text(encoding="utf-8").splitlines() if init.is_file() else []:
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip("\"'")
    return "0.0.0"


VERSION = _read_version()
ICON = "anim2d.ico"
OWNED_FILES = {PROGRAM, UNINSTALLER, ICON}  # the only files install() ever creates


# --- locations ---------------------------------------------------------------------------

def _known_folder(csidl: int) -> Path:
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
    return Path(buf.value)


def default_target() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Programs" / APP_NAME


def default_start_menu() -> Path:
    return _known_folder(0x02) / APP_NAME  # CSIDL_PROGRAMS


def default_desktop() -> Path:
    return _known_folder(0x10)  # CSIDL_DESKTOPDIRECTORY (follows OneDrive redirection)


def bundled(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "payload" / name


def read_manifest(target: Path) -> dict | None:
    """The install record in target, or None if there is no readable one."""
    try:
        manifest = json.loads((Path(target) / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None


def foreign_files(folder: Path) -> list[Path]:
    """What's in folder that Setup didn't put there. Leftovers of an earlier Anim2D (an uninstaller that
    couldn't delete itself, a half-copied .new file) don't count, so the folder can be reused."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    ours = OWNED_FILES | {f"{name}.new" for name in OWNED_FILES} | {MANIFEST, MANIFEST + ".new"}
    return [item for item in folder.iterdir() if item.name not in ours]


def manifest_desktop(manifest: dict | None) -> bool:
    """Whether an installation has desktop shortcuts. Records from before this was stored (0.16.0) say
    so through their shortcuts: any outside the Start-menu folder were on the desktop."""
    manifest = manifest or {}
    if isinstance(manifest.get("desktop"), bool):
        return manifest["desktop"]
    if "shortcuts" not in manifest:
        return True  # nothing recorded: the default
    menu = os.path.normcase(str(Path(manifest.get("start_menu_dir") or "").resolve()))
    return any(isinstance(s, str) and os.path.normcase(str(Path(s).parent.resolve())) != menu
               for s in manifest.get("shortcuts", []))


def manifest_apps(manifest: dict | None) -> list[str]:
    """The apps an installation was set up with (records from before the choice existed: both)."""
    apps = (manifest or {}).get("apps")
    chosen = [a for a in apps if a in APPS] if isinstance(apps, list) else []
    return chosen or list(APPS)


# The app's own folders and the user's templates, as anim2d.paths lays them out (it can't be imported here).
def _local_app_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME


def update_cache() -> Path:
    """%LOCALAPPDATA%/Anim2D/updates: downloaded updates (anim2d.paths.updates_dir)."""
    return _local_app_data() / "updates"


def logs_folder() -> Path:
    """%LOCALAPPDATA%/Anim2D/logs: anim2d.log (anim2d.paths.logs_dir)."""
    return _local_app_data() / "logs"


def documents() -> Path:
    """The user's Documents folder as Windows has it (anim2d.paths.documents), OneDrive redirection included."""
    if hasattr(ctypes, "windll"):
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]

        guid = GUID()
        ctypes.windll.ole32.CLSIDFromString("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}", ctypes.byref(guid))
        found = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(found)) == 0:
            try:
                return Path(found.value)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(found)
    return Path.home() / "Documents"


def template_folders() -> list[Path]:
    """The user's FX templates: Documents/Anim2D/FX Templates, and the folders used before 0.17.1."""
    docs = documents()
    folders = [docs / APP_NAME / "FX Templates", docs / "Anim2D Templates", docs / "Spine FX Templates"]
    legacy_docs = Path.home() / "Documents"
    if legacy_docs != docs:
        folders += [legacy_docs / "Anim2D Templates", legacy_docs / "Spine FX Templates"]
    return folders


def find_existing() -> Path | None:
    """Where Anim2D is installed already: the registered location, else the default folder."""
    candidates = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{UNINSTALL_ROOT}\\{REGISTRY_KEY}") as key:
            candidates.append(Path(winreg.QueryValueEx(key, "InstallLocation")[0]))
    except (OSError, ImportError):
        pass
    candidates.append(default_target())
    return next((c for c in candidates if read_manifest(c) is not None), None)


# --- pieces ------------------------------------------------------------------------------

def make_shortcut(link: Path, target: Path, arguments: str, icon: Path, workdir: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    script = ("$s = New-Object -ComObject WScript.Shell; $l = $s.CreateShortcut($env:RW_LNK); "
              "$l.TargetPath = $env:RW_TARGET; $l.Arguments = $env:RW_ARGS; $l.IconLocation = $env:RW_ICON; "
              "$l.WorkingDirectory = $env:RW_DIR; $l.Save()")
    env = {**os.environ, "RW_LNK": str(link), "RW_TARGET": str(target), "RW_ARGS": arguments,
           "RW_ICON": f"{icon},0", "RW_DIR": str(workdir)}
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env,
                            capture_output=True, text=True, creationflags=NO_WINDOW)
    if result.returncode != 0 or not link.is_file():
        raise SetupError(f"Could not create shortcut {link.name}: {result.stderr.strip()}")


def register(key_name: str, target: Path, size_kb: int) -> None:
    import winreg
    uninstaller = target / UNINSTALLER
    values = {
        "DisplayName": APP_NAME,
        "DisplayVersion": VERSION,
        "Publisher": "Anim2D",
        "DisplayIcon": f"{target / PROGRAM},0",
        "InstallLocation": str(target),
        "UninstallString": f'"{uninstaller}" --uninstall',
        "QuietUninstallString": f'"{uninstaller}" --uninstall --silent',
        "URLInfoAbout": "https://github.com/turtlegameworks/Anim2D",
    }
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{UNINSTALL_ROOT}\\{key_name}") as key:
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)


def _delete_registry_tree(subkey: str, root=None) -> None:
    """Delete HKEY_CURRENT_USER/Software/<subkey> and everything under it (missing is fine)."""
    import winreg
    root = root if root is not None else winreg.HKEY_CURRENT_USER
    path = "Software\\" + subkey
    try:
        with winreg.OpenKey(root, path) as key:
            children = []
            while True:
                try:
                    children.append(winreg.EnumKey(key, len(children)))
                except OSError:
                    break
        for child in children:
            _delete_registry_tree(subkey + "\\" + child, root)
        winreg.DeleteKey(root, path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise SetupError(f"Could not remove settings (HKEY_CURRENT_USER\\{path}): {exc}") from None


def _delete_empty_registry_key(subkey: str) -> None:
    """Remove a key only if nothing is left in it (another program may share the name)."""
    import winreg
    path = "Software\\" + subkey
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            subkeys, values, _ = winreg.QueryInfoKey(key)
        if subkeys == 0 and values == 0:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        pass


def _remove_tree(folder: Path) -> None:
    if folder.is_symlink() or (hasattr(folder, "is_junction") and folder.is_junction()):
        folder.unlink()  # the link only, never what it points at
    elif folder.is_dir():
        shutil.rmtree(folder)


def _remove_owned_logs(folder: Path) -> None:
    """Delete only the log files Anim2D wrote; remove the folder only when empty afterwards."""
    if folder.is_symlink() or (hasattr(folder, "is_junction") and folder.is_junction()):
        return
    if not folder.is_dir():
        return
    for name in ("anim2d.log", "anim2d.log.1", "anim2d.log.2", "anim2d.log.3"):
        p = folder / name
        if p.is_file() or p.is_symlink():
            p.unlink()
    try:
        folder.rmdir()
    except OSError:
        pass


def remove_user_data(settings: bool, templates: bool) -> list[str]:
    """Delete the app's settings and update downloads and/or the user's FX templates. Returns what was
    removed, for the summary. Projects and generated FX packages are never touched."""
    removed = []
    if settings:
        for subkey, parent in SETTINGS_KEYS:
            _delete_registry_tree(subkey)
            if parent:
                _delete_empty_registry_key(parent)
        removed.append("settings")
        folder = update_cache()
        if folder.exists():
            try:
                _remove_tree(folder)
            except OSError as exc:
                raise SetupError(f"Could not remove {folder}: {exc}") from None
            removed.append(str(folder))
        folder = logs_folder()
        if folder.exists():
            try:
                _remove_owned_logs(folder)
            except OSError as exc:
                raise SetupError(f"Could not remove {folder}: {exc}") from None
            removed.append(str(folder))
        try:
            update_cache().parent.rmdir()  # %LOCALAPPDATA%/Anim2D, only if nothing else is in it
        except OSError:
            pass
    if templates:
        for folder in template_folders():
            if folder.exists():
                try:
                    _remove_tree(folder)
                except OSError as exc:
                    raise SetupError(f"Could not remove {folder}: {exc}") from None
                removed.append(str(folder))
    return removed


def unregister(key_name: str) -> None:
    import winreg
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, f"{UNINSTALL_ROOT}\\{key_name}")
    except FileNotFoundError:
        pass


def start_program(program: Path, arguments: list[str] | None = None) -> None:
    """Start the installed program as an independent process. This installer is itself a
    PyInstaller build, so its bootloader variables must not leak into the program. (The installer
    can't import anim2d, so anim2d.updates.start_installer does the same the other way round: keep
    the two in step.)"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI") and k != "_MEIPASS2"}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen([str(program), *(arguments or [])], cwd=str(program.parent), env=env, close_fds=True,
                     creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def launch_arguments(apps: list[str]) -> list[str]:
    """What to start after installing: the editor if it was chosen, else the FX builder."""
    return [] if "editor" in apps else ["--fx-builder"]


def wait_for_exit(pid: int, timeout: float = 60.0) -> bool:
    """Wait for the process that started an update to close. True once it has (or never existed)."""
    if pid <= 0 or not hasattr(ctypes, "windll"):
        return True
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE  # a pointer-sized handle, not a C int
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    synchronize = 0x00100000
    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        return True  # already gone
    try:
        return kernel32.WaitForSingleObject(handle, int(timeout * 1000)) == 0
    finally:
        kernel32.CloseHandle(handle)


def _copy(source: Path, destination: Path, wait: float | None = None) -> None:
    """Copy, retrying for a while if the destination is still in use (Anim2D closing). The copy goes
    to a temporary file first, so a half-written program never replaces a working one."""
    temp = destination.with_name(destination.name + ".new")
    deadline = time.monotonic() + (IN_USE_WAIT if wait is None else wait)
    try:
        shutil.copyfile(source, temp)
        while True:
            try:
                os.replace(temp, destination)
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise SetupError(f"{destination.name} is in use. Close every {APP_NAME} window "
                                     "(including the FX Builder) and try again.") from None
                time.sleep(0.5)
    finally:
        temp.unlink(missing_ok=True)


def _write_manifest(target: Path, manifest: dict) -> None:
    temp = target / (MANIFEST + ".new")
    temp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(temp, target / MANIFEST)


# --- install / uninstall -----------------------------------------------------------------

def install(target: Path, *, apps: list[str] | None = None, desktop: bool = True,
            start_menu_dir: Path | None = None, desktop_dir: Path | None = None,
            registry_key: str | None = REGISTRY_KEY, progress=lambda text, fraction=None: None) -> Path:
    """Install or update into target. apps: which of APPS get shortcuts (default both)."""
    apps = [a for a in APPS if a in (apps if apps is not None else list(APPS))]
    if not apps:
        raise SetupError("Choose at least one app to install.")
    payload = bundled(PROGRAM)
    if not payload.is_file():
        raise SetupError("The program is missing from this installer.")
    target = Path(target)
    previous = read_manifest(target)
    if previous is None and foreign_files(target):
        raise SetupError(f"{target} already contains other files. Choose an empty folder or a new one.")
    target.mkdir(parents=True, exist_ok=True)
    menu = Path(start_menu_dir) if start_menu_dir else default_start_menu()
    desk = Path(desktop_dir) if desktop_dir else None  # None: the user's desktop, looked up when needed
    manifest = {"version": VERSION, "apps": apps, "desktop": desktop, "files": [], "shortcuts": [],
                "start_menu_dir": str(menu), "registry_key": registry_key}
    if desk is not None:
        manifest["desktop_dir"] = str(desk)

    progress("Copying program…", 0.1)
    # The installer embeds a small uninstaller build of this script (no program inside).
    for source, name in ((payload, PROGRAM), (bundled("uninstaller.exe"), UNINSTALLER), (bundled(ICON), ICON)):
        if not source.is_file():
            continue
        _copy(source, target / name)
        manifest["files"].append(name)
    if previous is not None:  # an update or a change of apps: the shortcuts are made again below
        for link in _owned_shortcuts(previous):
            link.unlink(missing_ok=True)
    _write_manifest(target, manifest)  # from here on, uninstall and repair know what is ours
    progress("Creating shortcuts…", 0.7)
    icon = target / ICON
    links = [(menu / f"{APPS[a][0]}.lnk", target / PROGRAM, APPS[a][1]) for a in apps]
    if UNINSTALLER in manifest["files"]:
        links.append((menu / f"Uninstall {APP_NAME}.lnk", target / UNINSTALLER, "--uninstall"))
    if desktop:
        desk = desk or default_desktop()
        links += [(desk / f"{APPS[a][0]}.lnk", target / PROGRAM, APPS[a][1]) for a in apps]
    for link, program, arguments in links:
        make_shortcut(link, program, arguments, icon, target)
        manifest["shortcuts"].append(str(link))
        _write_manifest(target, manifest)

    if registry_key:
        progress("Registering with Windows…", 0.9)
        size_kb = sum((target / f).stat().st_size for f in manifest["files"]) // 1024
        register(registry_key, target, size_kb)
    _write_manifest(target, manifest)
    progress("Done", 1.0)
    return target / PROGRAM


def uninstall(target: Path, *, remove_settings: bool = False, remove_templates: bool = False,
              progress=lambda text, fraction=None: None) -> None:
    target = Path(target)
    manifest_path = target / MANIFEST
    if not manifest_path.is_file():
        raise SetupError(f"No {APP_NAME} installation found in {target}.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SetupError(f"{manifest_path} cannot be read: {exc}") from None
    if not isinstance(manifest, dict):
        raise SetupError(f"{manifest_path} is not an {APP_NAME} install record.")
    files, links = _owned_files(manifest), _owned_shortcuts(manifest)
    if remove_settings or remove_templates:
        # First, while the install record is still there: if this fails, Retry starts over cleanly.
        progress("Removing your settings…" if remove_settings else "Removing your FX templates…", 0.1)
        remove_user_data(remove_settings, remove_templates)
    progress("Removing shortcuts…", 0.2)
    for link in links:
        link.unlink(missing_ok=True)
    menu = Path(manifest.get("start_menu_dir", ""))
    if menu.name == APP_NAME and menu.is_dir() and not any(menu.iterdir()):
        menu.rmdir()
    if manifest.get("registry_key") == REGISTRY_KEY:
        progress("Unregistering…", 0.4)
        unregister(REGISTRY_KEY)
    progress("Removing files…", 0.6)
    running_from_target = Path(sys.executable).resolve().parent == target.resolve()
    for name in files:
        path = target / name
        if running_from_target and path.resolve() == Path(sys.executable).resolve():
            continue  # removed after this process exits
        try:
            path.unlink(missing_ok=True)
        except PermissionError:
            raise SetupError(f"{name} is in use. Close {APP_NAME} and try again.") from None
    manifest_path.unlink()
    if running_from_target:
        # Delete this uninstaller and the (then empty) folder once this process has exited.
        _delete_after_exit(Path(sys.executable), target)
    elif not any(target.iterdir()):
        target.rmdir()
    progress("Done", 1.0)


def _owned_files(manifest: dict) -> list[str]:
    """Manifest file entries, limited to the names install() writes: never a path elsewhere."""
    return [name for name in manifest.get("files", []) if isinstance(name, str) and name in OWNED_FILES]


def _owned_shortcuts(manifest: dict) -> list[Path]:
    """Manifest shortcuts, limited to the .lnk names install() creates in the recorded Start-menu and
    desktop folders (or the user's desktop)."""
    names = {f"{name}.lnk" for name, _ in SHORTCUTS} | {f"Uninstall {APP_NAME}.lnk"}
    recorded = [Path(manifest[k]) for k in ("start_menu_dir", "desktop_dir") if isinstance(manifest.get(k), str)]
    folders = {os.path.normcase(str(f.resolve())) for f in recorded if str(f) not in ("", ".")}
    try:
        folders.add(os.path.normcase(str(default_desktop().resolve())))
    except (OSError, AttributeError):  # no shell32 (not Windows)
        pass
    links = []
    for entry in manifest.get("shortcuts", []):
        if not isinstance(entry, str):
            continue
        link = Path(entry)
        if link.name in names and os.path.normcase(str(link.parent.resolve())) in folders:
            links.append(link)
    return links


def _delete_after_exit(uninstaller: Path, folder: Path) -> None:
    """Delete this uninstaller and the (then empty) folder once this process has exited. Paths go in
    through the environment, not the command line, so no character in them is interpreted."""
    # Retries for up to 30 s: the one-file launcher keeps the program locked until it has exited too.
    script = ("for ($i = 0; $i -lt 30 -and (Test-Path -LiteralPath $env:A2D_EXE); $i++) { Start-Sleep -Seconds 1; "
              "Remove-Item -LiteralPath $env:A2D_EXE -Force -ErrorAction SilentlyContinue }; "
              "if ((Test-Path -LiteralPath $env:A2D_DIR) -and -not (Get-ChildItem -LiteralPath $env:A2D_DIR -Force)) "
              "{ Remove-Item -LiteralPath $env:A2D_DIR -Force -ErrorAction SilentlyContinue }")
    env = {**os.environ, "A2D_EXE": str(uninstaller), "A2D_DIR": str(folder)}
    # No DETACHED_PROCESS: PowerShell started detached exits without running the script, which is why
    # 0.16.0's uninstaller was left behind. A hidden window in its own process group outlives us.
    subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env,
                     creationflags=NO_WINDOW | 0x00000200, close_fds=True)  # CREATE_NEW_PROCESS_GROUP


# --- windows -----------------------------------------------------------------------------

ACCENT, HEADER_BG, HEADER_FG, MUTED = "#2f7de1", "#24272f", "#ffffff", "#5f6672"


def _tk_root(title: str):
    import tkinter as tk
    from tkinter import ttk
    root = tk.Tk()
    root.title(title)
    root.resizable(False, False)
    icon = bundled(ICON)
    if icon.is_file():
        try:
            root.iconbitmap(str(icon))
        except tk.TclError:
            pass
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    style.configure("Title.TLabel", font=("Segoe UI", 15, "bold"))
    style.configure("Heading.TLabel", font=("Segoe UI", 11, "bold"))
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Error.TLabel", foreground="#c0392b")
    return root


def _center(root) -> None:
    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    root.geometry(f"+{(root.winfo_screenwidth() - w) // 2}+{(root.winfo_screenheight() - h) // 3}")


def run_wizard(uninstalling: bool) -> int:
    if uninstalling:
        return _uninstall_wizard()
    return SetupWizard().run()


def _uninstall_wizard() -> int:
    """Confirm, with two unticked options for what the app stored outside its folder."""
    import tkinter as tk
    from tkinter import messagebox, ttk
    target = Path(sys.executable).resolve().parent
    root = _tk_root(f"Uninstall {APP_NAME}")
    header = tk.Frame(root, bg=HEADER_BG, padx=20, pady=14)
    header.pack(fill="x")
    tk.Label(header, text=f"Uninstall {APP_NAME}", bg=HEADER_BG, fg=HEADER_FG, font=("Segoe UI", 14, "bold"),
             anchor="w").pack(fill="x")
    tk.Label(header, text=str(target), bg=HEADER_BG, fg="#b8bfcc", font=("Segoe UI", 9), anchor="w").pack(fill="x")
    body = ttk.Frame(root, padding=(20, 16, 20, 8))
    body.pack(fill="both", expand=True)
    ttk.Label(body, wraplength=500, justify="left", text=(
        f"This removes {APP_NAME} and the Anim2D FX Builder, their shortcuts and their entry in Installed apps. "
        "Your projects and generated FX packages are always kept.")).pack(anchor="w")
    settings, templates = tk.BooleanVar(value=False), tk.BooleanVar(value=False)
    ttk.Checkbutton(body, text="Also remove my settings and downloaded updates", variable=settings).pack(
        anchor="w", pady=(14, 0))
    ttk.Label(body, style="Muted.TLabel", text="Theme, recent projects, shortcuts, window layout, logs, update "
              "downloads").pack(anchor="w", padx=(22, 0))
    ttk.Checkbutton(body, text="Also delete my FX templates", variable=templates).pack(anchor="w", pady=(8, 0))
    ttk.Label(body, style="Muted.TLabel", text=f"{template_folders()[0]}: templates you registered from Spine "
              "exports").pack(anchor="w", padx=(22, 0))
    ttk.Separator(root).pack(fill="x", pady=(8, 0))
    bar = ttk.Frame(root, padding=(20, 10))
    bar.pack(fill="x")
    result = {"code": 1}

    def remove() -> None:
        if templates.get() and not messagebox.askyesno(
                f"Uninstall {APP_NAME}", "Delete your FX templates too? This can't be undone.", parent=root):
            return
        go.state(["disabled"])
        while True:
            try:
                uninstall(target, remove_settings=settings.get(), remove_templates=templates.get())
                break
            except SetupError as exc:
                if not messagebox.askretrycancel(f"Uninstall {APP_NAME}", str(exc), parent=root):
                    go.state(["!disabled"])
                    return
        result["code"] = 0
        kept = ["projects"] + ([] if templates.get() else ["FX templates"]) + ([] if settings.get() else ["settings"])
        listed = kept[0] if len(kept) == 1 else ", ".join(kept[:-1]) + " and " + kept[-1]
        messagebox.showinfo(f"Uninstall {APP_NAME}", f"{APP_NAME} was removed. Your {listed} were kept.",
                            parent=root)
        root.destroy()

    ttk.Button(bar, text="Cancel", command=root.destroy).pack(side="right")
    go = ttk.Button(bar, text="Uninstall", command=remove)
    go.pack(side="right", padx=(0, 8))
    _center(root)
    root.mainloop()
    return result["code"]


class SetupWizard:
    """Welcome → Choose apps and folder → Installing → Finished (or an error with Retry)."""

    def __init__(self):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.existing = find_existing()
        self.previous = read_manifest(self.existing) if self.existing else None
        self.root = _tk_root(f"{APP_NAME} {VERSION} Setup")
        self.result = 1
        self.program: Path | None = None
        self.launch_now, self.installed_apps = False, []
        chosen = manifest_apps(self.previous)
        self.app_vars = {a: tk.BooleanVar(value=a in chosen) for a in APPS}
        self.folder = tk.StringVar(value=str(self.existing or default_target()))
        self.desktop = tk.BooleanVar(value=manifest_desktop(self.previous))
        self.launch = tk.BooleanVar(value=True)
        self.status = tk.StringVar()
        self.error = tk.StringVar()

        header = tk.Frame(self.root, bg=HEADER_BG, padx=20, pady=14)
        header.pack(fill="x")
        self.heading = tk.Label(header, bg=HEADER_BG, fg=HEADER_FG, font=("Segoe UI", 14, "bold"), anchor="w")
        self.heading.pack(fill="x")
        self.subheading = tk.Label(header, bg=HEADER_BG, fg="#b8bfcc", font=("Segoe UI", 9), anchor="w")
        self.subheading.pack(fill="x")
        self.body = ttk.Frame(self.root, padding=(20, 16, 20, 8), width=560, height=300)
        self.body.pack(fill="both", expand=True)
        self.body.pack_propagate(False)
        ttk.Separator(self.root).pack(fill="x")
        bar = ttk.Frame(self.root, padding=(20, 10))
        bar.pack(fill="x")
        self.back = ttk.Button(bar, text="< Back")
        self.next = ttk.Button(bar, text="Next >")
        self.cancel = ttk.Button(bar, text="Cancel", command=self._cancel)
        self.cancel.pack(side="right")
        self.next.pack(side="right", padx=(0, 8))
        self.back.pack(side="right", padx=(0, 4))
        self.root.protocol("WM_DELETE_WINDOW", self._cancel)
        for var in self.app_vars.values():
            var.trace_add("write", lambda *_: self._validate())
        self.folder.trace_add("write", lambda *_: self._validate())
        self._welcome()
        _center(self.root)

    def run(self) -> int:
        self.root.mainloop()
        if self.result == 0 and self.launch_now and self.program is not None:
            start_program(self.program, launch_arguments(self.installed_apps))
        return self.result

    # pages

    def _page(self, heading: str, subheading: str):
        for child in self.body.winfo_children():
            child.destroy()
        self.heading.config(text=heading)
        self.subheading.config(text=subheading)
        self.error.set("")
        return self.body

    def _buttons(self, back=None, next_text="Next >", next_command=None, cancel=True):
        self.back.config(command=back or (lambda: None), state="normal" if back else "disabled")
        self.next.config(text=next_text, command=next_command or (lambda: None),
                         state="normal" if next_command else "disabled")
        self.cancel.config(state="normal" if cancel else "disabled")

    def _welcome(self) -> None:
        ttk = self.ttk
        page = self._page(f"Welcome to {APP_NAME} {VERSION}",
                          "Free and open-source 2D animation, released under the MIT License")
        ttk.Label(page, wraplength=500, justify="left", text=(
            f"{APP_NAME} is a 2D skeletal animation editor that reads and writes Spine data, with an FX "
            "builder for game effects.\n\nSetup installs it for your user account only, so it doesn't need "
            "administrator rights.")).pack(anchor="w")
        if self.previous is not None:
            old = self.previous.get("version", "an earlier version")
            box = ttk.LabelFrame(page, text="Already installed", padding=10)
            box.pack(fill="x", pady=(18, 0))
            verb = "reinstall" if old == VERSION else f"update it from {old} to {VERSION}"
            ttk.Label(box, wraplength=490, justify="left", text=(
                f"{APP_NAME} {old} is installed in {self.existing}. Setup will {verb}. Your projects, "
                "FX templates and settings are kept.")).pack(anchor="w")
        self._buttons(next_command=self._options)

    def _options(self) -> None:
        ttk = self.ttk
        page = self._page("Choose what to install", "Both apps are selected by default")
        for app, (name, _, description) in APPS.items():
            row = ttk.Frame(page)
            row.pack(fill="x", pady=(0, 6))
            ttk.Checkbutton(row, text=name, variable=self.app_vars[app], style="TCheckbutton").pack(anchor="w")
            ttk.Label(row, text=description, style="Muted.TLabel").pack(anchor="w", padx=(22, 0))
        ttk.Label(page, wraplength=500, justify="left", style="Muted.TLabel", text=(
            "They're one program, so this picks which apps get shortcuts. The editor's Insert FX works either "
            "way.")).pack(anchor="w", pady=(0, 12))
        ttk.Label(page, text="Install folder").pack(anchor="w")
        row = ttk.Frame(page)
        row.pack(fill="x", pady=(2, 0))
        entry = ttk.Entry(row, textvariable=self.folder)
        entry.pack(side="left", fill="x", expand=True)
        browse = ttk.Button(row, text="Browse…", command=self._browse)
        browse.pack(side="left", padx=(6, 0))
        if self.previous is not None:  # an update stays where it is, so there is never a second copy
            entry.state(["disabled"])
            browse.state(["disabled"])
            ttk.Label(page, text="Updates go into the existing installation.",
                      style="Muted.TLabel").pack(anchor="w")
        ttk.Checkbutton(page, text="Create desktop shortcuts", variable=self.desktop).pack(anchor="w", pady=(12, 0))
        ttk.Label(page, textvariable=self.error, style="Error.TLabel", wraplength=500).pack(anchor="w", pady=(8, 0))
        verb = "Update" if self.previous is not None else "Install"
        self._buttons(back=self._welcome, next_text=verb, next_command=self._install)
        self._validate()

    def _installing(self) -> None:
        ttk = self.ttk
        page = self._page("Installing…" if self.previous is None else "Updating…", f"{APP_NAME} {VERSION}")
        self.bar = ttk.Progressbar(page, mode="determinate", maximum=1.0, length=520)
        self.bar.pack(fill="x", pady=(30, 8))
        ttk.Label(page, textvariable=self.status, style="Muted.TLabel").pack(anchor="w")
        self._buttons(cancel=False)

    def _finished(self) -> None:
        ttk = self.ttk
        apps = self._apps()
        names = " and ".join(APPS[a][0] for a in apps)
        updated = self.previous is not None
        page = self._page(f"{APP_NAME} is {'updated' if updated else 'installed'}", f"Version {VERSION}")
        where = f"Find {names} in the Start menu under “{APP_NAME}”"
        where += " and on your desktop." if self.desktop.get() else "."
        ttk.Label(page, text=where, wraplength=500, justify="left").pack(anchor="w")
        ttk.Label(page, wraplength=500, justify="left", style="Muted.TLabel", text=(
            "Anim2D checks for updates once a day; change that in Settings → Updates. To remove it, use "
            "Windows Settings → Apps → Installed apps.")).pack(anchor="w", pady=(10, 0))
        first = APPS["editor" if "editor" in apps else "fx"][0]
        ttk.Checkbutton(page, text=f"Start {first} now", variable=self.launch).pack(anchor="w", pady=(18, 0))
        self._buttons(next_text="Finish", next_command=self._finish, cancel=False)
        self.next.focus_set()

    def _failed(self, message: str) -> None:
        ttk = self.ttk
        page = self._page("Setup couldn't finish", "Nothing outside the install folder was changed")
        ttk.Label(page, text=message, wraplength=500, justify="left", style="Error.TLabel").pack(anchor="w")
        ttk.Label(page, wraplength=500, justify="left", style="Muted.TLabel", text=(
            "Close any open Anim2D or FX Builder windows, then choose Retry. Running Setup again later also "
            "repairs a partial install.")).pack(anchor="w", pady=(12, 0))
        self._buttons(back=self._options, next_text="Retry", next_command=self._install)

    # actions

    def _apps(self) -> list[str]:
        return [a for a, var in self.app_vars.items() if var.get()]

    def _validate(self) -> bool:
        problem = ""
        if not self._apps():
            problem = "Choose at least one app."
        elif self.previous is None:
            folder = Path(self.folder.get().strip() or ".")
            if not self.folder.get().strip():
                problem = "Choose an install folder."
            elif folder.exists() and not folder.is_dir():
                problem = f"{folder} is a file, not a folder."
            elif read_manifest(folder) is None and foreign_files(folder):
                problem = f"{folder} already contains other files. Choose an empty or new folder."
        self.error.set(problem)
        if str(self.next.cget("text")) in ("Install", "Update"):
            self.next.state(["disabled"] if problem else ["!disabled"])
        return not problem

    def _browse(self) -> None:
        from tkinter import filedialog
        chosen = filedialog.askdirectory(initialdir=self.folder.get(), title="Install folder")
        if chosen:
            folder = Path(chosen)
            # Installing into a folder of your choice puts Anim2D in its own subfolder unless it's empty.
            if read_manifest(folder) is None and foreign_files(folder):
                folder = folder / APP_NAME
            self.folder.set(str(folder))

    def _install(self) -> None:
        if not self._validate():
            return
        self._installing()

        def progress(text: str, fraction=None) -> None:
            self.status.set(text)
            if fraction is not None:
                self.bar["value"] = fraction
            self.root.update()

        try:
            self.program = install(Path(self.folder.get().strip()), apps=self._apps(), desktop=self.desktop.get(),
                                   progress=progress)
        except (SetupError, OSError) as exc:
            self._failed(str(exc))
            return
        self.result = 0
        self._finished()

    def _finish(self) -> None:
        # Read the choices while Tk is alive: its variables can't be read after destroy().
        self.launch_now, self.installed_apps = self.launch.get(), self._apps()
        self.root.destroy()

    def _cancel(self) -> None:
        from tkinter import messagebox
        if str(self.cancel.cget("state")) == "disabled":
            return
        if messagebox.askyesno(f"{APP_NAME} Setup", f"Cancel {APP_NAME} Setup?", parent=self.root):
            self.root.destroy()


def run_update(target: Path, wait_pid: int, launch: bool) -> int:
    """Update mode (started by the app): a small progress window, Retry if the app is still open."""
    import tkinter as tk
    from tkinter import messagebox, ttk
    previous = read_manifest(target)
    if previous is None:
        root = _tk_root(f"{APP_NAME} update")
        root.withdraw()
        messagebox.showerror(f"{APP_NAME} update", f"No {APP_NAME} installation was found in {target}. "
                             "Download the installer from the release page instead.")
        return 1
    root = _tk_root(f"Updating {APP_NAME}")
    frame = ttk.Frame(root, padding=20)
    frame.pack()
    ttk.Label(frame, text=f"Updating {APP_NAME} to {VERSION}", style="Heading.TLabel").pack(anchor="w")
    status = tk.StringVar(value=f"Waiting for {APP_NAME} to close…")
    bar = ttk.Progressbar(frame, mode="determinate", maximum=1.0, length=360)
    bar.pack(pady=(12, 6))
    ttk.Label(frame, textvariable=status, style="Muted.TLabel").pack(anchor="w")
    _center(root)
    root.update()

    def progress(text: str, fraction=None) -> None:
        status.set(text)
        if fraction is not None:
            bar["value"] = fraction
        root.update()

    wait_for_exit(wait_pid)
    apps = manifest_apps(previous)
    while True:
        try:
            recorded = {k: Path(previous[k]) for k in ("start_menu_dir", "desktop_dir")
                        if isinstance(previous.get(k), str)}
            program = install(target, apps=apps, desktop=manifest_desktop(previous),
                              start_menu_dir=recorded.get("start_menu_dir"), desktop_dir=recorded.get("desktop_dir"),
                              registry_key=REGISTRY_KEY if previous.get("registry_key") else None,
                              progress=progress)
            break
        except (SetupError, OSError) as exc:
            if not messagebox.askretrycancel(f"{APP_NAME} update", f"{exc}\n\nThe installed version still "
                                             "works; you can update later from Settings → Updates.", parent=root):
                root.destroy()
                return 1
    root.destroy()
    if launch:
        start_program(program, launch_arguments(apps))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} setup")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--remove-settings", action="store_true",
                        help="with --uninstall: also remove settings and downloaded updates")
    parser.add_argument("--remove-templates", action="store_true",
                        help="with --uninstall: also delete the user's FX templates")
    parser.add_argument("--silent", action="store_true")
    parser.add_argument("--update", action="store_true", help="update the installation in --target")
    parser.add_argument("--wait-pid", type=int, default=0, help="wait for this process to exit first")
    parser.add_argument("--target", type=Path)
    parser.add_argument("--apps", default=",".join(APPS), help="editor, fx or editor,fx (default)")
    parser.add_argument("--no-desktop", action="store_true")
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--start-menu-dir", type=Path)
    parser.add_argument("--desktop-dir", type=Path)
    parser.add_argument("--registry-key", default=REGISTRY_KEY)
    parser.add_argument("--no-register", action="store_true")
    args = parser.parse_args(argv)
    if args.update:
        return run_update(args.target or find_existing() or default_target(), args.wait_pid, args.launch)
    if not args.silent:
        return run_wizard(args.uninstall)
    apps = [a.strip() for a in args.apps.split(",") if a.strip()]
    unknown = [a for a in apps if a not in APPS]
    if unknown:
        print(f"error: unknown app {', '.join(unknown)} (choose from {', '.join(APPS)})", file=sys.stderr)
        return 2
    try:
        if args.uninstall:
            uninstall(args.target or Path(sys.executable).resolve().parent, remove_settings=args.remove_settings,
                      remove_templates=args.remove_templates)
        else:
            program = install(args.target or default_target(), apps=apps, desktop=not args.no_desktop,
                              start_menu_dir=args.start_menu_dir, desktop_dir=args.desktop_dir,
                              registry_key=None if args.no_register else args.registry_key)
            if args.launch:
                start_program(program, launch_arguments(apps))
    except SetupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
