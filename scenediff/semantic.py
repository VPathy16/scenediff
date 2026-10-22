"""Conservative three-way comparison of scene entities and resource identity."""

from __future__ import annotations

import difflib
import hashlib
import json
from collections import defaultdict

from .parser import Document, ParseError, canonical, node_path, parse, references, unquote


def _resources(doc: Document | None, kind: str):
    result = {}
    if doc:
        for section in doc.sections:
            if section.kind == kind:
                identity = unquote(section.attrs.get("id", ""))
                if not identity or identity in result:
                    raise ParseError(f"Line {section.line}: missing or duplicate {kind} id {identity!r}")
                result[identity] = section
    return result


def _usage(doc: Document | None) -> dict[str, set[str]]:
    """Anchor subresources to their reference slots, including nested resources."""
    usage = defaultdict(set)
    subs = _resources(doc, "sub_resource")
    if not doc:
        return usage

    def visit(value, anchor, seen):
        for index, (kind, identity) in enumerate(references(value)):
            if kind != "SubResource":
                continue
            slot = f"{anchor}[{index}]"
            usage[identity].add(slot)
            if identity in subs and identity not in seen and len(seen) < 32:
                for prop, child in subs[identity].props.items():
                    visit(child, f"{slot}/{prop}", seen | {identity})

    for section in doc.sections:
        if section.kind in {"node", "resource"}:
            anchor = node_path(section) if section.kind == "node" else "resource"
            for prop, value in {**section.attrs, **section.props}.items():
                visit(value, f"{anchor}/{prop}", set())
    return usage


def resource_maps(docs: list[Document | None]):
    maps: list[dict[tuple[str, str], str]] = []
    known = {"ExtResource": [], "SubResource": []}
    warnings: list[str] = []
    for version, doc in enumerate(docs):
        mapping: dict[tuple[str, str], str] = {}
        usage = _usage(doc)
        for kind, section_kind in [("ExtResource", "ext_resource"), ("SubResource", "sub_resource")]:
            used_keys = set()
            for identity, section in _resources(doc, section_kind).items():
                typ = section.attrs.get("type", "")
                path = section.attrs.get("path")
                uid = section.attrs.get("uid")
                candidates = []
                # Definitions from this document never collapse into one identity.
                prior = [k for k in known[kind] if k["version"] < version and k["key"] not in used_keys]
                if kind == "ExtResource":
                    candidates = [k for k in prior if path and k["path"] == path]
                    if not candidates:
                        candidates = [k for k in prior if uid and k["uid"] == uid]
                else:
                    candidates = [k for k in prior if k["id"] == identity]
                    if not candidates and usage[identity]:
                        candidates = [k for k in prior if k["type"] == typ and k["usage"] == usage[identity]]
                keys = {k["key"] for k in candidates}
                if len(keys) == 1:
                    logical = next(iter(keys))
                else:
                    logical = f"{'ext' if kind == 'ExtResource' else 'sub'}:{version}:{identity}"
                    if len(keys) > 1:
                        warnings.append(f"Ambiguous resource match for {identity}; kept separate for review.")
                    elif kind == "SubResource" and version and any(k["type"] == typ and k["usage"] & usage[identity] for k in prior):
                        warnings.append(f"Resource {identity} changed identity or sharing; inspect its references.")
                mapping[kind, identity] = logical
                used_keys.add(logical)
                known[kind].append({"key": logical, "version": version, "id": identity,
                                    "path": path, "uid": uid, "type": typ, "usage": usage[identity]})
        maps.append(mapping)
    return maps, warnings


