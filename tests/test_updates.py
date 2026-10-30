"""Update checks and downloads (anim2d.updates) against a fake GitHub: no network is used."""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import socket
import urllib.error
from datetime import datetime, timedelta, timezone
from email.message import Message

import pytest

from anim2d import updates
from anim2d.updates import Asset, UpdateCancelled, UpdateError

PAYLOAD = b"MZ" + b"x" * 300_000  # a stand-in installer
SHA = hashlib.sha256(PAYLOAD).hexdigest()


def _release_json(tag="v0.17.0", **extra) -> dict:
    data = {
        "tag_name": tag, "name": f"Anim2D {tag[1:]}", "body": "## What's new\n* Settings tabs",
        "html_url": f"https://github.com/turtlegameworks/Anim2D/releases/tag/{tag}",
        "published_at": "2026-11-01T10:00:00Z",
        "assets": [
            {"name": f"Anim2D-Setup-{tag[1:]}.exe", "size": len(PAYLOAD),
             "browser_download_url": "https://example.test/setup.exe"},
            {"name": f"Anim2D-Portable-{tag[1:]}.exe", "size": len(PAYLOAD),
             "browser_download_url": "https://example.test/portable.exe"},
            {"name": "SHA256SUMS.txt", "size": 200, "browser_download_url": "https://example.test/sums"},
        ],
    }
    data.update(extra)
    return data


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _http_error(code: int, **headers) -> urllib.error.HTTPError:
    msg = Message()
    for k, v in headers.items():
        msg[k] = v
    return urllib.error.HTTPError("https://api.github.com", code, "error", msg, None)


@pytest.fixture
def github(monkeypatch):
    """Route urlopen to a dict of url -> bytes or exception; records requests."""
    routes: dict[str, object] = {}
    seen = []

    def urlopen(request, timeout=None, context=None):
        seen.append(request)
        answer = routes[request.full_url]
        if isinstance(answer, BaseException):
            raise answer
        return FakeResponse(answer)
    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    routes[updates.API_LATEST] = json.dumps(_release_json()).encode()
    routes["https://example.test/sums"] = (f"{SHA}  Anim2D-Setup-0.17.0.exe\r\n"
                                           f"{SHA}  Anim2D-Portable-0.17.0.exe\r\n").encode()
    routes["https://example.test/setup.exe"] = PAYLOAD
    routes["https://example.test/portable.exe"] = PAYLOAD
    return routes, seen


# --- versions

@pytest.mark.parametrize("newer, older", [
    ("0.17.0", "0.16.0"), ("0.16.1", "0.16.0"), ("1.0", "0.99.9"), ("v0.10.0", "0.9.0"), ("0.17.0", "0.17.0-rc1"),
    ("0.17.0-rc2", "0.17.0-rc1"), ("0.17.0-rc1", "0.16.9"),
])
def test_version_order(newer, older):
    assert updates.is_newer(newer, older) and not updates.is_newer(older, newer)


def test_same_or_unreadable_versions_are_not_updates():
    assert not updates.is_newer("0.16.0", "0.16.0")
    assert not updates.is_newer("nightly", "0.16.0")
    with pytest.raises(UpdateError):
        updates.version_key("latest")


# --- checking

def test_fetch_latest_reads_the_release(github):
    _, seen = github
    release = updates.fetch_latest()
    assert release.version == "0.17.0" and release.tag == "v0.17.0" and "Settings tabs" in release.notes
    assert updates.installer_asset(release).name == "Anim2D-Setup-0.17.0.exe"
    assert updates.portable_asset(release).name == "Anim2D-Portable-0.17.0.exe"
    assert seen[0].get_header("User-agent").startswith("Anim2D/")


def test_a_plain_anim2d_exe_is_not_taken_for_the_portable_download():
    """It could be the name of the running program; only Anim2D-Portable-<version>.exe is used."""
    data = _release_json("v0.16.0")
    data["assets"][1]["name"] = "Anim2D.exe"
    assert updates.portable_asset(updates.parse_release(data)) is None


def test_download_never_overwrites_the_running_program(github, tmp_path, monkeypatch):
    monkeypatch.setattr(updates.sys, "executable", str(tmp_path / "Anim2D-Portable-0.17.0.exe"))
    monkeypatch.setattr(updates, "cache_dir", lambda: tmp_path / "cache")
    asset = Asset("Anim2D-Portable-0.17.0.exe", "https://example.test/portable.exe", len(PAYLOAD))
    path = updates.download(asset, SHA, tmp_path)
    assert path == tmp_path / "cache" / asset.name and not (tmp_path / asset.name).exists()


class _BrokenResponse(FakeResponse):
    def __init__(self, error):
        super().__init__(b"")
        self.error = error

    def read(self, *args):
        raise self.error


@pytest.mark.parametrize("error, message", [
    (ConnectionResetError("reset by peer"), "cut off"),
    (http.client.IncompleteRead(b"partial"), "cut off"),
    (TimeoutError(), "stopped answering"),
], ids=["reset", "incomplete", "timeout"])
def test_failures_while_reading_the_answer(github, monkeypatch, error, message):
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda request, timeout=None, context=None: _BrokenResponse(error))
    with pytest.raises(UpdateError, match=message):
        updates.fetch_latest()


