"""Conservative three-way comparison of scene entities and resource identity."""

from __future__ import annotations

import difflib
import hashlib
import json
from collections import defaultdict

from .conflicts import has_conflict_markers, split_conflicts
from .parser import Document, ParseError, canonical, node_path, parse, references, unquote
from .matching import identities


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


def _usage(doc: Document | None, node_paths=None) -> dict[str, set[str]]:
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
            if section.kind == "node" and node_paths:
                anchor = node_paths.get(anchor, anchor)
            for prop, value in {**section.attrs, **section.props}.items():
                visit(value, f"{anchor}/{prop}", set())
    return usage


def resource_maps(docs: list[Document | None], node_maps=None):
    maps: list[dict[tuple[str, str], str]] = []
    known = {"ExtResource": [], "SubResource": []}
    warnings: list[str] = []
    for version, doc in enumerate(docs):
        mapping: dict[tuple[str, str], str] = {}
        usage = _usage(doc, node_maps[version] if node_maps else None)
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


def model(doc: Document | None, mapping: dict, node_paths=None) -> dict:
    entities = {}
    if not doc:
        return entities
    node_paths = node_paths or {}
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
            key = f"node:{node_paths.get(path, path)}"
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
        if kind == "node":
            for attr in ("parent", "owner"):
                if attr in attrs:
                    reference = unquote(attrs[attr])
                    if reference in node_paths:
                        entities[key]["canonical"]["@" + attr] = canonical(json.dumps(node_paths[reference]))
    for parent, children in orders.items():
        key = f"order:{node_paths.get(parent, parent)}"
        value = json.dumps(children, ensure_ascii=False)
        logical_order = json.dumps([node_paths.get(child, child) for child in children], ensure_ascii=False)
        entities[key] = {"key": key, "kind": "order", "label": f"Child order · {parent}", "path": parent,
                         "line": None, "properties": {"children": value}, "canonical": {"children": logical_order}, "used_by": []}
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


def compare_scene(base: str | None, left: str | None, right: str | None, *, three_way=True, node_matches=None) -> dict:
    texts = [base, left, right]
    docs = [parse(t) if t is not None else None for t in texts]
    node_maps, confirmed, suggestions, match_options, match_warnings = identities(docs, node_matches, three_way=three_way)
    mappings, warnings = resource_maps(docs, node_maps)
    warnings.extend(match_warnings)
    models = [model(d, m, n) for d, m, n in zip(docs, mappings, node_maps)]
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
            "paths": [v["path"] if v else None for v in versions],
            "structural_conflict": "delete/edit" if existence_conflict else "add/add" if addition_conflict else None,
            "notes": [],
        })

    # Ancestor removal conflicts with descendant additions/edits on the other side.
    if three_way:
        for entity in entities:
            if entity["kind"] != "node" or not entity["exists"][0]:
                continue
            for removed, edited in [(1, 2), (2, 1)]:
                if entity["exists"][removed] or not entity["exists"][edited]:
                    continue
                parent_path = entity["paths"][edited]
                prefix = parent_path + "/"
                descendants = [e for e in entities if e["kind"] == "node" and e is not entity
                               and e["paths"][edited] is not None
                               and (parent_path == "." or e["paths"][edited].startswith(prefix))]
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
    for entity in entities:
        if entity["kind"] == "node" and len({p for p in entity["paths"] if p is not None}) > 1:
            entity["notes"].append("Confirmed node identity across different paths. Check references and child order before applying a merge.")
    return {"entities": entities, "warnings": list(dict.fromkeys(warnings)), "three_way": three_way,
            "node_matches": confirmed, "match_suggestions": suggestions, "match_options": match_options}


def raw_diff(left: str | None, right: str | None, left_label="A", right_label="B") -> str:
    return "".join(difflib.unified_diff((left or "").splitlines(True), (right or "").splitlines(True), fromfile=left_label, tofile=right_label))