def model(doc: Document | None, mapping: dict) -> dict:
    entities = {}
    if not doc:
        return entities
    root_name = next((unquote(s.attrs.get("name", '"Scene"')) for s in doc.sections if s.kind == "node" and "parent" not in s.attrs), "Scene")
    orders = defaultdict(list)
    usage = _usage(doc)
    for index, section in enumerate(doc.sections):
        attrs = dict(section.attrs)
        label = section.kind
        path = ""
        users = []
        kind = section.kind
        if kind == "node":
            path = node_path(section)
            key = f"node:{path}"
            label = root_name if path == "." else f"{root_name}/{path}"
            if "parent" in attrs:
                orders[unquote(attrs["parent"])].append(path)
        elif kind in {"ext_resource", "sub_resource"}:
            identity = unquote(attrs.pop("id"))
            key = mapping[("ExtResource" if kind == "ext_resource" else "SubResource", identity)]
            label = unquote(attrs.get("path", attrs.get("resource_name", attrs.get("type", '"Resource"'))))
            if kind == "sub_resource":
                users = sorted(usage[identity])
                label += f" · {identity}"
            else:
                for owner in doc.sections:
                    if owner.kind == "node" and any(("ExtResource", identity) in references(v) for v in [*owner.attrs.values(), *owner.props.values()]):
                        users.append(node_path(owner))
        elif kind in {"gd_scene", "gd_resource"}:
            key = "header"
            label = "Scene metadata" if kind == "gd_scene" else "Resource metadata"
            attrs.pop("load_steps", None)
        elif kind == "resource":
            key = "resource"
            label = "Resource properties"
        elif kind == "connection":
            key = "signal:" + canonical(json.dumps([attrs.get(k) for k in ("signal", "from", "to", "method")]))
            label = f'{unquote(attrs.get("from", "?"))} → {unquote(attrs.get("to", "?"))} · {unquote(attrs.get("signal", "?"))}'
        elif kind == "editable":
            key = "editable:" + attrs.get("path", "")
            label = "Editable instance " + unquote(attrs.get("path", ""))
        else:
            key = f"unknown:{index}:{kind}"
            label = f"Unknown section [{kind}]"
        if key in entities:
            raise ParseError(f"Line {section.line}: duplicate scene entity {label}")
        props = {"@" + k: v for k, v in attrs.items()}
        props.update(section.props)
        entities[key] = {"key": key, "kind": kind, "label": label, "path": path,
                         "line": section.line, "properties": props,
                         "canonical": {k: canonical(v, mapping) for k, v in props.items()},
                         "used_by": users}
    for parent, children in orders.items():
        key = f"order:{parent}"
        value = json.dumps(children, ensure_ascii=False)
        entities[key] = {"key": key, "kind": "order", "label": f"Child order · {parent}", "path": parent,
                         "line": None, "properties": {"children": value}, "canonical": {"children": value}, "used_by": []}
    return entities


def classify(base, left, right, three_way=True):
    if not three_way:
        if left == right:
            return "unchanged"
        return "added" if left is None else "removed" if right is None else "modified"
    if base == left == right:
        return "unchanged"
    if left == right:
        return "both"
    if left == base:
        return "right"
    if right == base:
        return "left"
    return "conflict"


def _fingerprint(entity):
    return None if entity is None else entity["canonical"]