def test_connection_dropped_during_download(github, tmp_path, monkeypatch):
    monkeypatch.setattr(updates.urllib.request, "urlopen",
                        lambda request, timeout=None, context=None: _BrokenResponse(http.client.IncompleteRead(b"x")))
    asset = Asset("Anim2D-Setup-0.17.0.exe", "https://example.test/setup.exe", len(PAYLOAD))
    with pytest.raises(UpdateError, match="stopped before it finished"):
        updates.download(asset, SHA, tmp_path)
    assert not list(tmp_path.iterdir())


def test_assets_that_are_not_https_are_ignored():
    data = _release_json()
    data["assets"][0]["browser_download_url"] = "http://example.test/setup.exe"
    assert updates.installer_asset(updates.parse_release(data)) is None


@pytest.mark.parametrize("answer, message", [
    (_http_error(404), "No releases"),
    (_http_error(403, **{"X-RateLimit-Remaining": "0"}), "limiting"),
    (_http_error(429), "limiting"),
    (_http_error(500), "500"),
    (urllib.error.URLError(OSError("getaddrinfo failed")), "internet connection"),
    (urllib.error.URLError(socket.timeout()), "in time"),
    (TimeoutError(), "in time"),
    (b"not json", "couldn't be read"),
    (json.dumps({"message": "Not Found"}).encode(), "didn't describe a release"),
    (json.dumps(_release_json("nightly")).encode(), "not a version"),
    (b"[" * (updates.MAX_JSON + 10), "unexpectedly large"),
], ids=["404", "rate-limit", "429", "500", "offline", "url-timeout", "timeout", "not-json", "not-a-release",
        "bad-tag", "too-large"])
def test_check_failures_are_readable(github, answer, message):
    routes, _ = github
    routes[updates.API_LATEST] = answer
    with pytest.raises(UpdateError, match=message):
        updates.fetch_latest()


def test_checksums_accept_crlf_and_binary_marker():
    text = f"{SHA}  a.exe\r\n{SHA.upper()} *b.exe\n\nnot a line\n"
    assert updates.parse_checksums(text) == {"a.exe": SHA, "b.exe": SHA}


def test_release_without_checksums_cannot_be_verified(github):
    routes, _ = github
    data = _release_json()
    data["assets"] = data["assets"][:2]
    routes[updates.API_LATEST] = json.dumps(data).encode()
    with pytest.raises(UpdateError, match="can't be verified"):
        updates.fetch_checksums(updates.fetch_latest())


@pytest.mark.parametrize("last, due", [
    (None, True), ("garbage", True),
    (lambda now: (now - timedelta(hours=1)).isoformat(), False),
    (lambda now: (now - timedelta(hours=25)).isoformat(), True),
    (lambda now: (now + timedelta(days=3)).isoformat(), True),  # the clock was set back
    (lambda now: (now - timedelta(hours=1)).replace(tzinfo=None).isoformat(), False),  # no time zone stored
])
def test_check_due(last, due):
    now = datetime.now(timezone.utc)
    assert updates.check_due(last(now) if callable(last) else last, now) is due


def test_running_from_source_is_not_an_installation():
    assert updates.install_kind() == "source"


# --- downloading

def test_download_verifies_and_reuses(github, tmp_path):
    release = updates.fetch_latest()
    asset = updates.installer_asset(release)
    sums = updates.fetch_checksums(release)
    calls = []
    path = updates.download(asset, sums[asset.name], tmp_path, lambda d, t: calls.append((d, t)) or True)
    assert path.read_bytes() == PAYLOAD and calls[-1] == (len(PAYLOAD), len(PAYLOAD))
    routes, seen = github
    routes["https://example.test/setup.exe"] = RuntimeError("must not download again")
    assert updates.download(asset, sums[asset.name], tmp_path) == path  # the verified copy is reused
    assert not list(tmp_path.glob("*.part"))


def test_download_rejects_a_wrong_checksum(github, tmp_path):
    routes, _ = github
    routes["https://example.test/setup.exe"] = b"MZ" + b"y" * (len(PAYLOAD) - 2)
    asset = Asset("Anim2D-Setup-0.17.0.exe", "https://example.test/setup.exe", len(PAYLOAD))
    with pytest.raises(UpdateError, match="checksum"):
        updates.download(asset, SHA, tmp_path)
    assert not list(tmp_path.iterdir()), "nothing is left behind"


@pytest.mark.parametrize("body, message", [(PAYLOAD[:1000], "stopped before"), (PAYLOAD + b"extra", "bigger")],
                         ids=["short", "long"])
def test_download_of_the_wrong_size(github, tmp_path, body, message):
    routes, _ = github
    routes["https://example.test/setup.exe"] = body
    asset = Asset("Anim2D-Setup-0.17.0.exe", "https://example.test/setup.exe", len(PAYLOAD))
    with pytest.raises(UpdateError, match=message):
        updates.download(asset, SHA, tmp_path)
    assert not list(tmp_path.iterdir())


