"""The website build (tools/build_site.py): every page builds, nothing links to a missing file, docs link to
each other as pages and to the rest of the repository on GitHub."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("markdown", reason="pip install -e .[site]")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_site  # noqa: E402


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    return build_site.build(tmp_path_factory.mktemp("site") / "out")


def test_every_page_is_built(site):
    assert (site / "index.html").is_file() and (site / ".nojekyll").is_file()
    assert (site / "assets" / "icon-64.png").is_file()
    for md in (ROOT / "docs").glob("*.md"):
        assert (site / "docs" / f"{md.stem}.html").is_file(), md.name
    assert (site / "docs" / "index.html").is_file() and (site / "docs" / "install.html").is_file()


def test_no_link_points_at_a_missing_file(site):
    assert build_site.broken_links(site) == []


def test_install_page_is_the_readme_section(site):
    page = (site / "docs" / "install.html").read_text(encoding="utf-8")
    assert "<h1" in page and "Install</h1>" in page
    assert "AppleSilicon.dmg" in page and "Anim2D-Setup-" in page


def test_links_between_docs_become_pages_and_others_go_to_github():
    body = ('<a href="editor.md#tree">a</a> <a href="../CONTRIBUTING.md">b</a> '
            '<img src="screenshots/x.png"> <a href="https://example.com">c</a> <a href="#here">d</a>')
    out = build_site.rewrite_links(body, {"editor"}, "docs/fx_presets.md")
    assert 'href="editor.html#tree"' in out
    assert f'href="{build_site.BLOB}/CONTRIBUTING.md"' in out
    assert 'src="screenshots/x.png"' in out and 'href="https://example.com"' in out and 'href="#here"' in out


def test_the_install_page_links_from_readme_paths():
    out = build_site.rewrite_links('<a href="docs/editor.md">g</a> <a href="LICENSE">l</a>', {"editor"}, "README.md")
    assert 'href="editor.html"' in out and f'href="{build_site.BLOB}/LICENSE"' in out