def compare_file(path: str, base: str | None, left: str | None, right: str | None, *, three_way=True, binary=False, node_matches=None) -> dict:
    status = classify(base, left, right, three_way)
    result = {"path": path, "status": status, "kind": "binary" if binary else "text", "entities": [],
              "warnings": [], "sources": [base, left, right], "raw_diff": "" if binary else raw_diff(left, right),
              "exists": [v is not None for v in (base, left, right)]}
    if binary:
        result["warnings"] = ["Binary asset: fingerprints are compared; contents require the appropriate editor."]
    elif path.lower().endswith((".tscn", ".tres")):
        try:
            semantic = compare_scene(base, left, right, three_way=three_way, node_matches=node_matches)
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
            marked = [label for label, text in zip(("Base", "A", "B"), (base, left, right)) if has_conflict_markers(text)]
            if marked:
                result["warnings"] = [f"Semantic comparison unavailable: {' and '.join(marked)} contain{'s' if len(marked) == 1 else ''} "
                                      "unresolved Git conflict markers (<<<<<<< / ======= / >>>>>>>). "
                                      "Compare the conflicted file on its own to review both sides of each conflict."]
            else:
                result["warnings"] = [f"Semantic comparison unavailable: {exc}. Review the complete raw diff."]
    return result


def compare_local(name: str, base: str | None, left: str | None, right: str | None, *,
                  labels=("Base", "A", "B"), binary=False) -> dict:
    """Compare local files. A file with Git conflict markers is split into its sides.

    The conflicted file may be given alone (as A) or with other files, which are
    then ignored because the markers already hold both sides of the merge.
    """
    labels = list(labels)
    marked = [i for i, text in enumerate((base, left, right)) if not binary and has_conflict_markers(text)]
    if not marked:
        if left is None or right is None:
            raise ValueError("Choose both A and B, or a single file containing Git conflict markers")
        three = base is not None
        item = compare_file(name, base, left, right, three_way=three, binary=binary)
        return report([item], labels, three_way=three, context={"source": "files"})
    if len(marked) > 1:
        raise ValueError("More than one file contains Git conflict markers; choose a single conflicted file")
    index = marked[0]
    split = split_conflicts((base, left, right)[index])
    # A diff3 ancestor is the authoritative base; otherwise use a separately provided base.
    use_base = split.base is None and index != 0 and base is not None
    ancestor = split.base if split.base is not None else base if use_base else None
    ignored = [labels[i] for i, text in enumerate((base, left, right))
               if i != index and text is not None and not (i == 0 and use_base)]
    three = ancestor is not None
    item = compare_file(name, ancestor, split.ours, split.theirs, three_way=three)
    plural = "s" if split.regions != 1 else ""
    item["warnings"].insert(0, f"{labels[index]} contains {split.regions} unresolved Git conflict region{plural}. "
                               f"A is its “{split.ours_label}” side and B is its “{split.theirs_label}” side"
                               + (f"; the base is the recorded ancestor ({split.base_label})." if split.base is not None
                                  else "; the base is the separately chosen file." if three
                                  else ". Add a common base (or use git config merge.conflictStyle diff3) to detect conflicts."))
    if ignored:
        item["warnings"].insert(1, f"Ignored {', '.join(ignored)}: the conflicted file already contains both sides of the merge.")
    side_labels = [split.base_label or (labels[0] if three else "Not provided"), split.ours_label, split.theirs_label]
    return report([item], side_labels, three_way=three,
                  context={"source": "files", "conflict_markers": {"file": labels[index], "regions": split.regions}})


def report(files: list[dict], labels: list[str], *, three_way=True, context=None):
    payload = {"version": 1, "labels": labels, "three_way": three_way, "files": files, "context": context or {}}
    fingerprint = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    payload["id"] = hashlib.sha256(fingerprint).hexdigest()[:20]
    payload["summary"] = {"files": len(files), "changed_files": sum(f["status"] != "unchanged" for f in files),
                          "conflicts": sum(sum(e["status"] == "conflict" for e in f["entities"]) if f["entities"] else f["status"] == "conflict" for f in files),
                          "warnings": sum(len(f["warnings"]) for f in files)}
    return payload
