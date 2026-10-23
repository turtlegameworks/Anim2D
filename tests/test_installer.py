"""The per-user installer (installer/anim2d_setup.py) with shortcuts, registry and payload redirected to
temporary folders: app choices, updates that keep them, files in use, and the optional data removal."""

from __future__ import annotations

import importlib.util
import json
import uuid
import winreg
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("anim2d_setup_t", ROOT / "installer" / "anim2d_setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    payload = tmp_path / "payload"
    payload.mkdir()
    for name in ("Anim2D.exe", "uninstaller.exe", "anim2d.ico"):
        (payload / name).write_bytes(name.encode())
    monkeypatch.setattr(module, "bundled", lambda name: payload / name)
    monkeypatch.setattr(module, "make_shortcut",
                        lambda link, target, args, icon, workdir: (link.parent.mkdir(parents=True, exist_ok=True),
                                                                   link.write_text(f"{target} {args}")))
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    monkeypatch.setattr(module, "default_desktop", lambda: desktop)
    module.test_paths = {"target": tmp_path / "Programs" / "Anim2D", "menu": tmp_path / "menu" / "Anim2D",
                         "desktop": desktop, "payload": payload}
    return module


def _install(setup, **kw):
    p = setup.test_paths
    return setup.install(p["target"], start_menu_dir=p["menu"], desktop_dir=p["desktop"], registry_key=None, **kw)


def _links(folder: Path) -> set[str]:
    return {f.name for f in folder.glob("*.lnk")}


def test_both_apps_by_default(setup):
    program = _install(setup)
    p = setup.test_paths
    assert program == p["target"] / "Anim2D.exe" and program.read_bytes() == b"Anim2D.exe"
    assert _links(p["menu"]) == {"Anim2D.lnk", "Anim2D FX Builder.lnk", "Uninstall Anim2D.lnk"}
    assert _links(p["desktop"]) == {"Anim2D.lnk", "Anim2D FX Builder.lnk"}
    assert "--fx-builder" in (p["menu"] / "Anim2D FX Builder.lnk").read_text()
    manifest = json.loads((p["target"] / "install.json").read_text())
    assert manifest["apps"] == ["editor", "fx"] and manifest["desktop"] is True
    assert not list(p["target"].glob("*.new")), "no temporary copies are left"


@pytest.mark.parametrize("apps, menu, launch", [
    (["editor"], {"Anim2D.lnk", "Uninstall Anim2D.lnk"}, []),
    (["fx"], {"Anim2D FX Builder.lnk", "Uninstall Anim2D.lnk"}, ["--fx-builder"]),
])
def test_one_app_only(setup, apps, menu, launch):
    _install(setup, apps=apps, desktop=False)
    p = setup.test_paths
    assert _links(p["menu"]) == menu and not _links(p["desktop"])
    assert setup.launch_arguments(apps) == launch


def test_at_least_one_app(setup):
    with pytest.raises(setup.SetupError, match="at least one"):
        _install(setup, apps=[])


def test_reinstall_with_fewer_apps_removes_their_shortcuts(setup):
    p = setup.test_paths
    _install(setup)
    _install(setup, apps=["fx"])
    assert _links(p["menu"]) == {"Anim2D FX Builder.lnk", "Uninstall Anim2D.lnk"}
    assert _links(p["desktop"]) == {"Anim2D FX Builder.lnk"}


def test_update_keeps_the_choices_made_at_install(setup, monkeypatch):
    p = setup.test_paths
    _install(setup, apps=["editor"], desktop=False)
    (p["payload"] / "Anim2D.exe").write_bytes(b"new version")
    manifest = setup.read_manifest(p["target"])
    assert setup.manifest_apps(manifest) == ["editor"] and manifest["desktop"] is False
    _install(setup, apps=setup.manifest_apps(manifest), desktop=manifest["desktop"])
    assert (p["target"] / "Anim2D.exe").read_bytes() == b"new version"
    assert _links(p["menu"]) == {"Anim2D.lnk", "Uninstall Anim2D.lnk"} and not _links(p["desktop"])


def test_records_from_before_the_choice_mean_both_apps(setup):
    assert setup.manifest_apps({"files": []}) == ["editor", "fx"]
    assert setup.manifest_apps({"apps": ["nonsense"]}) == ["editor", "fx"]
    assert setup.manifest_apps(None) == ["editor", "fx"]


def test_waits_for_a_program_that_is_closing(setup, monkeypatch):
    real_replace, attempts = setup.os.replace, {"n": 0}

    def replace(src, dst):
        if Path(dst).name == "Anim2D.exe" and attempts["n"] < 2:
            attempts["n"] += 1
            raise PermissionError("in use")
        return real_replace(src, dst)
    monkeypatch.setattr(setup.os, "replace", replace)
    monkeypatch.setattr(setup.time, "sleep", lambda s: None)
    _install(setup)
    assert attempts["n"] == 2 and (setup.test_paths["target"] / "Anim2D.exe").is_file()


def test_gives_up_on_a_program_that_stays_open(setup, monkeypatch):
    real_replace = setup.os.replace

    def replace(src, dst):
        if Path(dst).name == "Anim2D.exe":
            raise PermissionError("in use")
        return real_replace(src, dst)
    monkeypatch.setattr(setup.os, "replace", replace)
    monkeypatch.setattr(setup, "IN_USE_WAIT", 0.0)
    with pytest.raises(setup.SetupError, match="in use. Close every Anim2D window"):
        _install(setup)
    assert not list(setup.test_paths["target"].glob("*.new"))


def test_refuses_a_folder_with_other_files(setup):
    target = setup.test_paths["target"]
    target.mkdir(parents=True)
    (target / "someone_elses.txt").write_text("x")
    with pytest.raises(setup.SetupError, match="already contains other files"):
        _install(setup)


def test_reuses_a_folder_holding_only_anim2d_leftovers(setup):
    """0.16.0's uninstaller could leave itself behind; installing again must still work there."""
    target = setup.test_paths["target"]
    target.mkdir(parents=True)
    (target / "Uninstall Anim2D.exe").write_bytes(b"old")
    (target / "Anim2D.exe.new").write_bytes(b"half copied")
    assert setup.foreign_files(target) == []
    _install(setup)
    assert (target / "Uninstall Anim2D.exe").read_bytes() == b"uninstaller.exe"
    assert not (target / "Anim2D.exe.new").exists()


def test_desktop_choice_of_installs_from_before_it_was_recorded(setup):
    """0.16.0 records have no 'desktop' key: their shortcut list tells whether there were any."""
    menu = setup.test_paths["menu"]
    only_menu = {"start_menu_dir": str(menu), "shortcuts": [str(menu / "Anim2D.lnk")]}
    with_desktop = {"start_menu_dir": str(menu), "shortcuts": [str(menu / "Anim2D.lnk"), "C:/Users/x/Desktop/Anim2D.lnk"]}
    assert setup.manifest_desktop(only_menu) is False
    assert setup.manifest_desktop(with_desktop) is True
    assert setup.manifest_desktop({"desktop": False, "shortcuts": []}) is False
    assert setup.manifest_desktop(None) is True


def test_wait_for_exit(setup):
    import subprocess
    import sys
    import time
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1)"])
    start = time.monotonic()
    assert setup.wait_for_exit(child.pid, timeout=10) and child.poll() is not None
    assert time.monotonic() - start < 9
    assert setup.wait_for_exit(0) and setup.wait_for_exit(2 ** 31 - 1), "a process that isn't there"


