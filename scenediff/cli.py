from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from pathlib import Path

from . import __version__
from .gitrepo import MAX_FILE, compare_git, decode
from .report import demo, render
from .semantic import compare_file, report
from .server import create_server


def parser():
    cli = argparse.ArgumentParser(prog="scenediff", description="Scene-aware comparison for Godot. Everything stays on your computer.")
    cli.add_argument("--version", action="version", version=__version__)
    commands = cli.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="Open the local visual app")
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--no-open", action="store_true")
    serve.add_argument("--desktop", action="store_true", help="Use an optional native window (pip install '.[desktop]')")
    files = commands.add_parser("files", help="Compare two files, with an optional common base")
    files.add_argument("left", type=Path)
    files.add_argument("right", type=Path)
    files.add_argument("--base", type=Path)
    git = commands.add_parser("git", help="Compare committed Git branches using their merge base")
    git.add_argument("left")
    git.add_argument("right")
    git.add_argument("--repo", default=".")
    git.add_argument("--base")
    git.add_argument("--path", help="Limit comparison to a committed file or directory")
    example = commands.add_parser("demo", help="Create an example report")
    for command in (files, git, example):
        command.add_argument("-o", "--output", type=Path, help="HTML report destination; defaults to a temporary local report")
        command.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of opening a report")
        command.add_argument("--no-open", action="store_true")
        command.add_argument("--check", action="store_true", help="Exit 1 if conflicts exist (errors exit 2)")
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command in {None, "serve"}:
            server = create_server(getattr(args, "port", 0))
            url = f"http://127.0.0.1:{server.server_port}/"
            print(f"SceneDiff is running at {url}\nPress Ctrl+C to stop.", flush=True)
            if getattr(args, "desktop", False):
                try:
                    import webview
                except ImportError:
                    server.server_close()
                    raise ValueError("Native window support requires: pip install 'godot-scenediff[desktop]'")
                threading.Thread(target=server.serve_forever, daemon=True).start()
                try:
                    webview.create_window("SceneDiff", url, width=1440, height=960, min_size=(900, 650))
                    webview.start()
                finally:
                    server.shutdown()
                    server.server_close()
            else:
                if not getattr(args, "no_open", False):
                    webbrowser.open(url)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
                finally:
                    server.server_close()
            return 0
        if args.command == "demo":
            payload = demo()
        elif args.command == "git":
            payload = compare_git(args.repo, args.left, args.right, args.base, args.path)
        else:
            contents = []
            binary = False
            for path in (args.base, args.left, args.right):
                if path is None:
                    contents.append(None)
                    continue
                if path.stat().st_size > MAX_FILE:
                    raise ValueError(f"{path.name} exceeds the 8 MiB file limit")
                value, is_binary = decode(path.read_bytes())
                binary |= is_binary
                contents.append(value)
            payload = report([compare_file(args.right.name, *contents, three_way=args.base is not None, binary=binary)],
                             [str(args.base or "No base"), str(args.left), str(args.right)], three_way=args.base is not None,
                             context={"source": "files"})
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=True))
        else:
            if args.output:
                output = args.output.expanduser().resolve()
                # Reports must never replace either source scene or an existing report.
                with output.open("x", encoding="utf-8") as file:
                    file.write(render(payload))
            else:
                import tempfile
                with tempfile.NamedTemporaryFile(prefix="scenediff-", suffix=".html", delete=False, mode="w", encoding="utf-8") as file:
                    file.write(render(payload))
                    output = Path(file.name)
            print(f"Report: {output}")
            if not args.no_open:
                webbrowser.open(output.as_uri())
        return 1 if args.check and payload["summary"]["conflicts"] else 0
    except (OSError, ValueError) as exc:
        print(f"scenediff: {exc}", file=sys.stderr)
        return 2
