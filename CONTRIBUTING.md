# Contributing to Anim2D

Thanks for helping. Anim2D is free and open source under the [MIT License](LICENSE), and contributions
of any size are welcome: bug reports, docs fixes, new FX presets, features.

## Ground rules

* Open an issue first for anything bigger than a small fix, so we can agree on the approach.
* Keep a pull request to one change. Behaviour fixes and pure refactors or renames go in separate PRs.
* Never commit files you don't have the right to share: example projects from other tools, client or
  game art, licensed fonts. Tests that need such files read their location from an environment variable
  and skip when it isn't set.
* Be kind. Disagreement about code is fine; disrespect for people isn't.

## Set up

Windows, Python 3.11+ and Git; Node.js 22 for the runtime-parity tests.

```bash
git clone https://github.com/turtlegameworks/Anim2D.git
cd Anim2D
python -m venv .venv
.venv\Scripts\python -m pip install -e .[dev]
.venv\Scripts\python tools\fetch_spine_runtime.py
```

## Before you open a pull request

```bash
.venv\Scripts\ruff check .
.venv\Scripts\python -m pytest
```

Both must pass. CI runs the same commands on Windows with Python 3.11 and 3.13, then builds and
self-tests the program. Add a test for every bug you fix: it should fail before your change and pass
after.

What reviewers look for:

* **User data is safe.** Nothing is overwritten without an explicit overwrite option, nothing is
  written outside the folder the user chose, and edits that say they can be undone really can be.
* **Readable errors.** Expected failures raise `EditorError` / `PresetError` with a message a user can
  act on, not a traceback.
* **Match the surrounding code.** Same naming, comment style and structure as the module you touch.

Every pull request is also reviewed automatically by [CodeRabbit](https://coderabbit.ai), configured in
`.coderabbit.yaml`. Treat its comments like any reviewer's: fix what's right, reply where it's wrong.
You can talk to it in the PR with `@coderabbitai`.

## Local test builds

To try a change in the real program before opening a pull request:

```bash
.venv\Scripts\python tools\dev_build.py
```

This builds `dev-builds\<date>-<time>-<commit>[-dirty]\Anim2D-Dev.exe` and opens it; add `--fx` to open
the FX Builder, or `--no-open` to only build. `--list` shows the test builds and `--clean` deletes them.
The newest three are kept, and a post-commit hook that the script adds to your clone (it isn't part
of the repository) deletes them all when you commit. Nothing builds automatically.

A test build is separate from an installed Anim2D and can run at the same time. It's a different
program (`Anim2D-Dev.exe`) and installs nothing. It keeps its own settings and its own FX templates
and folders (`Documents\Anim2D Dev`, `%LOCALAPPDATA%\Anim2D-Dev`), and never checks for or installs updates. It shows **DEV BUILD**
at the bottom right with the commit it was built from, **[DEV]** in the title, and a DEV tag on its icon.

## Commit messages

A short summary line in the imperative ("Fix atlas page overwrite"), a blank line, then what changed and
why if it isn't obvious.

## Releases (maintainers)

1. Bump `__version__` in `src/anim2d/__init__.py` and add the release to `CHANGELOG.md`.
2. Merge to `main`, then tag it: `git tag v0.16.0` and `git push origin v0.16.0`.
3. The **Release** workflow tests, builds, self-tests and publishes the installer, the portable exe and
   `SHA256SUMS.txt`. The tag must match the version or the workflow stops.
