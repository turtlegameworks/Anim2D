"""Picks the official spine-ts runtime that matches a skeleton file's version stamp."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

VENDOR = Path(__file__).parent / "runtime" / "vendor"
CORE38 = VENDOR / "spine-core-3.8.js"
CORE40 = VENDOR / "v40" / "spine-core-4.0.js"
NODE = shutil.which("node")
HAVE_RUNTIMES = bool(NODE) and CORE38.is_file() and CORE40.is_file()


def core_for(json_path: Path) -> Path:
    """.skel files are always 4.0 binary (the only binary Anim2D writes)."""
    if Path(json_path).suffix == ".skel":
        stamp = "4.0"
    else:
        stamp = str(json.loads(Path(json_path).read_text(encoding="utf-8"))["skeleton"].get("spine", ""))
    core = CORE40 if stamp.startswith("4.") else CORE38
    if not core.is_file():
        import pytest
        pytest.skip(f"needs {core.name}: run tools/fetch_spine_runtime.py")
    return core