def test_wizard_reads_its_choices_before_closing(setup, monkeypatch):
    monkeypatch.setattr(setup, "find_existing", lambda: None)
    started = []
    monkeypatch.setattr(setup, "start_program", lambda program, args: started.append((program, args)))
    wizard = setup.SetupWizard()
    wizard.app_vars["editor"].set(False)
    wizard.program, wizard.result = Path("C:/Anim2D/Anim2D.exe"), 0
    wizard.root.after(0, wizard._finish)
    assert wizard.run() == 0
    assert started == [(Path("C:/Anim2D/Anim2D.exe"), ["--fx-builder"])]


def test_unknown_app_on_the_command_line(setup):
    assert setup.main(["--silent", "--apps", "editor,sounds", "--no-register"]) == 2


# --- uninstall and what it may remove

@pytest.fixture
def user_data(setup, tmp_path, monkeypatch):
    """Settings in a throwaway registry key, a fake update cache, templates and a project."""
    key = f"Anim2DTest_{uuid.uuid4().hex[:8]}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"Software\\{key}\\Anim2D\\shortcuts") as k:
        winreg.SetValueEx(k, "Export", 0, winreg.REG_SZ, "Ctrl+E")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"Software\\{key}\\Anim2D") as k:
        winreg.SetValueEx(k, "theme", 0, winreg.REG_SZ, "Ocean")
    monkeypatch.setattr(setup, "SETTINGS_KEYS", [(f"{key}\\Anim2D", key)])
    cache = tmp_path / "LocalAppData" / "Anim2D" / "updates"
    cache.mkdir(parents=True)
    (cache / "Anim2D-Setup-0.17.0.exe").write_bytes(b"x")
    monkeypatch.setattr(setup, "update_cache", lambda: cache)
    templates = tmp_path / "Documents" / "Anim2D Templates"
    (templates / "my_hit").mkdir(parents=True)
    (templates / "my_hit" / "preset.json").write_text("{}")
    monkeypatch.setattr(setup, "template_folders", lambda: [templates])
    project = tmp_path / "Documents" / "hero.anim2d"
    project.write_text("{}")
    yield {"key": key, "cache": cache, "templates": templates, "project": project}
    try:
        setup._delete_registry_tree(key)
    except setup.SetupError:
        pass