def test_cancelled_download_leaves_nothing(github, tmp_path):
    asset = Asset("Anim2D-Setup-0.17.0.exe", "https://example.test/setup.exe", len(PAYLOAD))
    with pytest.raises(UpdateCancelled):
        updates.download(asset, SHA, tmp_path, lambda done, total: done < updates.CHUNK)
    assert not list(tmp_path.iterdir())


def test_download_refuses_odd_file_names(tmp_path):
    for name in ("..\\evil.exe", "notes.txt", "sub/Anim2D-Setup.exe", "..\\evil.dmg", ".hidden.dmg", "Anim2D.app",
                 "Anim2D.pkg"):
        with pytest.raises(UpdateError, match="Unexpected"):
            updates.download(Asset(name, "https://example.test/x", 10), SHA, tmp_path)


def test_download_needs_free_space(github, tmp_path, monkeypatch):
    monkeypatch.setattr(updates.shutil, "disk_usage", lambda p: type("U", (), {"free": 1000})())
    asset = Asset("Anim2D-Setup-0.17.0.exe", "https://example.test/setup.exe", len(PAYLOAD))
    with pytest.raises(UpdateError, match="Not enough free space"):
        updates.download(asset, SHA, tmp_path)


def test_clean_cache_keeps_only_the_current_download(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "cache_dir", lambda: tmp_path)
    keep, old, part = tmp_path / "Anim2D-Setup-0.17.0.exe", tmp_path / "Anim2D-Setup-0.16.5.exe", tmp_path / "x.part"
    for f in (keep, old, part):
        f.write_bytes(b"x")
    updates.clean_cache(keep=keep)
    assert [p.name for p in tmp_path.iterdir()] == [keep.name]


# --- macOS: disk images per processor

def _mac_release(*arches: str):
    data = _release_json("v0.18.0")
    data["assets"] += [{"name": f"Anim2D-0.18.0-macOS-{arch}.dmg", "size": len(PAYLOAD),
                        "browser_download_url": f"https://example.test/{arch}.dmg"} for arch in arches]
    return updates.parse_release(data)


@pytest.mark.parametrize("arch", ["AppleSilicon", "Intel"])
def test_mac_asset_is_the_disk_image_for_the_processor(arch):
    release = _mac_release("AppleSilicon", "Intel")
    assert updates.mac_asset(release, arch).name == f"Anim2D-0.18.0-macOS-{arch}.dmg"
    assert updates.installer_asset(release).name.endswith(".exe"), "Windows still gets its installer"


def test_mac_asset_missing_for_this_processor():
    assert updates.mac_asset(_mac_release("AppleSilicon"), "Intel") is None
    assert updates.mac_asset(_mac_release(), "AppleSilicon") is None


@pytest.mark.parametrize("machine, sysctl, arch", [
    ("arm64", None, "AppleSilicon"),
    ("x86_64", "1\n", "AppleSilicon"),  # an Intel build under Rosetta on an Apple silicon Mac
    ("x86_64", "0\n", "Intel"),
    ("x86_64", "", "Intel"),  # an Intel Mac has no hw.optional.arm64
])
def test_mac_arch(monkeypatch, machine, sysctl, arch):
    import platform
    monkeypatch.setattr(platform, "machine", lambda: machine)
    monkeypatch.setattr(updates.subprocess, "run",
                        lambda *a, **k: type("Done", (), {"stdout": sysctl if sysctl is not None else ""})())
    assert updates.mac_arch() == arch


def test_mac_arch_without_sysctl(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")

    def missing(*a, **k):
        raise FileNotFoundError("sysctl")
    monkeypatch.setattr(updates.subprocess, "run", missing)
    assert updates.mac_arch() == "Intel"


def test_the_built_mac_app_is_its_own_kind(monkeypatch):
    monkeypatch.setattr(updates.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updates.sys, "platform", "darwin")
    assert updates.install_kind() == "mac"


def test_a_disk_image_downloads_and_verifies(github, tmp_path):
    routes, _ = github
    routes["https://example.test/AppleSilicon.dmg"] = PAYLOAD
    asset = updates.mac_asset(_mac_release("AppleSilicon"), "AppleSilicon")
    path = updates.download(asset, SHA, tmp_path)
    assert path == tmp_path / "Anim2D-0.18.0-macOS-AppleSilicon.dmg" and path.read_bytes() == PAYLOAD


def test_update_checks_trust_the_systems_certificates(monkeypatch):
    # The macOS app bundles no CA file, so Python's default context verified nothing (CERTIFICATE_VERIFY_FAILED).
    assert type(updates.tls_context()).__module__.startswith("truststore")
    contexts = []

    def urlopen(request, timeout=None, context=None):
        contexts.append(context)
        return FakeResponse(json.dumps(_release_json()).encode())
    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    updates.fetch_latest()
    assert contexts == [updates.tls_context()]
