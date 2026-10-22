"""Downloads test fixtures from Esoteric (all pinned and hash-checked):

  * spine-ts 3.8 core runtime and 3.8 example exports (spine-runtimes branch 3.8)
  * spine-ts 4.0.31 core runtime (npm @esotericsoftware/spine-core) and 4.0 example exports
    (spine-runtimes branch 4.0)
  * the images of spineboy (3.8), used by the template and editor tests

They are used only for local testing and are not committed or redistributed;
see the Spine Runtimes License (https://esotericsoftware.com/spine-runtimes-license).

    python tools/fetch_spine_runtime.py
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import urllib.request
from pathlib import Path

COMMIT_38 = "8b4844bd4b193ba9e54487ed397a777993cbad56"  # spine-runtimes branch 3.8
COMMIT_40 = "425ce416bb218b28caeec47b317aa57cd7140375"  # spine-runtimes branch 4.0
RAW = "https://raw.githubusercontent.com/EsotericSoftware/spine-runtimes"
CORE_40_TARBALL = "https://registry.npmjs.org/@esotericsoftware/spine-core/-/spine-core-4.0.31.tgz"
VENDOR = Path(__file__).resolve().parents[1] / "tests" / "runtime" / "vendor"
FILES = {
    "spine-core-3.8.js": (f"{RAW}/{COMMIT_38}/spine-ts/build/spine-core.js",
                          "f1e0a31b9906e4d4daf2733857d21381ddbbe75adec7f4d83e1cc9b2b070dfc1"),
    "spineboy/spineboy-ess.json": (f"{RAW}/{COMMIT_38}/examples/spineboy/export/spineboy-ess.json",
                                   "0f1cabaccdad880120f7e57ce9ffb55606227076aeea2246eab610c29d0bde6e"),
    # Production rig with 9 IK constraints and IK keys; used to check the IK solver (bones only).
    "raptor/raptor-pro.json": (f"{RAW}/{COMMIT_38}/examples/raptor/export/raptor-pro.json",
                               "9c422e5661a61bc119750ded0bf79074f72732547db77c038322d7d7a101dfb2"),
    "v40/spineboy-ess.json": (f"{RAW}/{COMMIT_40}/examples/spineboy/export/spineboy-ess.json",
                              "1e51201274a1c10742ed4456ce65d377184f124230c6f784c13426cef8a29a30"),
    "v40/spineboy-pro.json": (f"{RAW}/{COMMIT_40}/examples/spineboy/export/spineboy-pro.json",
                              "30cb336cccf08ee909f9a5fa2b8b55b36df41e218828c185ddbba92b239e50a1"),
    "v40/raptor-pro.json": (f"{RAW}/{COMMIT_40}/examples/raptor/export/raptor-pro.json",
                            "18998b3540a9241d78f1a40cd19bd7b6ec5edddbdd26dc300594d754d66d939c"),
    # Transform and path constraints, skins and skin bones/constraints (solver parity tests).
    "v40/tank-pro.json": (f"{RAW}/{COMMIT_40}/examples/tank/export/tank-pro.json",
                          "157079c2acb266c4db2fb31804e1da146127acc6cc2a44118d65415f3335151c"),
    "v40/vine-pro.json": (f"{RAW}/{COMMIT_40}/examples/vine/export/vine-pro.json",
                          "9a910c51e9daa84d823944acc1ceaba04dfb03b8cc8d60db1671c56fb1fb7717"),
    "v40/stretchyman-pro.json": (f"{RAW}/{COMMIT_40}/examples/stretchyman/export/stretchyman-pro.json",
                                 "e13cd723d3cc8c13944dbec0e9f3408cd801fcf4b26dca1156b67ecd905dc99d"),
    "v40/mix-and-match-pro.json": (f"{RAW}/{COMMIT_40}/examples/mix-and-match/export/mix-and-match-pro.json",
                                   "ef838c3b3dcb803caecc8b68429437b0e260878ab96251d8fde4b6c824ed18d8"),
    "v40/goblins-pro.json": (f"{RAW}/{COMMIT_40}/examples/goblins/export/goblins-pro.json",
                             "d82349307e74e2f4e30f2c4f367968b480666688a214febd75940fc48b1957fa"),
}
CORE_40 = ("v40/spine-core-4.0.js", "package/dist/iife/spine-core.js",
           "c703fc90e26fc58dd84635655de2b6643f25cc8e5ca9f65c18869274a395b93f")


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def _write(target: str, data: bytes, sha: str) -> None:
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha:
        raise SystemExit(f"{target}: hash mismatch: expected {sha}, got {digest}")
    out = VENDOR / target
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"wrote {out} ({len(data)} bytes, sha256 ok)")


def main() -> None:
    for target, (url, sha) in FILES.items():
        _write(target, _get(url), sha)

    target, member, sha = CORE_40
    with tarfile.open(fileobj=io.BytesIO(_get(CORE_40_TARBALL)), mode="r:gz") as archive:
        _write(target, archive.extractfile(member).read(), sha)

    # Images referenced by the spineboy export (immutable at the pinned commit).
    skeleton = json.loads((VENDOR / "spineboy" / "spineboy-ess.json").read_text(encoding="utf-8"))
    names = set()
    for skin in skeleton["skins"]:
        for entries in skin["attachments"].values():
            for key, att in entries.items():
                if att.get("type", "region") in ("region", "mesh"):
                    names.add(att.get("path", att.get("name", key)))
    for name in sorted(names):
        out = VENDOR / "spineboy" / "images" / f"{name}.png"
        if out.is_file():
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(_get(f"{RAW}/{COMMIT_38}/examples/spineboy/images/{name}.png"))
    print(f"spineboy images: {len(names)}")


if __name__ == "__main__":
    main()
