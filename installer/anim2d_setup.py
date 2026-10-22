"""Anim2D per-user installer and uninstaller (Windows, no admin rights needed).

Built into Anim2D-Setup-<version>.exe by tools/build_exe.py, with the program
(Anim2D.exe) embedded. Installing copies the program, creates Start-menu (and optional
desktop) shortcuts for Anim2D and the Anim2D FX Builder, and registers an
uninstaller under "Installed apps". Everything created is recorded in install.json so the
uninstaller removes exactly that and nothing else (user templates and settings are kept).

    Anim2D-Setup.exe                      install wizard
    Anim2D-Setup.exe --silent [--target DIR] [--no-desktop] [--launch]
    "Uninstall Anim2D.exe" --uninstall [--silent]

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
from pathlib import Path

APP_NAME = "Anim2D"
PROGRAM = "Anim2D.exe"
UNINSTALLER = "Uninstall Anim2D.exe"
REGISTRY_KEY = "Anim2D"
UNINSTALL_ROOT = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
MANIFEST = "install.json"
NO_WINDOW = 0x08000000
SHORTCUTS = [  # (name, arguments)
    (APP_NAME, ""),
    ("Anim2D FX Builder", "--fx-builder"),
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
    }
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{UNINSTALL_ROOT}\\{key_name}") as key:
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)


def unregister(key_name: str) -> None:
    import winreg
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, f"{UNINSTALL_ROOT}\\{key_name}")
    except FileNotFoundError:
        pass


def start_program(program: Path) -> None:
    """Start the installed program as an independent process. This installer is itself a
    PyInstaller build, so its bootloader variables must not leak into the program."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI") and k != "_MEIPASS2"}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen([str(program)], cwd=str(program.parent), env=env, close_fds=True,
                     creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


# --- install / uninstall -----------------------------------------------------------------

def install(target: Path, *, desktop: bool = True, start_menu_dir: Path | None = None,
            desktop_dir: Path | None = None, registry_key: str | None = REGISTRY_KEY,
            progress=lambda text: None) -> Path:
    payload = bundled(PROGRAM)
    if not payload.is_file():
        raise SetupError("The program is missing from this installer.")
    target = Path(target)
    if target.exists() and any(target.iterdir()) and not (target / MANIFEST).is_file():
        raise SetupError(f"{target} already contains other files. Choose an empty folder or a new one.")
    target.mkdir(parents=True, exist_ok=True)
    files = []

    progress("Copying program…")
    # The installer embeds a small uninstaller build of this script (no program inside).
    for source, name in ((payload, PROGRAM), (bundled("uninstaller.exe"), UNINSTALLER),
                         (bundled(ICON), ICON)):
        if not source.is_file():
            if name == PROGRAM:
                raise SetupError(f"{name} is missing from this installer.")
            continue
        destination = target / name
        try:
            shutil.copyfile(source, destination)
        except PermissionError:
            raise SetupError(f"{destination.name} is in use. Close {APP_NAME} and try again.") from None
        files.append(name)

    shortcuts = []
    icon = target / ICON
    progress("Creating shortcuts…")
    menu = Path(start_menu_dir) if start_menu_dir else default_start_menu()
    for name, args in SHORTCUTS:
        link = menu / f"{name}.lnk"
        make_shortcut(link, target / PROGRAM, args, icon, target)
        shortcuts.append(str(link))
    if UNINSTALLER in files:
        link = menu / f"Uninstall {APP_NAME}.lnk"
        make_shortcut(link, target / UNINSTALLER, "--uninstall", icon, target)
        shortcuts.append(str(link))
    if desktop:
        desk = Path(desktop_dir) if desktop_dir else default_desktop()
        for name, args in SHORTCUTS:
            link = desk / f"{name}.lnk"
            make_shortcut(link, target / PROGRAM, args, icon, target)
            shortcuts.append(str(link))

    if registry_key:
        progress("Registering uninstaller…")
        size_kb = sum((target / f).stat().st_size for f in files) // 1024
        register(registry_key, target, size_kb)
    manifest = {"version": VERSION, "files": files, "shortcuts": shortcuts,
                "start_menu_dir": str(menu), "registry_key": registry_key}
    (target / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    progress("Done")
    return target / PROGRAM


def uninstall(target: Path, *, progress=lambda text: None) -> None:
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
    progress("Removing shortcuts…")
    for link in links:
        link.unlink(missing_ok=True)
    menu = Path(manifest.get("start_menu_dir", ""))
    if menu.name == APP_NAME and menu.is_dir() and not any(menu.iterdir()):
        menu.rmdir()
    if manifest.get("registry_key") == REGISTRY_KEY:
        progress("Unregistering…")
        unregister(REGISTRY_KEY)
    progress("Removing files…")
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
    progress("Done")


def _owned_files(manifest: dict) -> list[str]:
    """Manifest file entries, limited to the names install() writes: never a path elsewhere."""
    return [name for name in manifest.get("files", []) if isinstance(name, str) and name in OWNED_FILES]


def _owned_shortcuts(manifest: dict) -> list[Path]:
    """Manifest shortcuts, limited to the .lnk names install() creates in the recorded Start-menu folder
    or on the desktop."""
    names = {f"{name}.lnk" for name, _ in SHORTCUTS} | {f"Uninstall {APP_NAME}.lnk"}
    menu = Path(manifest.get("start_menu_dir") or "")
    folders = {os.path.normcase(str(menu.resolve()))} if str(menu) not in ("", ".") else set()
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
    script = ("Start-Sleep -Seconds 2; Remove-Item -LiteralPath $env:A2D_EXE -Force -ErrorAction SilentlyContinue; "
              "if (-not (Get-ChildItem -LiteralPath $env:A2D_DIR -Force)) "
              "{ Remove-Item -LiteralPath $env:A2D_DIR -Force -ErrorAction SilentlyContinue }")
    env = {**os.environ, "A2D_EXE": str(uninstaller), "A2D_DIR": str(folder)}
    subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env,
                     creationflags=NO_WINDOW | 0x00000008, close_fds=True)  # DETACHED_PROCESS


# --- wizard ------------------------------------------------------------------------------

def run_wizard(uninstalling: bool) -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title(f"{APP_NAME} Setup" if not uninstalling else f"Uninstall {APP_NAME}")
    icon = bundled(ICON)
    if icon.is_file():
        root.iconbitmap(str(icon))
    if uninstalling:
        root.withdraw()
        target = Path(sys.executable).resolve().parent
        if not messagebox.askyesno(f"Uninstall {APP_NAME}",
                                   f"Remove {APP_NAME} and the Anim2D FX Builder from\n{target}?\n\n"
                                   "Your projects, templates and settings are kept."):
            return 1
        try:
            uninstall(target)
        except SetupError as exc:
            messagebox.showerror(f"Uninstall {APP_NAME}", str(exc))
            return 1
        messagebox.showinfo(f"Uninstall {APP_NAME}", f"{APP_NAME} was removed.")
        return 0

    root.resizable(False, False)
    frame = ttk.Frame(root, padding=18)
    frame.grid()
    ttk.Label(frame, text=f"{APP_NAME} {VERSION}", font=("Segoe UI", 15, "bold")).grid(columnspan=3, sticky="w")
    ttk.Label(frame, justify="left", text=(
        "Installs two tools for your user account (no administrator rights needed):\n"
        "  • Anim2D: 2D skeletal animation editor (Spine 3.8 / 4.0 data)\n"
        "  • Anim2D FX Builder: editable FX for Spine\n")).grid(columnspan=3, sticky="w", pady=(6, 10))
    ttk.Label(frame, text="Install folder").grid(row=2, column=0, sticky="w")
    folder = tk.StringVar(value=str(default_target()))
    ttk.Entry(frame, textvariable=folder, width=56).grid(row=2, column=1, padx=6)
    ttk.Button(frame, text="Browse…",
               command=lambda: folder.set(filedialog.askdirectory(initialdir=folder.get()) or folder.get())
               ).grid(row=2, column=2)
    desktop = tk.BooleanVar(value=True)
    launch = tk.BooleanVar(value=True)
    ttk.Checkbutton(frame, text="Create desktop shortcuts", variable=desktop).grid(row=3, column=1, sticky="w", pady=(10, 0))
    ttk.Checkbutton(frame, text=f"Start {APP_NAME} when setup finishes", variable=launch).grid(row=4, column=1, sticky="w")
    status = tk.StringVar(value="")
    ttk.Label(frame, textvariable=status, foreground="#2f7de1").grid(row=5, column=0, columnspan=3, sticky="w", pady=(12, 0))
    buttons = ttk.Frame(frame)
    buttons.grid(row=6, column=0, columnspan=3, sticky="e", pady=(12, 0))
    result = {"code": 1}

    def progress(text: str) -> None:
        status.set(text)
        root.update()

    def do_install() -> None:
        install_button.state(["disabled"])
        try:
            program = install(Path(folder.get()), desktop=desktop.get(), progress=progress)
        except (SetupError, OSError) as exc:
            status.set("")
            messagebox.showerror(f"{APP_NAME} Setup", str(exc))
            install_button.state(["!disabled"])
            return
        result["code"] = 0
        messagebox.showinfo(f"{APP_NAME} Setup",
                            f"{APP_NAME} is installed.\n\nFind it in the Start menu under “{APP_NAME}”"
                            f"{' and on your desktop' if desktop.get() else ''}.")
        root.destroy()
        if launch.get():
            start_program(program)

    install_button = ttk.Button(buttons, text="Install", command=do_install)
    install_button.grid(row=0, column=0, padx=4)
    ttk.Button(buttons, text="Cancel", command=root.destroy).grid(row=0, column=1)
    root.mainloop()
    return result["code"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} setup")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--silent", action="store_true")
    parser.add_argument("--target", type=Path)
    parser.add_argument("--no-desktop", action="store_true")
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--start-menu-dir", type=Path)
    parser.add_argument("--desktop-dir", type=Path)
    parser.add_argument("--registry-key", default=REGISTRY_KEY)
    parser.add_argument("--no-register", action="store_true")
    args = parser.parse_args(argv)
    if not args.silent:
        return run_wizard(args.uninstall)
    try:
        if args.uninstall:
            uninstall(args.target or Path(sys.executable).resolve().parent)
        else:
            program = install(args.target or default_target(), desktop=not args.no_desktop,
                              start_menu_dir=args.start_menu_dir, desktop_dir=args.desktop_dir,
                              registry_key=None if args.no_register else args.registry_key)
            if args.launch:
                start_program(program)
    except SetupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
