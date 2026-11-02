"""Split text containing unresolved Git conflict markers into its versions.

Supports the default ``merge`` style and the ``diff3``/``zdiff3`` styles, which
also record the common ancestor between ``|||||||`` and ``=======``.
"""

from __future__ import annotations

from dataclasses import dataclass

OPEN, ANCESTOR, SEPARATOR, CLOSE = "<<<<<<<", "|||||||", "=======", ">>>>>>>"


@dataclass
class ConflictSplit:
    ours: str
    base: str | None
    theirs: str
    ours_label: str
    theirs_label: str
    base_label: str | None
    regions: int


def _marker(line: str, marker: str) -> str | None:
    """Return the marker's label if ``line`` is that marker, else None."""
    text = line.rstrip("\r\n")
    if not text.startswith(marker):
        return None
    rest = text[len(marker):]
    if marker == SEPARATOR:
        return "" if rest.strip() == "" else None
    if rest and not rest[0].isspace():
        return None
    return rest.strip()


def has_conflict_markers(text: str | None) -> bool:
    if not text:
        return False
    lines = text.splitlines()
    return any(_marker(l, OPEN) is not None for l in lines) and any(_marker(l, CLOSE) is not None for l in lines)


def split_conflicts(text: str) -> ConflictSplit:
    ours: list[str] = []
    theirs: list[str] = []
    base: list[str] = []
    labels = {"ours": None, "theirs": None, "base": None}
    diff3 = None
    regions = 0
    state = None  # None, "ours", "base", "theirs"
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        if state is None:
            label = _marker(line, OPEN)
            if label is not None:
                state = "ours"
                regions += 1
                labels["ours"] = labels["ours"] or label
                continue
            if _marker(line, CLOSE) is not None or _marker(line, ANCESTOR) is not None:
                raise ValueError(f"Line {number}: conflict marker without a matching {OPEN}")
            ours.append(line); theirs.append(line); base.append(line)
        elif state == "ours":
            if _marker(line, OPEN) is not None:
                raise ValueError(f"Line {number}: nested conflict markers are not supported")
            label = _marker(line, ANCESTOR)
            if label is not None:
                if diff3 is False:
                    raise ValueError(f"Line {number}: mixed merge and diff3 conflict styles")
                diff3, state = True, "base"
                labels["base"] = labels["base"] or label
            elif _marker(line, SEPARATOR) is not None:
                if diff3 is True:
                    raise ValueError(f"Line {number}: diff3 conflict without an ancestor section")
                diff3, state = False, "theirs"
            else:
                ours.append(line)
        elif state == "base":
            if _marker(line, SEPARATOR) is not None:
                state = "theirs"
            elif _marker(line, OPEN) is not None or _marker(line, CLOSE) is not None:
                raise ValueError(f"Line {number}: malformed conflict region")
            else:
                base.append(line)
        else:
            label = _marker(line, CLOSE)
            if label is not None:
                state = None
                labels["theirs"] = labels["theirs"] or label
            elif _marker(line, OPEN) is not None or _marker(line, ANCESTOR) is not None:
                raise ValueError(f"Line {number}: malformed conflict region")
            else:
                theirs.append(line)
    if state is not None:
        raise ValueError(f"Unterminated conflict region (missing {CLOSE})")
    if not regions:
        raise ValueError("No conflict markers found")
    return ConflictSplit("".join(ours), "".join(base) if diff3 else None, "".join(theirs),
                         labels["ours"] or "Ours", labels["theirs"] or "Theirs",
                         (labels["base"] or "Ancestor") if diff3 else None, regions)
