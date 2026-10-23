"""Update checks against the project's GitHub releases, and the download and hand-off to the installer.

No Qt here: the editor runs these on a worker thread and shows the results (editor/update_ui.py).

    release = fetch_latest()                        # UpdateError when offline, rate limited, ...
    if is_newer(release.version, VERSION):
        asset = installer_asset(release)            # Anim2D-Setup-<version>.exe
        sums = fetch_checksums(release)             # from the release's SHA256SUMS.txt
        path = download(asset, sums[asset.name])    # verified against the checksum
        start_installer(path, install_dir(), os.getpid())   # then quit; it updates and relaunches

Only an installed copy updates itself. A portable copy downloads the new portable program next to
itself, and running from source only reports that a release exists.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator

from .resources import REPOSITORY, VERSION

API_LATEST = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
CHECK_INTERVAL = timedelta(hours=24)
MAX_JSON = 2 * 1024 * 1024  # a release description is far smaller; refuse anything bigger
CHUNK = 256 * 1024
INSTALLER = re.compile(r"^Anim2D-Setup-(?P<version>[\w.\-+]+)\.exe$", re.IGNORECASE)
PORTABLE = re.compile(r"^Anim2D-Portable-(?P<version>[\w.\-+]+)\.exe$", re.IGNORECASE)
CHECKSUMS = "SHA256SUMS.txt"
_VERSION = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(.*)$")


class UpdateError(Exception):
    """A check or download that did not work, with a message for the user."""


class UpdateCancelled(UpdateError):
    pass


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int


@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    title: str
    notes: str
    page: str
    published: str
    assets: dict[str, Asset] = field(default_factory=dict)


# --- versions ------------------------------------------------------------------------------------

def version_key(text: str) -> tuple:
    """Sort key for versions like 0.16.0, v1.2, 1.0.0-rc1. A pre-release sorts before its release."""
    match = _VERSION.match(text.strip())
    if not match:
        raise UpdateError(f"'{text}' is not a version number")
    major, minor, patch, rest = match.groups()
    rest = rest.lstrip("-.+")
    return int(major), int(minor), int(patch or 0), 0 if rest else 1, rest


def is_newer(candidate: str, current: str = VERSION) -> bool:
    try:
        return version_key(candidate) > version_key(current)
    except UpdateError:
        return False


# --- talking to GitHub ---------------------------------------------------------------------------

def _request(url: str, accept: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"Accept": accept, "User-Agent": f"Anim2D/{VERSION} (update check)"})


def _open(url: str, accept: str, timeout: float):
    """urlopen, with every network failure turned into an UpdateError that says what happened."""
    try:
        return urllib.request.urlopen(_request(url, accept), timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429) and (exc.headers.get("X-RateLimit-Remaining") == "0" or exc.code == 429):
            raise UpdateError("GitHub is limiting update checks from this network right now. "
                              "Try again in an hour.") from None
        if exc.code == 404:
            raise UpdateError("No releases have been published yet.") from None
        raise UpdateError(f"GitHub answered {exc.code} {exc.reason}.") from None
    except (socket.timeout, TimeoutError):
        raise UpdateError("GitHub did not answer in time. Check your connection and try again.") from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise UpdateError("GitHub did not answer in time. Check your connection and try again.") from None
        raise UpdateError(f"Can't reach GitHub ({reason}). Check your internet connection.") from None
    except OSError as exc:
        raise UpdateError(f"Can't reach GitHub ({exc}).") from None


def _read_limited(response, limit: int) -> bytes:
    try:
        data = response.read(limit + 1)
    except (socket.timeout, TimeoutError):
        raise UpdateError("GitHub stopped answering. Check your connection and try again.") from None
    except (OSError, http.client.HTTPException) as exc:
        raise UpdateError(f"The answer from GitHub was cut off ({exc}). Try again.") from None
    if len(data) > limit:
        raise UpdateError("GitHub sent an unexpectedly large answer.")
    return data


def parse_release(data: object) -> Release:
    if not isinstance(data, dict) or not isinstance(data.get("tag_name"), str):
        raise UpdateError("GitHub's answer didn't describe a release.")
    tag = data["tag_name"]
    version = tag[1:] if tag[:1] in "vV" else tag
    version_key(version)  # must be a version number
    assets = {}
    for raw in data.get("assets") or []:
        if not isinstance(raw, dict):
            continue
        name, url, size = raw.get("name"), raw.get("browser_download_url"), raw.get("size")
        if isinstance(name, str) and isinstance(url, str) and url.startswith("https://") and isinstance(size, int):
            assets[name] = Asset(name, url, size)
    return Release(version=version, tag=tag, title=str(data.get("name") or f"Anim2D {version}"),
                   notes=str(data.get("body") or ""), page=str(data.get("html_url") or ""),
                   published=str(data.get("published_at") or ""), assets=assets)


def fetch_latest(timeout: float = 15) -> Release:
    """The newest published release (drafts and pre-releases are never offered)."""
    with _open(API_LATEST, "application/vnd.github+json", timeout) as response:
        raw = _read_limited(response, MAX_JSON)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise UpdateError("GitHub's answer couldn't be read.") from None
    return parse_release(data)


def parse_checksums(text: str) -> dict[str, str]:
    """SHA256SUMS.txt: '<hash>  <name>' per line (CRLF and sha256sum's '*name' binary marker accepted)."""
    sums = {}
    for line in text.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            sums[parts[1].lstrip("*").strip()] = parts[0].lower()
    return sums


def fetch_checksums(release: Release, timeout: float = 15) -> dict[str, str]:
    asset = release.assets.get(CHECKSUMS)
    if asset is None:
        raise UpdateError(f"This release has no {CHECKSUMS}, so its download can't be verified.")
    with _open(asset.url, "application/octet-stream", timeout) as response:
        raw = _read_limited(response, 64 * 1024)
    return parse_checksums(raw.decode("utf-8", "replace"))


def installer_asset(release: Release) -> Asset | None:
    return next((a for n, a in release.assets.items() if INSTALLER.match(n)), None)


def portable_asset(release: Release) -> Asset | None:
    return next((a for n, a in release.assets.items() if PORTABLE.match(n)), None)


# --- downloading ---------------------------------------------------------------------------------

def cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Anim2D" / "updates"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _chunks(response) -> Iterator[bytes]:
    while True:
        chunk = response.read(CHUNK)
        if not chunk:
            return
        yield chunk


def download(asset: Asset, sha256: str, folder: Path | None = None,
             progress: Callable[[int, int], bool] = lambda done, total: True, timeout: float = 30) -> Path:
    """Download asset into folder (the update cache by default) and check its size and SHA-256.

    progress(done, total) is called as bytes arrive; return False to cancel (UpdateCancelled). A file
    already in the folder with the right checksum is reused. Nothing half-written is left behind."""
    folder = Path(folder) if folder else cache_dir()
    if Path(asset.name).name != asset.name or not asset.name.lower().endswith(".exe"):
        raise UpdateError(f"Unexpected download name '{asset.name}'.")
    sha256 = sha256.lower()
    target = folder / asset.name
    if os.path.normcase(str(target.resolve())) == os.path.normcase(str(Path(sys.executable).resolve())):
        folder = cache_dir()  # never write over the program that's running
        target = folder / asset.name
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.stat().st_size == asset.size and _sha256(target) == sha256:
            progress(asset.size, asset.size)
            return target
        free = shutil.disk_usage(folder).free
    except OSError as exc:
        raise UpdateError(f"Can't use the download folder {folder}: {exc}") from None
    if free < asset.size + 50 * 1024 * 1024:
        raise UpdateError(f"Not enough free space in {folder.anchor or folder}: the update needs "
                          f"{asset.size / 1048576:.0f} MB.")
    part = target.with_name(target.name + ".part")
    digest, done = hashlib.sha256(), 0
    try:
        with _open(asset.url, "application/octet-stream", timeout) as response, open(part, "wb") as out:
            for chunk in _chunks(response):
                done += len(chunk)
                if done > asset.size:
                    raise UpdateError("The download is bigger than GitHub said it would be.")
                digest.update(chunk)
                out.write(chunk)
                if not progress(done, asset.size):
                    raise UpdateCancelled("Download cancelled.")
        if done != asset.size:
            raise UpdateError("The download stopped before it finished. Try again.")
        if digest.hexdigest() != sha256:
            raise UpdateError("The download doesn't match the release's checksum, so it wasn't used. Try again.")
        os.replace(part, target)
    except (socket.timeout, TimeoutError):
        raise UpdateError("The download stalled. Check your connection and try again.") from None
    except http.client.HTTPException:  # IncompleteRead and friends: the connection dropped
        raise UpdateError("The download stopped before it finished. Try again.") from None
    except OSError as exc:
        raise UpdateError(f"Couldn't save the download: {exc}") from None
    finally:
        part.unlink(missing_ok=True)
    return target


def clean_cache(keep: Path | None = None) -> None:
    """Remove downloads other than keep (older updates, abandoned .part files)."""
    folder = cache_dir()
    if not folder.is_dir():
        return
    for item in folder.iterdir():
        if item.is_file() and (keep is None or item.resolve() != Path(keep).resolve()):
            try:
                item.unlink()
            except OSError:
                pass


# --- this copy and the hand-off ------------------------------------------------------------------

def install_kind() -> str:
    """'installed' (by the installer, so it can update itself), 'portable', 'source', or 'dev' (a local
    test build from tools/dev_build.py, which never updates itself)."""
    if not getattr(sys, "frozen", False):
        return "source"
    from .resources import is_dev_build
    if is_dev_build():
        return "dev"
    return "installed" if (install_dir() / "install.json").is_file() else "portable"


def install_dir() -> Path:
    return Path(sys.executable).resolve().parent


def check_due(last_check: str | None, now: datetime | None = None) -> bool:
    """Whether a scheduled check is due: never checked, a day has passed, or the clock went back."""
    now = now or datetime.now(timezone.utc)
    try:
        last = datetime.fromisoformat(str(last_check))
    except (TypeError, ValueError):
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return not (now - CHECK_INTERVAL < last <= now)


def start_installer(installer: Path, target: Path, wait_pid: int) -> None:
    """Run the downloaded installer in update mode: it waits for this process to exit, updates the
    installation in target (keeping the chosen apps and shortcuts) and starts Anim2D again. The
    environment clean-up matches the installer's start_program (installer/anim2d_setup.py)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI") and k != "_MEIPASS2"}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"  # this program is a PyInstaller build too
    try:
        subprocess.Popen([str(installer), "--update", "--target", str(target), "--wait-pid", str(wait_pid),
                          "--launch"], cwd=str(Path(installer).parent), env=env, close_fds=True,
                         creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    except OSError as exc:
        raise UpdateError(f"Couldn't start the installer: {exc}") from None
