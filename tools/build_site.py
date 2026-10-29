"""Builds the website (GitHub Pages): the landing page in site/ and the docs in docs/ as HTML pages.

    python -m pip install -e ".[site]"
    python tools/build_site.py [--out build/site]

Output: <out>/index.html (from site/), <out>/assets/ (with the icon drawn by tools/make_icon.py),
<out>/docs/<name>.html for every docs/*.md, docs/install.html from README.md's Install section, and
docs/screenshots/. Links between docs point at the pages; links to other files in the repository point
at GitHub. Fails if a page links to a local file or image that doesn't exist.
.github/workflows/pages.yml builds and publishes this on every push to main that changes it.
"""

from __future__ import annotations

import argparse
import html
import posixpath
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
from anim2d.resources import HOMEPAGE, VERSION  # noqa: E402

SITE = ROOT / "site"
DOCS = ROOT / "docs"
BLOB = f"{HOMEPAGE}/blob/main"
# docs pages in sidebar order: (file stem, title)
PAGES = [("install", "Install"), ("editor", "Editor guide"), ("fx_presets", "FX presets"),
         ("preset_authoring", "Preset authoring"), ("template_round_trip", "Template round trip")]

PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} · Anim2D docs</title>
  <meta name="description" content="{description}">
  <link rel="icon" href="../assets/icon-64.png">
  <link rel="stylesheet" href="../assets/style.css">
</head>
<body>
  <header class="top">
    <a class="brand" href="../"><img src="../assets/icon-64.png" alt="" width="28" height="28"> Anim2D</a>
    <nav>
      <a href="../#features">Features</a>
      <a href="./">Docs</a>
      <a href="{homepage}">GitHub</a>
      <a class="button small" href="../#download">Download</a>
    </nav>
  </header>
  <div class="doc-layout">
    <aside class="doc-nav">
      <h4>Documentation</h4>
      <ul>{pages}</ul>
      {toc}
    </aside>
    <article class="prose">
{body}
      <p class="fine">Anim2D {version} · <a href="{source}">Edit this page on GitHub</a></p>
    </article>
  </div>
  <footer><p>Anim2D is free software under the <a href="{homepage}/blob/main/LICENSE">MIT License</a>.</p></footer>
</body>
</html>
"""


def install_markdown() -> str:
    """README.md's Install section, as its own page."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"^## Install\n(.*?)(?=^## )", readme, re.MULTILINE | re.DOTALL)
    if not match:
        raise SystemExit("README.md has no '## Install' section")
    return "# Install\n" + match.group(1).replace("\n### ", "\n## ")


def sources() -> dict[str, tuple[str, str, str]]:
    """stem -> (title, markdown, the source's path in the repository)."""
    out = {"install": ("Install", install_markdown(), "README.md")}
    for path in sorted(DOCS.glob("*.md")):
        title = dict(PAGES).get(path.stem, path.stem.replace("_", " ").capitalize())
        out[path.stem] = (title, path.read_text(encoding="utf-8"), f"docs/{path.name}")
    return out


def rewrite_links(body: str, stems: set[str], source: str) -> str:
    """docs/x.md -> x.html; other repository files -> GitHub; screenshots and web links unchanged."""
    base = Path(source).parent

    def fix(match: re.Match) -> str:
        attr, url = match.group(1), html.unescape(match.group(2))
        if re.match(r"^(?:[a-z]+:|#|//)", url):
            return match.group(0)
        path, _, anchor = url.partition("#")
        target = posixpath.normpath((base / path).as_posix())  # docs/../LICENSE -> LICENSE
        name = Path(target)
        if name.parent.as_posix() == "docs" and name.suffix == ".md" and name.stem in stems:
            new = f"{name.stem}.html"
        elif target.startswith("docs/screenshots/"):
            new = target[len("docs/"):]
        else:
            new = f"{BLOB}/{target}"
        return f'{attr}="{html.escape(new + ("#" + anchor if anchor else ""))}"'
    return re.sub(r'\b(href|src)="([^"]*)"', fix, body)


def render(stem: str, title: str, text: str, source: str, stems: set[str]) -> str:
    import markdown
    md = markdown.Markdown(extensions=["tables", "fenced_code", "toc", "sane_lists"],
                           extension_configs={"toc": {"toc_depth": "2-3"}})
    body = rewrite_links(md.convert(text), stems, source)
    first = re.search(r"<p>(.*?)</p>", body, re.DOTALL)
    description = re.sub(r"<[^>]+>", "", first.group(1)).strip()[:160] if first else title
    pages = "".join(f'<li><a href="{s}.html"{" class=current" if s == stem else ""}>{html.escape(t)}</a></li>'
                    for s, t in PAGES if s in stems)
    toc = f'<div class="toc"><h4>On this page</h4>{md.toc}</div>' if md.toc_tokens else ""
    return PAGE.format(title=html.escape(title), description=html.escape(description), homepage=HOMEPAGE,
                       pages=pages, toc=toc, body=body, version=VERSION, source=f"{BLOB}/{source}")


def docs_index(stems: set[str], titles: dict[str, str]) -> str:
    intro = ("Guides for Anim2D: installing it, every part of the editor, the FX presets, and making your own "
             "effects.\n\n" + "\n".join(f"* [{titles[s]}]({s}.md)" for s, _ in PAGES if s in stems))
    return render("index", "Documentation", "# Documentation\n\n" + intro, "docs/index.md", stems)


def broken_links(out: Path) -> list[str]:
    """Local href/src targets that don't exist in the built site."""
    problems = []
    for page in out.rglob("*.html"):
        for url in re.findall(r'\b(?:href|src)="([^"]*)"', page.read_text(encoding="utf-8")):
            url = html.unescape(url).partition("#")[0]
            if not url or re.match(r"^(?:[a-z]+:|//)", url):
                continue
            target = (page.parent / url)
            if url.endswith("/"):
                target = target / "index.html"
            if not target.exists():
                problems.append(f"{page.relative_to(out)}: {url}")
    return problems


def build(out: Path) -> Path:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(SITE, out)
    from make_icon import draw
    icon = draw()
    for size in (64, 256):
        icon.resize((size, size)).save(out / "assets" / f"icon-{size}.png")
    shutil.copytree(DOCS / "screenshots", out / "docs" / "screenshots")
    pages = sources()
    stems = set(pages)
    for stem, (title, text, source) in pages.items():
        (out / "docs" / f"{stem}.html").write_text(render(stem, title, text, source, stems), encoding="utf-8")
    (out / "docs" / "index.html").write_text(docs_index(stems, {s: t for s, (t, _, _) in pages.items()}),
                                             encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")  # serve files as they are
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Anim2D website.")
    parser.add_argument("--out", type=Path, default=ROOT / "build" / "site")
    out = build(parser.parse_args().out.resolve())
    problems = broken_links(out)
    for problem in problems:
        print(f"broken link: {problem}")
    print(f"built {out}" if not problems else f"{len(problems)} broken link(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
