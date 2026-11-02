"""Suggest legacy node identities, and apply only explicit reviewer matches.

Similarity is evidence, not proof: deleting and recreating an identical node
cannot be distinguished from a rename in an ID-less scene. Every suggested
rename/move therefore requires confirmation. Source text is never rewritten.
"""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_left
from collections import Counter

from .parser import Document, ParseError, canonical, node_path, references, unquote


class MatchError(ValueError):
    pass


def nodes(doc: Document | None) -> dict:
    result = {}
    for section in doc.sections if doc else []:
        if section.kind == "node":
            path = node_path(section)
            if path in result:
                raise ParseError(f"Line {section.line}: duplicate node path {path}")
            result[path] = section
    return result


def _descriptions(doc, entries, wanted=None):
    """Describe the nodes in ``wanted`` (default: all). Descendants come from one sorted index."""
    resources = {}
    for section in doc.sections if doc else []:
        if section.kind in {"ext_resource", "sub_resource"}:
            kind = "ExtResource" if section.kind == "ext_resource" else "SubResource"
            resources[kind, unquote(section.attrs.get("id", ""))] = section
    cache = {}

    def resource_signature(key, seen=frozenset()):
        if key in seen or len(seen) >= 12:
            return "<recursive resource>"
        if key in cache:
            return cache[key]
        section = resources.get(key)
        if section is None:
            return "<missing resource>"
        if key[0] == "ExtResource":
            result = canonical(section.attrs.get("path", section.attrs.get("uid", "")))
        else:
            properties = {k: value_signature(v, seen | {key}) for k, v in section.props.items()}
            result = hashlib.sha256(json.dumps([section.attrs.get("type"), properties], sort_keys=True).encode()).hexdigest()
        cache[key] = result
        return result

    def value_signature(value, seen=frozenset()):
        return canonical(value, {ref: resource_signature(ref, seen) for ref in references(value)})

    ordered = sorted(entries)
    result = {}
    for path in (entries if wanted is None else wanted):
        section = entries[path]
        script = section.props.get("script", section.props.get("script/script"))
        if path == ".":
            children = []
        else:
            # Paths under "p/" sort between "p/" and "p0" ("0" follows "/").
            lo, hi = bisect_left(ordered, path + "/"), bisect_left(ordered, path + "0")
            children = sorted((child[len(path) + 1:], entries[child].attrs.get("type", "")) for child in ordered[lo:hi])
        result[path] = {
            "type": section.attrs.get("type", ""),
            "script": value_signature(script) if script is not None else None,
            "instance": value_signature(section.attrs["instance"]) if "instance" in section.attrs else None,
            "props": {k: value_signature(v) for k, v in section.props.items() if k not in {"script", "script/script"}},
            "children": children,
        }
    return result


def _evidence(old_path, new_path, a, b):
    if a["type"] != b["type"]:
        return None
    if a["instance"] != b["instance"]:
        return None
    reasons = ["Same node type"] if a["type"] else []
    score = 10 if a["type"] else 0
    if a["script"] and a["script"] == b["script"]:
        reasons.append("Same script")
        score += 30
    elif a["script"] != b["script"]:
        score -= 15
    if a["instance"]:
        reasons.append("Same instanced scene")
        score += 30
    keys = a["props"].keys() | b["props"].keys()
    same = sum(k in a["props"] and k in b["props"] and a["props"][k] == b["props"][k] for k in keys)
    if same:
        reasons.append(f"{same} of {len(keys)} serialized properties agree")
        score += round(30 * same / len(keys))
    if a["children"] and a["children"] == b["children"]:
        reasons.append("Same relative child structure")
        score += 30
    if old_path.rsplit("/", 1)[-1] == new_path.rsplit("/", 1)[-1]:
        reasons.append("Same name under a different parent")
        score += 20
    if score < 30:
        return None
    return {"score": score, "reasons": reasons}


