"""The GitHub release's description: the version's entry in CHANGELOG.md and a table of the downloads.

    python tools/release_notes.py 0.18.0 > notes.md

Fails if CHANGELOG.md has no entry (a "## 0.18.0 ..." heading with something under it) for the version, so
no release goes out without its changelog.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from anim2d.resources import REPOSITORY  # noqa: E402

DOWNLOADS = (
    ("Windows: installer", "Anim2D-Setup-{v}.exe"),
    ("Windows: portable, no install", "Anim2D-Portable-{v}.exe"),
    ("macOS: Apple silicon (M1 and later)", "Anim2D-{v}-macOS-AppleSilicon.dmg"),
    ("macOS: Intel", "Anim2D-{v}-macOS-Intel.dmg"),
)


def changelog_entry(version: str, changelog: str) -> str:
    """The text under '## <version>' up to the next '## ' heading."""
    match = re.search(rf"^## {re.escape(version)}\b[^\n]*\n(.*?)(?=^## |\Z)", changelog, re.MULTILINE | re.DOTALL)
    if not match or not match.group(1).strip():
        raise SystemExit(f"CHANGELOG.md has no entry for {version}: add a '## {version} (date)' section first.")
    return match.group(1).strip()


def notes(version: str, changelog: str) -> str:
    base = f"https://github.com/{REPOSITORY}/releases/download/v{version}"
    rows = "\n".join(f"| {label} | [{name.format(v=version)}]({base}/{name.format(v=version)}) |"
                     for label, name in DOWNLOADS)
    return (f"{changelog_entry(version, changelog)}\n\n"
            "## Downloads\n\n"
            "| For | File |\n| --- | --- |\n"
            f"{rows}\n\n"
            "macOS: open the disk image and drag Anim2D into Applications (macOS 14 or later). The builds are not "
            "code-signed yet: the first time, macOS says it can't check Anim2D; choose *Done*, then *System Settings "
            "→ Privacy & Security → Open Anyway*. On Windows, SmartScreen: *More info → Run anyway*. "
            "`SHA256SUMS.txt` has every file's checksum (`sha256sum -c SHA256SUMS.txt`).\n\n"
            f"[All changes](https://github.com/{REPOSITORY}/blob/v{version}/CHANGELOG.md)\n")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    version = sys.argv[1].removeprefix("v")
    sys.stdout.write(notes(version, (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
