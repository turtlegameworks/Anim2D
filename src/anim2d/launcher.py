"""One program for both tools: `Anim2D.exe` opens the editor, `Anim2D.exe --fx-builder`
opens the Anim2D FX Builder. Other arguments are passed on (files to open, --self-test)."""

from __future__ import annotations

import sys


def main() -> int:
    if "--fx-builder" in sys.argv:
        sys.argv.remove("--fx-builder")
        from .app import main as fx_builder
        return fx_builder()
    from .editor.app import main as editor
    return editor()


if __name__ == "__main__":
    raise SystemExit(main())
