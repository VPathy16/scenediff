# SceneDiff

**Understand what changed in a Godot scene.** SceneDiff is a local visual diff and review tool for `.tscn`, `.tres`, scripts, and other committed project files.

Compare two files or two Git branches. Explore the scene tree, inspect properties side by side, see shared material references, and record A/B/custom decisions for conflicts.

**Version 0.1 is a working review tool.** It exports portable HTML reports and JSON review plans. It does **not** write merged scene files, execute Godot, or render game previews. A review decision is not an applied or validated merge.

## Start in one command

Requires **Python 3.10+**. Git branch comparisons also require **Git 2.20+**.

```bash
git clone https://github.com/VPathy16/scenediff.git
cd scenediff
python -m scenediff
```

Use `python3` if that is your system's Python command. The app opens in your default browser and listens only on a random port at `127.0.0.1`. It starts with an example project; click **New comparison** to select local files or enter your project path and branches. Press Ctrl+C in the terminal to stop it.

The core app has **no third-party Python dependencies**, no account requirement, and no remote services or CDN assets. Project content stays on your computer.

Alternatively, install the command in a virtual environment:

```bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell instead:
# .venv\Scripts\Activate.ps1
python -m pip install .
scenediff
```

### Optional desktop window

```bash
python -m pip install '.[desktop]'
scenediff serve --desktop
```

This uses pywebview and your platform's native webview. Linux may require GTK/Qt and WebKit system packages; Windows may require WebView2. The browser interface works without these optional dependencies. Prebuilt desktop installers are not included in this release.

## Compare two branches

Both people commit their work on separate branches. In SceneDiff's **Git branches** form, enter the local repository path and both branch names. SceneDiff detects their common ancestor with `git merge-base`.

```bash
scenediff git alice/layout bob/lighting --repo /path/to/godot-project
```

Limit a large project comparison or specify the base explicitly:

```bash
scenediff git alice/layout bob/lighting --repo /path/to/project --path scenes
scenediff git alice/layout bob/lighting --repo /path/to/project --base main
```

No branch is checked out, no files are modified, and no fetch is performed. Fetch remote branches yourself before comparing `origin/alice/layout` and `origin/bob/lighting`. Uncommitted changes are not part of a branch comparison.

## Compare local files

```bash
scenediff files alice.tscn bob.tscn --base original.tscn
scenediff files before.tres after.tres
```

Without a base, this is a two-way comparison: differences are not labeled conflicts. Use **Local files** in the app to choose files graphically.

## Review changes

- **Scene outline:** select nodes, resource definitions, child order, or signal connections.
- **Properties:** see Base / A / B values. `Absent / no override` is distinct from the literal Godot value `null`.
- **New nodes:** additions show their full properties; child nodes appear separately in the tree.
- **Materials:** embedded resources and external text `.tres` files use property comparison, with reference sites shown where available.
- **Scripts:** new/modified `.gd`, `.cs`, and shader files use a normal text diff and full source views.
- **Review controls:** choose A, B, or a custom property expression. `A` and `B` keyboard shortcuts apply to the selected property.
- **Next unresolved conflict:** navigates property, structural, and whole-file review decisions.
- **Save report:** downloads a self-contained HTML report, including your decisions. It works offline and contains the compared source text.
- **Export review plan:** downloads JSON containing the report ID, commit IDs for Git comparisons, decisions, and unresolved items. The plan does not apply a merge. Custom values are recorded verbatim and are not syntax-validated.

The conflict summary counts affected **entities/files**; the progress panel counts individual **decisions**. Structural conflicts require both a structural decision and decisions for any conflicting properties. Recorded choices can still be mutually inconsistent; validate them when implementing a merge.

## Git difftool integration

After installing `scenediff` into an environment on your PATH, run these commands from your Godot repository (POSIX shell or Git Bash):

```bash
git config diff.tool scenediff
git config difftool.scenediff.cmd 'scenediff files "$LOCAL" "$REMOTE"'
git config difftool.prompt false
git difftool alice/layout bob/lighting -- scenes/level.tscn
```

Reports capture the files before Git removes its temporary copies. `git difftool` supplies two versions; use `scenediff git` for common-base conflict detection and project-wide dependencies.

## CLI reports and automation

```bash
scenediff demo -o demo.html --no-open
scenediff git alice/layout bob/lighting --repo /path/to/project -o review.html --no-open
scenediff git alice/layout bob/lighting --repo /path/to/project --json > review.json
scenediff git alice/layout bob/lighting --repo /path/to/project --json --check
```

`--check` returns `1` for detected conflicts, `0` otherwise, and `2` for command errors. A zero exit code does not establish runtime compatibility or the absence of warnings. Output files are created exclusively; existing files are never overwritten. Without `-o`, the CLI creates a temporary HTML report that you can remove after review.

## Comparison rules and limits

| Case | Version 0.1 behavior |
| --- | --- |
| Different properties changed on each branch | Marked independent edits |
| Same property changed differently | Property conflict |
| Same change on both branches | Both agree |
| External resource IDs regenerated | Match by path, then UID; references are normalized |
| Embedded resource IDs regenerated | Match stable IDs or unambiguous reference-slot sets; uncertain matches stay separate |
| Material made unique | Separate definitions stay separate; identical contents never collapse resource sharing |
| Node deleted while its descendant is added/edited | Structural conflict |
| Different nodes added at the same path | Structural add/add conflict |
| Reordered children | Explicit child-order entity; conflicting orders require review |
| Renamed/moved nodes | Add/remove entries, with a possible-move note; no speculative identity rewrite |
| Inherited/instanced scenes | Compare serialized overrides and note that inherited defaults are not expanded |
| Missing external files | Git mode checks direct `res://` declarations against each committed tree |
| Changed external material/script | Reference index scans up to 500 `.tscn`/`.tres` files in branch B; not a complete runtime dependency graph |
| Binary `.res`, textures, other assets | Fingerprint comparison; no content preview |
| Malformed/unsupported section syntax | Visible warning and complete raw text fallback |
| Unknown section kind | Retained for review with a warning |
| Two branches edit a script | Conservative whole-file conflict; no language-aware script merge |

Godot Variant expressions are tokenized, never evaluated. Whitespace outside strings and resource ID churn can be ignored; dictionary/array order, numeric spelling, and serialized property differences remain visible. No scripts, shaders, editor plugins, or project code are executed.

Different Godot versions can produce different serialized defaults and formats. Compare work produced by the same Godot version where possible. NodePath targets, animation tracks, shader uniform compatibility, script export renames, runtime references, and behavior interactions are **not fully validated** in this release. Godot-assisted validation, visual scene previews, and an actual merge writer are future work.

Git mode reads up to 8 MiB per text blob; larger assets are shown by object identity. Total loaded content is limited to 64 MiB; use `--path` for larger comparisons. Local file comparisons accept up to 8 MiB per file.

## Development

```bash
python -m unittest discover -s tests -v
node --check scenediff/web/app.js
```

Optional browser integration tests:

```bash
python -m pip install playwright
python -m playwright install chromium
python tests/browser_smoke.py
```

The test suite covers nested Variant expressions, escaped strings, resource ID churn, material sharing, additions, property/structural conflicts, child order, malformed input, Git merge bases, dependency checks, source-safe report export, and the local API. GitHub Actions runs tests on Linux and Windows, plus a Chromium UI check on Linux.

Architecture: `parser.py` reads sections; `semantic.py` builds comparable entities; `gitrepo.py` reads commit snapshots; `server.py` serves a local UI; `report.py` embeds portable reports; `web/` provides the interface without a build step.

MIT licensed. Independent community tool; not affiliated with the Godot project.
