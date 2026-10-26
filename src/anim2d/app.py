"""Desktop entry point: python -m anim2d.app (or the anim2d-fx-builder command)."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .presets.library import PresetLibrary, default_user_library_dir
from .resources import FX_APP_NAME, apply_app_icon
from .ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(FX_APP_NAME)
    from . import logs, paths
    logs.setup("fx")
    logs.install_error_handler()
    paths.migrate()
    from .editor.theme import apply_theme, saved_theme
    apply_theme(app, saved_theme("Midnight"))  # the editor's theme; Midnight until one is chosen
    apply_app_icon(app)
    library = PresetLibrary.load(user_root=default_user_library_dir())
    window = MainWindow(library)
    window.show()
    if len(sys.argv) > 2 and sys.argv[1] == "--self-test":
        return _self_test(window, library, Path(sys.argv[2]))
    return app.exec()


def _self_test(window, library: PresetLibrary, out_dir: Path) -> int:
    """Build check (anim2d-fx-builder --self-test DIR): generate a package and take a screenshot."""
    import json
    import traceback
    from .models.settings import validate_settings
    from .packaging.package import write_package
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        QApplication.processEvents()
        window.grab().save(str(out_dir / "fx_builder.png"))
        settings, _ = validate_settings(library.get("impact_basic"), "fx_selftest_01", {})
        result = write_package(library.get("impact_basic"), settings, out_dir / "packages")
        report = {"ok": result.ok, "presets": sorted(library.presets), "library_errors": library.errors,
                  "package": str(result.output_dir), "errors": result.report.errors}
    except Exception:
        report = {"ok": False, "error": traceback.format_exc()}
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