def _key_exists(key: str) -> bool:
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"Software\\{key}").Close()
        return True
    except FileNotFoundError:
        return False


def test_uninstall_keeps_user_data_by_default(setup, user_data):
    _install(setup)
    setup.uninstall(setup.test_paths["target"])
    assert _key_exists(f"{user_data['key']}\\Anim2D") and user_data["cache"].is_dir()
    assert user_data["templates"].is_dir() and user_data["project"].is_file()
    assert not setup.test_paths["target"].exists() and not _links(setup.test_paths["desktop"])


def test_uninstall_can_remove_settings_and_downloads(setup, user_data):
    _install(setup)
    setup.uninstall(setup.test_paths["target"], remove_settings=True)
    assert not _key_exists(user_data["key"]), "the settings key and its now-empty parent are gone"
    assert not user_data["cache"].exists() and not user_data["cache"].parent.exists(), "empty parent tidied"
    assert user_data["templates"].is_dir() and user_data["project"].is_file()


def test_uninstall_can_delete_templates_too(setup, user_data):
    _install(setup)
    setup.uninstall(setup.test_paths["target"], remove_settings=True, remove_templates=True)
    assert not user_data["templates"].exists()
    assert user_data["project"].is_file(), "projects are never touched"


def test_only_the_updates_folder_is_removed(setup, user_data):
    other = user_data["cache"].parent / "something_else.txt"
    other.write_text("not downloaded updates")
    setup.remove_user_data(settings=True, templates=False)
    assert not user_data["cache"].exists() and other.is_file()


def test_failed_data_removal_can_be_retried(setup, user_data, monkeypatch):
    _install(setup)
    real = setup._remove_tree

    def locked(folder):
        raise OSError("a template is open in another program")
    monkeypatch.setattr(setup, "_remove_tree", locked)
    with pytest.raises(setup.SetupError, match="Could not remove"):
        setup.uninstall(setup.test_paths["target"], remove_templates=True)
    assert (setup.test_paths["target"] / "install.json").is_file(), "nothing removed yet, so Retry works"
    monkeypatch.setattr(setup, "_remove_tree", real)
    setup.uninstall(setup.test_paths["target"], remove_templates=True)
    assert not user_data["templates"].exists() and not setup.test_paths["target"].exists()


def test_removing_settings_leaves_a_shared_parent_key_with_other_content(setup, user_data):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"Software\\{user_data['key']}\\SomethingElse"):
        pass
    setup.remove_user_data(settings=True, templates=False)
    assert not _key_exists(f"{user_data['key']}\\Anim2D") and _key_exists(f"{user_data['key']}\\SomethingElse")
