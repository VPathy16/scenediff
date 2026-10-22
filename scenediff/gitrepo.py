"""Read committed snapshots without checkout, Git hooks, or project execution."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path, PurePosixPath

from .parser import ParseError, parse, unquote
from .semantic import compare_file, report

MAX_FILE = 8 * 1024 * 1024
MAX_REPORT = 64 * 1024 * 1024


class GitError(ValueError):
    pass


def git(repo: str | Path, *args: str) -> bytes:
    try:
        completed = subprocess.run(["git", "-C", str(repo), *args], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"Could not run Git: {exc}") from exc
    if completed.returncode:
        raise GitError(completed.stderr.decode("utf-8", "replace").strip() or "Git command failed")
    return completed.stdout


def commit(repo: str | Path, ref: str) -> str:
    if not ref or "\0" in ref:
        raise GitError("A valid Git branch, tag, or commit is required")
    return git(repo, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").decode().strip()


def tree(repo: str | Path, sha: str) -> dict[str, tuple[str, str]]:
    result = {}
    for entry in git(repo, "ls-tree", "-r", "-z", "--full-tree", sha).split(b"\0"):
        if not entry:
            continue
        metadata, filename = entry.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        result[filename.decode("utf-8", "surrogateescape")] = (kind, oid)
    return result


def decode(data: bytes) -> tuple[str, bool]:
    if b"\0" not in data:
        try:
            return data.decode("utf-8-sig"), False
        except UnicodeDecodeError:
            pass
    return f"Binary · {len(data):,} bytes · SHA-256 {hashlib.sha256(data).hexdigest()}", True


def compare_git(repo: str, left: str, right: str, base: str | None = None, path: str | None = None) -> dict:
    repo_path = Path(repo).expanduser().resolve()
    git(repo_path, "rev-parse", "--git-dir")
    a, b = commit(repo_path, left), commit(repo_path, right)
    if base:
        ancestor = commit(repo_path, base)
    else:
        ancestors = git(repo_path, "merge-base", "--all", a, b).decode().splitlines()
        if len(ancestors) != 1:
            raise GitError("There is no unique merge base. Choose an explicit base commit with --base.")
        ancestor = ancestors[0]
    trees = [tree(repo_path, sha) for sha in (ancestor, a, b)]
    paths = sorted(set().union(*(set(t) for t in trees)))
    if path:
        path = path.replace("\\", "/").removeprefix("./")
        paths = [p for p in paths if p == path or p.startswith(path.rstrip("/") + "/")]
        if not paths:
            raise GitError(f"No committed path matches {path!r}")
    paths = [p for p in paths if not trees[0].get(p) == trees[1].get(p) == trees[2].get(p)]
    cache: dict[str, bytes] = {}
    total = 0

    def read_entry(entry):
        nonlocal total
        if entry is None:
            return None
        kind, oid = entry
        if kind != "blob":
            return f"Git {kind} {oid}".encode()
        if oid not in cache:
            size = int(git(repo_path, "cat-file", "-s", oid))
            if size > MAX_FILE:
                # Fingerprint is sufficient to show a large asset changed.
                return f"\0Large asset · {size:,} bytes · Git object {oid}".encode()
            total += size
            if total > MAX_REPORT:
                raise GitError("Comparison exceeds 64 MiB of content. Restrict it using --path.")
            cache[oid] = git(repo_path, "cat-file", "blob", oid)
        return cache[oid]

    files = []
    for filename in paths:
        snapshots = [read_entry(t.get(filename)) for t in trees]
        decoded = [decode(s) if s is not None else (None, False) for s in snapshots]
        binary = any(d[1] for d in decoded)
        if binary:
            values = [None if s is None else f"{len(s):,} bytes · SHA-256 {hashlib.sha256(s).hexdigest()}" for s in snapshots]
        else:
            values = [d[0] for d in decoded]
        item = compare_file(filename, *values, binary=binary)
        if any(s is not None and s.startswith(b"\0Large asset") for s in snapshots):
            item["warnings"].append("At least one version exceeds 8 MiB; compared by Git object identity.")
        # Check committed res:// dependencies against each version of the project.
        if item["kind"] in {"scene", "resource"}:
            for label, source, snapshot_tree in zip(["Base", "A", "B"], values, trees):
                if source is None:
                    continue
                for section in parse(source).sections:
                    if section.kind != "ext_resource":
                        continue
                    dependency = unquote(section.attrs.get("path", ""))
                    if dependency.startswith("res://"):
                        # Projects may live below the repository root.
                        current = PurePosixPath(filename).parent
                        roots = [current, *current.parents]
                        project_root = next((str(r) for r in roots if str(r / "project.godot") in snapshot_tree), ".")
                        target = str(PurePosixPath(project_root) / dependency[6:])
                        if target not in snapshot_tree:
                            item["warnings"].append(f"{label}: referenced file {dependency} is absent from this commit.")
        files.append(item)

    # A changed .tres or script can affect otherwise unchanged scenes.
    affected = {f["path"]: f for f in files}
    scanned = 0
    for filename, entry in trees[2].items():
        if not filename.endswith((".tscn", ".tres")):
            continue
        scanned += 1
        if scanned > 500:
            break
        data = read_entry(entry)
        if data is None:
            continue
        try:
            doc = parse(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, ParseError):
            continue
        current = PurePosixPath(filename).parent
        project_root = next((r for r in [current, *current.parents] if str(r / "project.godot") in trees[2]), PurePosixPath("."))
        for section in doc.sections:
            if section.kind == "ext_resource":
                dependency = unquote(section.attrs.get("path", ""))
                target = str(project_root / dependency[6:]) if dependency.startswith("res://") else ""
                if target in affected:
                    affected[target].setdefault("referenced_by", []).append(filename)
    for item in files:
        if "referenced_by" in item:
            item["referenced_by"] = sorted(set(item["referenced_by"]))
    context = {"repository": repo_path.name, "commits": [ancestor, a, b], "source": "git",
               "dependency_scan": "up to 500 text scenes/resources in branch B; indirect and runtime references are not expanded"}
    return report(files, [base or f"merge base · {ancestor[:7]}", left, right], context=context)
