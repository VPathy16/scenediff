"""Self-contained HTML reports: no CDN, remote fonts, or project uploads."""

from __future__ import annotations

import json
from pathlib import Path

WEB = Path(__file__).parent / "web"


def render(payload: dict | None = None, *, token="", live=False) -> str:
    boot = json.dumps({"report": payload, "token": token, "live": live}, ensure_ascii=True)
    # Prevent scene text from terminating the inert JSON script element.
    boot = boot.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return (WEB / "index.html").read_text(encoding="utf-8").replace("/*__STYLE__*/", (WEB / "style.css").read_text(encoding="utf-8")).replace("/*__APP__*/", (WEB / "app.js").read_text(encoding="utf-8")).replace("__BOOT_DATA__", boot)


def demo() -> dict:
    from .semantic import compare_file, report
    examples = Path(__file__).parent / "examples"
    paths = sorted({p.name for folder in ("base", "alice", "bob") for p in (examples / folder).iterdir() if p.is_file()})
    files = []
    for path in paths:
        versions = [(examples / folder / path).read_text(encoding="utf-8") if (examples / folder / path).exists() else None for folder in ("base", "alice", "bob")]
        files.append(compare_file(path, *versions))
    return report(files, ["main · original", "alice/layout", "bob/lighting"], context={"source": "demo", "repository": "Forest Adventure"})
