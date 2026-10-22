# SceneDiff

**Understand what changed in a Godot scene.** SceneDiff is a local visual diff and review tool for `.tscn`, `.tres`, scripts, and other committed project files.

Compare two files or two Git branches. Explore the scene tree, inspect properties side by side, see shared material references, and record A/B/custom decisions for conflicts.

**Version 0.2 adds selective scene merging.** Copy properties, nodes and their children, resources, signals, and child order between A and B, then download the merged `.tscn` or `.tres` file. It also exports portable HTML reports and JSON review plans. Review-plan choices remain separate from Apply operations. Godot execution and game previews are not included.

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
python -m pip install ".[desktop]"
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

## Apply changes and download a merged scene

1. Run `python -m scenediff` and open the local browser page.
2. Choose **New comparison → Local files**, select versions A and B of the same `.tscn` or `.tres` file, and click **Compare changes**. A common base is optional. Merging also works on scene files in a Git comparison.
3. Select a node or resource, then a property. Under **Selected property**, click **Apply A → B** to copy its A value into B, or **Apply B → A** for the reverse direction. Other target properties are retained. Copying an absent value removes that override.
4. To add, remove, or replace an entire node, use **Whole node and children**. This copies the source subtree and its associated signal connections. Target-only descendants in that subtree are removed. If the source node is absent, the target subtree is removed.
5. Repeat for other changes. **Undo last apply** restores the previous preview (up to 20 operations).
6. Click **Download merged B** or **Download merged A**. The selected file downloads as, for example, `level.merged-B.tscn`. Place it in the target Godot project, check its dependencies and behavior in Godot, then use it to replace the intended scene when ready. The download keeps the target file UID, so it is intended as a replacement for that scene rather than a second permanent scene with the same UID.

Apply updates the in-memory preview. It does not overwrite source files, change Git branches, or apply every recorded review-plan decision. Download each modified file before closing the app. A saved HTML report retains merged previews for download, but further Apply operations require the live app. Undo history is only kept in the live session.

Embedded resource dependencies are copied recursively. Conflicting resource IDs are remapped, and matching unchanged definitions are reused. Copying a resource-valued property can create a separate resource when the source definition differs; other target users keep their existing definition. Applying changes directly to a resource definition affects all its users in the target.

External resource declarations (scripts, textures, other scenes) are copied with their references; the asset files themselves are not bundled. They must exist at the declared `res://` paths in the target project. Whole-file additions, script/text merges, custom-value merging, and automatic application of review plans are not part of this feature.

The writer checks serialized structure, resource references, and explicit node and signal paths. It rejects missing parents, deletion of referenced resources, cyclic embedded resources, unknown section types, and mixed Godot formats. Child order can be applied after both sides contain the same children. Instanced/inherited defaults, NodePath properties, animations, and runtime behavior still require validation in Godot. Untouched sections retain their text; changed sections are rewritten and may lose comments or formatting.

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

| Case | Version 0.2 comparison behavior |
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

Different Godot versions can produce different serialized defaults and formats. Compare work produced by the same Godot version where possible. NodePath targets, animation tracks, shader uniform compatibility, script export renames, runtime references, and behavior interactions are **not fully validated** in this release. Godot-assisted validation and visual scene previews are future work; selective serialized merging is available through the live app.

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

The test suite covers nested Variant expressions, escaped strings, resource ID churn, material sharing, additions, property/structural conflicts, child order, malformed input, Git merge bases, dependency checks, source-safe report export, the local API, selective merging, resource remapping, and merged downloads. GitHub Actions runs tests on Linux and Windows, plus a Chromium UI check on Linux.

Architecture: `merge.py` copies selected changes and serializes merged downloads; `parser.py` reads sections; `semantic.py` builds comparable entities; `gitrepo.py` reads commit snapshots; `server.py` serves a local UI; `report.py` embeds portable reports; `web/` provides the interface without a build step.

MIT licensed. Independent community tool; not affiliated with the Godot project.