def compare_scene(base: str | None, left: str | None, right: str | None, *, three_way=True) -> dict:
    texts = [base, left, right]
    docs = [parse(t) if t is not None else None for t in texts]
    mappings, warnings = resource_maps(docs)
    models = [model(d, m) for d, m in zip(docs, mappings)]
    for version, (doc, mapping) in enumerate(zip(docs, mappings)):
        if not doc:
            continue
        warnings.extend(f'{["Base", "A", "B"][version]}: {w}' for w in doc.warnings)
        for section in doc.sections:
            for value in [*section.attrs.values(), *section.props.values()]:
                for ref in references(value):
                    if ref not in mapping:
                        warnings.append(f'{["Base", "A", "B"][version]}: missing {ref[0]}({ref[1]!r}) definition.')
        if any(s.kind == "node" and "instance" in s.attrs for s in doc.sections):
            warnings.append("Instanced or inherited scenes: this comparison shows serialized overrides; inherited defaults are not expanded.")
    entities = []
    for key in dict.fromkeys(k for m in models for k in m):
        versions = [m.get(key) for m in models]
        representative = next(v for v in reversed(versions) if v is not None)
        overall = classify(*[_fingerprint(v) for v in versions], three_way)
        rows = []
        for prop in sorted({p for v in versions if v for p in v["properties"]}):
            vals = [v["canonical"].get(prop) if v else None for v in versions]
            status = classify(*vals, three_way)
            rows.append({"property": prop, "values": [v["properties"].get(prop) if v else None for v in versions], "status": status})
        exists = [v is not None for v in versions]
        if three_way:
            existence_conflict = exists[0] and exists[1] != exists[2] and overall == "conflict"
            addition_conflict = not exists[0] and exists[1] and exists[2] and overall == "conflict"
        else:
            existence_conflict = addition_conflict = False
        conflicts = sum(r["status"] == "conflict" for r in rows)
        if overall == "conflict" and not conflicts and not existence_conflict and not addition_conflict:
            overall = "combined"
        if existence_conflict or addition_conflict:
            overall = "conflict"
        entities.append({k: representative[k] for k in ("key", "kind", "label", "path")} | {
            "status": overall, "exists": exists, "rows": rows,
            "used_by": [v["used_by"] if v else [] for v in versions],
            "lines": [v["line"] if v else None for v in versions],
            "structural_conflict": "delete/edit" if existence_conflict else "add/add" if addition_conflict else None,
            "notes": [],
        })

    # Ancestor removal conflicts with descendant additions/edits on the other side.
    by_key = {e["key"]: e for e in entities}
    if three_way:
        for entity in entities:
            if entity["kind"] != "node" or not entity["exists"][0]:
                continue
            for removed, edited in [(1, 2), (2, 1)]:
                if entity["exists"][removed] or not entity["exists"][edited]:
                    continue
                prefix = entity["path"] + "/"
                descendants = [e for e in entities if e["kind"] == "node" and e is not entity
                               and (entity["path"] == "." or e["path"].startswith(prefix))]
                for child in descendants:
                    if _fingerprint(models[0].get(child["key"])) != _fingerprint(models[edited].get(child["key"])) and child["exists"][edited]:
                        entity["status"] = child["status"] = "conflict"
                        entity["structural_conflict"] = child["structural_conflict"] = "ancestor deletion"
                        child["notes"].append(f"Parent {entity['label']} was deleted on the other branch.")
                        entity["notes"].append(f"Descendant {child['label']} was added or edited on the other branch.")
    # Renames/moves are deliberately not silently equated with identity.
    removed_nodes = [e for e in entities if e["kind"] == "node" and e["exists"][0] and not all(e["exists"][1:])]
    added_nodes = [e for e in entities if e["kind"] == "node" and not e["exists"][0]]
    if removed_nodes and added_nodes:
        warnings.append("Nodes were both added and removed. Possible renames or moves are shown separately; review node paths and references.")
    return {"entities": entities, "warnings": list(dict.fromkeys(warnings)), "three_way": three_way}


def raw_diff(left: str | None, right: str | None, left_label="A", right_label="B") -> str:
    return "".join(difflib.unified_diff((left or "").splitlines(True), (right or "").splitlines(True), fromfile=left_label, tofile=right_label))


def compare_file(path: str, base: str | None, left: str | None, right: str | None, *, three_way=True, binary=False) -> dict:
    status = classify(base, left, right, three_way)
    result = {"path": path, "status": status, "kind": "binary" if binary else "text", "entities": [],
              "warnings": [], "sources": [base, left, right], "raw_diff": "" if binary else raw_diff(left, right),
              "exists": [v is not None for v in (base, left, right)]}
    if binary:
        result["warnings"] = ["Binary asset: fingerprints are compared; contents require the appropriate editor."]
    elif path.lower().endswith((".tscn", ".tres")):
        try:
            semantic = compare_scene(base, left, right, three_way=three_way)
            result.update(semantic)
            result["kind"] = "scene" if path.lower().endswith(".tscn") else "resource"
            changed = [e for e in result["entities"] if e["status"] != "unchanged"]
            if any(e["status"] == "conflict" for e in changed):
                result["status"] = "conflict"
            elif not changed:
                result["status"] = "unchanged"
            elif three_way and status == "conflict":
                result["status"] = "combined"
        except (ParseError, RecursionError) as exc:
            result["warnings"] = [f"Semantic comparison unavailable: {exc}. Review the complete raw diff."]
    return result


def report(files: list[dict], labels: list[str], *, three_way=True, context=None):
    payload = {"version": 1, "labels": labels, "three_way": three_way, "files": files, "context": context or {}}
    fingerprint = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    payload["id"] = hashlib.sha256(fingerprint).hexdigest()[:20]
    payload["summary"] = {"files": len(files), "changed_files": sum(f["status"] != "unchanged" for f in files),
                          "conflicts": sum(sum(e["status"] == "conflict" for e in f["entities"]) if f["entities"] else f["status"] == "conflict" for f in files),
                          "warnings": sum(len(f["warnings"]) for f in files)}
    return payload