def identities(docs, confirmed=None, *, three_way=True):
    """Return canonical paths, accepted mappings, candidates, and warnings.

    A mapping has {side: 'left'|'right', base_path: str, target_path: str}.
    In two-way mode base_path refers to A and only side='right' is valid.
    Confirming a subtree also maps descendants at unchanged relative paths.
    """
    confirmed = [] if confirmed is None else confirmed
    if not isinstance(confirmed, list) or len(confirmed) > 1000:
        raise MatchError("Node matches must be a list with at most 1000 entries")
    entries = [nodes(doc) for doc in docs]
    anchor = 0 if three_way else 1
    base = entries[anchor]
    maps = [{} for _ in docs]
    maps[anchor] = {path: path for path in base}
    sides = [("left", 1), ("right", 2)] if three_way else [("right", 2)]
    accepted = []
    warnings = []
    for match in confirmed:
        if (not isinstance(match, dict) or set(match) != {"side", "base_path", "target_path"}
                or not all(isinstance(v, str) for v in match.values())):
            raise MatchError("Each node match requires side, base_path, and target_path strings")
        if match["side"] not in {name for name, _ in sides}:
            raise MatchError("Invalid side for this comparison")

    for side, index in sides:
        target = entries[index]
        pairs = {}
        claimed = {}
        explicit = [m for m in confirmed if m["side"] == side]
        for match in explicit:
            old, new = match["base_path"], match["target_path"]
            if old not in base or new not in target:
                raise MatchError(f"Cannot match missing node {old!r} to {new!r}")
            if old == "." or new == "." or old == new:
                raise MatchError("Root nodes and unchanged paths do not need a rename/move match")
            if old in pairs or new in claimed:
                raise MatchError("Node matches must be one-to-one; a node was matched more than once")
            if base[old].attrs.get("type") != target[new].attrs.get("type"):
                raise MatchError("Matched nodes must have the same serialized type")
            # An ID-less replacement at the original path is ambiguous. Don't
            # silently displace an exact-path identity to satisfy a suggestion.
            if old in target or new in base:
                raise MatchError("A rename/move match requires a removed path and an added path")
            pairs[old] = new
            claimed[new] = old
            accepted.append(dict(match))

        for old, new in sorted(list(pairs.items()), key=lambda pair: pair[0].count("/"), reverse=True):
            for path, section in base.items():
                if not path.startswith(old + "/") or path in pairs:
                    continue
                candidate = new + path[len(old):]
                if (candidate in target and candidate not in claimed and path not in target
                        and section.attrs.get("type") == target[candidate].attrs.get("type")):
                    pairs[path] = candidate
                    claimed[candidate] = path
        for path in base.keys() & target.keys():
            if path not in pairs and path not in claimed:
                pairs[path] = path
                claimed[path] = path
        for path in target:
            logical = claimed.get(path, path)
            if path not in claimed:
                # New children beneath a renamed/moved parent share that
                # parent's logical location across both branches. Do not use
                # this to infer replacement identities for existing children.
                ancestors = [p for p in claimed if p != "." and path.startswith(p + "/")]
                if ancestors:
                    parent = max(ancestors, key=len)
                    candidate = claimed[parent] + path[len(parent):]
                    if candidate not in base:
                        logical = candidate
            maps[index][path] = logical
        if len(set(maps[index].values())) != len(maps[index]):
            raise MatchError("Node matches produce conflicting identities")

    candidates = []
    options = []
    parent_of = lambda p: p.rsplit("/", 1)[0] if "/" in p else "."
    for side, index in sides:
        target = entries[index]
        matched = set(maps[index].values()) & base.keys()
        removed = [p for p in base if p not in matched and p != "."]
        added = [p for p in target if maps[index][p] not in base and p != "."]
        if removed and added:
            options.append({"side": side,
                            "removed": [{"path": p, "type": base[p].attrs.get("type")} for p in removed],
                            "added": [{"path": p, "type": target[p].attrs.get("type")} for p in added]})
        if not removed or not added:
            continue
        removed_set, added_set = set(removed), set(added)
        base_descriptions = _descriptions(docs[anchor], base, removed)
        descriptions = _descriptions(docs[index], target, added)

        def score(olds, news):
            found = []
            for old in olds:
                for new in news:
                    evidence = _evidence(old, new, base_descriptions[old], descriptions[new])
                    if evidence:
                        found.append({"side": side, "base_path": old, "target_path": new, **evidence})
            return found

        # Suggest the top of each moved subtree; confirming it carries its descendants.
        removed_roots = [p for p in removed if parent_of(p) not in removed_set]
        added_roots = [p for p in added if parent_of(p) not in added_set]
        if len(removed_roots) * len(added_roots) > 10000:
            warnings.append(f"{side}: rename suggestions skipped above 10,000 candidate pairs; use explicit node matches.")
            continue
        scored = score(removed_roots, added_roots)
        # Descendants a suggested root would carry along need no suggestion of their own.
        carried_old, carried_new = set(), set()
        for suggestion in scored:
            old_root, new_root = suggestion["base_path"], suggestion["target_path"]
            for path in removed:
                if path.startswith(old_root + "/") and new_root + path[len(old_root):] in added_set:
                    carried_old.add(path); carried_new.add(new_root + path[len(old_root):])
        # Anything still unexplained, e.g. a node wrapped in a new parent (its new path is not a root).
        suggested_old = {suggestion["base_path"] for suggestion in scored}
        suggested_new = {suggestion["target_path"] for suggestion in scored}
        rest_old = [p for p in removed if p not in carried_old and p not in suggested_old]
        rest_new = [p for p in added if p not in carried_new and p not in suggested_new]
        if rest_old and len(rest_old) * len(rest_new) <= 10000:
            tried = {(s["base_path"], s["target_path"]) for s in scored}
            roots_new = set(added_roots)
            scored += [s for s in score(rest_old, rest_new)
                       if (s["base_path"], s["target_path"]) not in tried
                       and not (s["base_path"] in removed_roots and s["target_path"] in roots_new)]
        elif rest_old:
            warnings.append(f"{side}: suggestions for nodes moved out of their parent were skipped above 10,000 candidate pairs; use explicit node matches.")
        by_old = Counter(s["base_path"] for s in scored)
        by_new = Counter(s["target_path"] for s in scored)
        for suggestion in scored:
            suggestion["ambiguous"] = by_old[suggestion["base_path"]] > 1 or by_new[suggestion["target_path"]] > 1
        candidates.extend(sorted(scored, key=lambda s: (-s["score"], s["base_path"], s["target_path"]))[:100])
        if len(scored) > 100:
            warnings.append(f"{side}: showing the first 100 rename/move suggestions; narrow the comparison for more.")
    if accepted:
        warnings.append("Node identities were confirmed for comparison only. Source paths, signal connections, animation tracks, and scripts were not rewritten or validated.")
    return maps, accepted, candidates, options, warnings
