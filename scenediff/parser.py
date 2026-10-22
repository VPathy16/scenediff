"""A non-executing, lossless-value parser for Godot text resource sections.

Variant expressions are retained as text. We tokenize them for comparison,
never evaluate them or load project scripts. Unknown sections remain visible.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


class ParseError(ValueError):
    pass


@dataclass
class Section:
    kind: str
    attrs: dict[str, str]
    line: int
    props: dict[str, str] = field(default_factory=dict)


@dataclass
class Document:
    source: str
    sections: list[Section]
    warnings: list[str]


# A quoted string is one token; apparent references inside it are never rewritten.
TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|[A-Za-z_][A-Za-z_0-9/.:]*|(?:\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)|[^\s]', re.S)


def tokens(value: str) -> list[str]:
    return TOKEN.findall(value)


def canonical(value: str, reference_map: dict[tuple[str, str], str] | None = None) -> str:
    parts = tokens(value)
    if reference_map:
        for i in range(len(parts) - 3):
            if parts[i] in {"ExtResource", "SubResource"} and parts[i + 1] == "(" and parts[i + 3] == ")":
                key = (parts[i], unquote(parts[i + 2]))
                if key in reference_map:
                    parts[i + 2] = json.dumps(reference_map[key], ensure_ascii=False)
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def references(value: str) -> list[tuple[str, str]]:
    parts = tokens(value)
    return [(parts[i], unquote(parts[i + 2])) for i in range(len(parts) - 3)
            if parts[i] in {"ExtResource", "SubResource"}
            and parts[i + 1] == "(" and parts[i + 3] == ")"]


def unquote(value: str) -> str:
    if value.startswith('"') and value.endswith('"'):
        try:
            return json.loads(value)
        except ValueError:
            # Godot permits escapes that JSON does not. Preserve them verbatim.
            return value[1:-1]
    return value


def statements(source: str):
    """Split at top-level newlines, honoring strings, nesting, and comments."""
    stack: list[str] = []
    buf: list[str] = []
    quoted = escaped = comment = False
    line = start = 1
    closing = {")": "(", "]": "[", "}": "{"}
    for char in source.lstrip("\ufeff"):
        if comment:
            if char != "\n":
                continue
            comment = False
        if quoted:
            buf.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            buf.append(char)
        elif char == ";":
            comment = True
        elif char in "([{":
            stack.append(char)
            buf.append(char)
        elif char in ")]}":
            if not stack or stack.pop() != closing[char]:
                raise ParseError(f"Line {line}: unmatched {char}")
            buf.append(char)
        elif char == "\n" and not stack:
            text = "".join(buf).strip()
            if text:
                yield start, text
            buf.clear()
            start = line + 1
        else:
            buf.append(char)
        if char == "\n":
            line += 1
    if quoted or stack:
        raise ParseError(f"Line {start}: unterminated string or expression")
    text = "".join(buf).strip()
    if text:
        yield start, text


def attributes(text: str, line: int) -> dict[str, str]:
    result: dict[str, str] = {}
    pos = 0
    while pos < len(text):
        if not text[pos:].strip():
            break
        match = re.match(r"\s*([\w/]+)\s*=\s*", text[pos:])
        if not match:
            raise ParseError(f"Line {line}: invalid section attributes near {text[pos:pos + 40]!r}")
        key = match[1]
        pos += match.end()
        begin = pos
        stack: list[str] = []
        quoted = escaped = False
        while pos < len(text):
            char = text[pos]
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "([{":
                stack.append(char)
            elif char in ")]}":
                if stack:
                    stack.pop()
            elif char.isspace() and not stack and re.match(r"\s+[\w/]+\s*=", text[pos:]):
                break
            pos += 1
        value = text[begin:pos].strip()
        if not value or key in result:
            raise ParseError(f"Line {line}: missing or duplicate attribute {key}")
        result[key] = value
    return result


def parse(source: str) -> Document:
    sections: list[Section] = []
    warnings: list[str] = []
    for line, text in statements(source):
        if text.startswith("[") and text.endswith("]"):
            match = re.fullmatch(r"\[(\w+)(.*?)\]", text, re.S)
            if not match:
                raise ParseError(f"Line {line}: invalid section header")
            section = Section(match[1], attributes(match[2], line), line)
            if section.kind not in {"gd_scene", "gd_resource", "ext_resource", "sub_resource", "node", "resource", "connection", "editable"}:
                warnings.append(f"Line {line}: unknown section [{section.kind}]; retained for review.")
            sections.append(section)
        else:
            match = re.match(r"([^=]+?)\s*=\s*(.+)\Z", text, re.S)
            if not match or not sections:
                raise ParseError(f"Line {line}: expected a section or property assignment")
            key, value = match[1].strip(), match[2].strip()
            if key in sections[-1].props:
                raise ParseError(f"Line {line}: duplicate property {key}")
            sections[-1].props[key] = value
    if not sections or sections[0].kind not in {"gd_scene", "gd_resource"}:
        raise ParseError("Expected [gd_scene] or [gd_resource] header")
    return Document(source, sections, warnings)


def node_path(section: Section) -> str:
    name = unquote(section.attrs.get("name", '"<unnamed>"'))
    if "parent" not in section.attrs:
        return "."
    parent = unquote(section.attrs["parent"])
    return name if parent == "." else f"{parent}/{name}"
