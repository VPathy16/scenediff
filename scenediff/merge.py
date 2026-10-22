"""Copy selected serialized changes into a new Godot document.

All operations happen in memory. Source files and Git worktrees are never written.
"""
from __future__ import annotations

import copy
import json
from pathlib import PurePosixPath

from .parser import Document, TOKEN, canonical, node_path, parse, references, unquote
from .semantic import model, resource_maps

MAX_FILE = 8 * 1024 * 1024
RESOURCE_KINDS = {"ext_resource": "ExtResource", "sub_resource": "SubResource"}
PROTECTED = {
    "node": {"@name", "@parent", "@unique_id", "@type", "@instance"},
    "gd_scene": {"@format", "@uid"},
    "gd_resource": {"@format", "@uid", "@type"},
}


def _under(path, root):
    return root == "." or path == root or path.startswith(root + "/")


def _values(section):
    return [*section.attrs.values(), *section.props.values()]


def _resources(doc):
    return {(RESOURCE_KINDS[s.kind], unquote(s.attrs["id"])): s
            for s in doc.sections if s.kind in RESOURCE_KINDS}


def _render_section(section):
    attrs = "".join(f" {k}={v}" for k, v in section.attrs.items())
    return f"[{section.kind}{attrs}]\n" + "".join(f"{k} = {v}\n" for k, v in section.props.items()) + "\n"


def _serialize(doc, original):
    """Keep untouched sections verbatim, including comments and formatting."""
    lines = original.source.splitlines(keepends=True)
    raw = {}
    for i, section in enumerate(original.sections):
        end = original.sections[i + 1].line - 1 if i + 1 < len(original.sections) else len(lines)
        raw[id(section)] = "".join(lines[section.line - 1:end])
    # Match by object identity: untouched sections remain the original objects.
    chunks = ["".join(lines[:original.sections[0].line - 1])]
    for section in doc.sections:
        text = raw.get(id(section), _render_section(section))
        chunks.append(text if text.endswith("\n") else text + "\n")
    return "".join(chunks)


class SceneMerge:
    def __init__(self, sources, source, imports=None):
        if type(source) is not int or source not in (1, 2):
            raise ValueError("Choose source A (1) or B (2).")
        if not isinstance(sources, list) or len(sources) != 3:
            raise ValueError("Provide base, A and B source text.")
        for text in sources:
            if text is not None and (not isinstance(text, str) or len(text.encode('utf-8')) > MAX_FILE):
                raise ValueError("Each scene must be text of at most 8 MiB.")
        self.source_index, self.target_index = source, 3 - source
        if sources[self.target_index] is None:
            raise ValueError("The target file is absent. Copy the complete source file into your project first.")
        self.docs = [parse(t) if t is not None else None for t in sources]
        self.maps, _ = resource_maps(self.docs)
        self.models = [model(d, m) for d, m in zip(self.docs, self.maps)]
        self.source = self.docs[source]
        self.original = self.docs[self.target_index]
        for doc in (self.source, self.original):
            if doc and doc.warnings:
                raise ValueError("Merging unknown section types is unsupported. Use the raw source to edit this file.")
        if self.source and (self.source.sections[0].kind != self.original.sections[0].kind or
                            self.source.sections[0].attrs.get('format') != self.original.sections[0].attrs.get('format')):
            raise ValueError("Both files must use the same Godot document kind and format.")
        if self.source and self.source.sections[0].kind == 'gd_resource' and self.source.sections[0].attrs.get('type') != self.original.sections[0].attrs.get('type'):
            raise ValueError("Both .tres files must use the same resource type.")
        self.result = Document('', list(self.original.sections), [])
        self.source_resources = _resources(self.source) if self.source else {}
        if imports is not None and (not isinstance(imports, list) or any(
                not isinstance(item, list) or len(item) != 3 or not all(isinstance(v, str) for v in item)
                for item in imports)):
            raise ValueError("Invalid resource import history.")
        self.prior_imports = {(kind, identity): target for kind, identity, target in (imports or [])
                              if (kind, identity) in self.source_resources}
        self.imported = {}
        self.importing = set()
        self.warnings = []
        self.by_key = []
        for doc, entities in zip(self.docs, self.models):
            by_line = {s.line: s for s in doc.sections} if doc else {}
            self.by_key.append({k: by_line[e['line']] for k, e in entities.items() if e['line'] is not None})
        self.target_resource_keys = {logical: ref for ref, logical in self.maps[self.target_index].items()}

    def _replace(self, old, new):
        index = next(i for i, s in enumerate(self.result.sections) if s is old)
        if new is None:
            del self.result.sections[index]
        else:
            self.result.sections[index] = new

    def _identity(self, identity):
        # Format 2 uses numeric IDs; format 3 uses quoted strings.
        return identity if self.original.sections[0].attrs.get('format') == '2' else json.dumps(identity)

    def _equivalent(self, source_ref, target_ref):
        """Compare a complete graph, preserving reference identity and sharing."""
        forward, reverse = {}, {}
        target_resources = _resources(self.result)
        def visit(sref, tref, depth=0):
            if sref in forward:
                return forward[sref] == tref
            if tref in reverse or depth > 128 or sref[0] != tref[0]:
                return False
            source, target = self.source_resources.get(sref), target_resources.get(tref)
            if source is None or target is None:
                return False
            forward[sref], reverse[tref] = tref, sref
            props = lambda s: {**{'@' + k: v for k, v in s.attrs.items() if k != 'id'}, **s.props}
            left, right = props(source), props(target)
            if left.keys() != right.keys():
                return False
            for key, value in left.items():
                a, b = references(value), references(right[key])
                if len(a) != len(b):
                    return False
                amap = {ref: str(i) for i, ref in enumerate(a)}
                bmap = {ref: str(i) for i, ref in enumerate(b)}
                if canonical(value, amap) != canonical(right[key], bmap):
                    return False
                if not all(visit(x, y, depth + 1) for x, y in zip(a, b)):
                    return False
            return True
        return forward if visit(source_ref, target_ref) else None

    def _import(self, ref):
        if ref in self.imported:
            return self.imported[ref]
        source = self.source_resources.get(ref)
        if not source:
            raise ValueError(f"Missing source definition for {ref[0]}({ref[1]!r}).")
        if len(self.importing) > 128:
            raise ValueError("Resource dependency graph is too deep to merge.")
        candidates = []
        if ref in self.prior_imports:
            candidates.append((ref[0], self.prior_imports[ref]))
        candidate = self.target_resource_keys.get(self.maps[self.source_index].get(ref))
        if candidate:
            candidates.append(candidate)
        for candidate in candidates:
            graph = self._equivalent(ref, candidate)
            if graph and all((sref not in self.imported or self.imported[sref] == tref[1]) and
                             not any(other != sref and other[0] == tref[0] and identity == tref[1]
                                     for other, identity in self.imported.items())
                             for sref, tref in graph.items()):
                self.imported.update({sref: tref[1] for sref, tref in graph.items()})
                return candidate[1]
        existing = _resources(self.result)
        identity = ref[1]
        n = 1
        while (ref[0], identity) in existing:
            identity = str(n) if self.original.sections[0].attrs.get('format') == '2' else f'{ref[1]}_merge{n}'
            n += 1
        self.imported[ref] = identity
        self.importing.add(ref)
        section = copy.deepcopy(source)
        section.attrs['id'] = self._identity(identity)
        self.result.sections.append(section)  # Reserve this ID before following dependencies.
        self._rewrite_section(section)
        self.importing.remove(ref)
        if ref[0] == 'ExtResource':
            path = unquote(section.attrs.get('path', ''))
            self.warnings.append(f"Referenced asset {path}: its declaration was copied; ensure the asset exists at this path in the target Godot project.")
        return identity

    def _rewrite(self, value):
        matches = list(TOKEN.finditer(value))
        edits = []
        for i in range(len(matches) - 3):
            name, opening, identity, closing = matches[i:i + 4]
            if name.group() in ('ExtResource', 'SubResource') and opening.group() == '(' and closing.group() == ')':
                ref = (name.group(), unquote(identity.group()))
                mapped = self._import(ref)
                edits.append((identity.start(), identity.end(), self._identity(mapped)))
        for start, end, replacement in reversed(edits):
            value = value[:start] + replacement + value[end:]
        return value

    def _rewrite_section(self, section):
        section.attrs = {k: self._rewrite(v) for k, v in section.attrs.items()}
        section.props = {k: self._rewrite(v) for k, v in section.props.items()}
        return section

    def _remove_links(self, root):
        def touches(section):
            paths = [unquote(section.attrs[k]) for k in ('from', 'to') if k in section.attrs] if section.kind == 'connection' else [unquote(section.attrs.get('path', ''))] if section.kind == 'editable' else []
            return any(_under(p, root) for p in paths)
        self.result.sections = [s for s in self.result.sections if not touches(s)]
        return touches

    def _copy_node(self, key, source, target):
        path = key.removeprefix('node:')
        if source is None and path == '.':
            raise ValueError("The root node cannot be removed from a scene.")
        old = [s for s in self.result.sections if s.kind == 'node' and _under(node_path(s), path)]
        index = next((i for i, s in enumerate(self.result.sections) if s in old), None)
        self.result.sections = [s for s in self.result.sections if s not in old]
        touches = self._remove_links(path)
        if source is None:
            return
        nodes = [s for s in self.source.sections if s.kind == 'node' and _under(node_path(s), path)]
        if index is None:
            # Place additions before the next source sibling already present in the target.
            last = self.source.sections.index(nodes[-1])
            following = {node_path(s) for s in self.source.sections[last + 1:] if s.kind == 'node'}
            index = next((i for i, s in enumerate(self.result.sections) if s.kind == 'node' and node_path(s) in following),
                         next((i for i, s in enumerate(self.result.sections) if s.kind in ('connection', 'editable')), len(self.result.sections)))
        copies = [self._rewrite_section(copy.deepcopy(s)) for s in nodes]
        # unique_id is optional in Godot 4. Do not create duplicates across branches.
        ids = {s.attrs.get('unique_id') for s in self.result.sections if s.kind == 'node'}
        for s in copies:
            if s.attrs.get('unique_id') in ids:
                s.attrs.pop('unique_id', None)
            ids.add(s.attrs.get('unique_id'))
        self.result.sections[index:index] = copies
        for s in self.source.sections:
            if touches(s):
                self.result.sections.append(self._rewrite_section(copy.deepcopy(s)))

    def _copy_order(self, key):
        parent = key.removeprefix('order:')
        source = self.models[self.source_index].get(key)
        if not source:
            raise ValueError("The source has no child order to apply.")
        wanted = json.loads(source['properties']['children'])
        children = [s for s in self.result.sections if s.kind == 'node' and unquote(s.attrs.get('parent', '')) == parent]
        if set(wanted) != {node_path(s) for s in children}:
            raise ValueError("Add or remove the differing child nodes before applying their order.")
        lookup = {node_path(s): s for s in children}
        ordered = iter(lookup[p] for p in wanted)
        self.result.sections = [next(ordered) if s in children else s for s in self.result.sections]

    def apply(self, key, prop=None):
        if not isinstance(key, str) or (prop is not None and not isinstance(prop, str)):
            raise ValueError("Select an entity and an optional property.")
        source = self.by_key[self.source_index].get(key)
        target = self.by_key[self.target_index].get(key)
        if key.startswith('order:'):
            if prop not in (None, 'children'):
                raise ValueError("Select the child order.")
            self._copy_order(key)
        elif source is None and target is None:
            raise ValueError("The selected entity does not exist in A or B.")
        elif prop is not None:
            if not source or not target:
                raise ValueError("Apply the whole node or resource first; it is absent in one version.")
            if prop in PROTECTED.get(target.kind, set()):
                raise ValueError("Apply the whole node for structural attributes such as name, parent, type or instance. Document format and identity cannot be copied as properties.")
            if prop not in self.models[self.source_index][key]['properties'] and prop not in self.models[self.target_index][key]['properties']:
                raise ValueError("The selected property does not exist.")
            self._seed_resource(source, target)
            updated = copy.deepcopy(target)
            attr = prop.startswith('@')
            name = prop[1:] if attr else prop
            source_values = source.attrs if attr else source.props
            values = updated.attrs if attr else updated.props
            if name in source_values:
                values[name] = self._rewrite(source_values[name])
            else:
                values.pop(name, None)
            if target.kind == 'ext_resource' and prop in ('@path', '@uid'):
                # Godot resolves a UID before its path. Keep these selectors together.
                for selector in ('path', 'uid'):
                    if selector in source.attrs:
                        updated.attrs[selector] = source.attrs[selector]
                    else:
                        updated.attrs.pop(selector, None)
                self.warnings.append('External resource path and UID were copied together. Ensure that asset exists in the target project.')
            self._replace(target, updated)
        elif (source or target).kind == 'node':
            self._copy_node(key, source, target)
        elif key == 'header':
            raise ValueError("Copy individual metadata properties; preserve the target document identity and format.")
        elif source is None:
            if target.kind == 'resource':
                raise ValueError("A .tres file must retain its resource section.")
            self._replace(target, None)
        else:
            self._seed_resource(source, target)
            updated = copy.deepcopy(source)
            if source.kind in RESOURCE_KINDS:
                ref = (RESOURCE_KINDS[source.kind], unquote(source.attrs['id']))
                if target is None:
                    self._import(ref)
                    updated = None
                else:
                    updated.attrs['id'] = target.attrs['id']
            if updated is not None:
                self._rewrite_section(updated)
                if target is None:
                    self.result.sections.append(updated)
                else:
                    self._replace(target, updated)
        self._validate_and_order()
        text = _serialize(self.result, self.original)
        if len(text.encode('utf-8')) > MAX_FILE:
            raise ValueError("Merged file exceeds the 8 MiB limit.")
        # Verify serialization round-trips into the same parser/model used for comparison.
        check = parse(text)
        maps, _ = resource_maps([check])
        model(check, maps[0])
        return text, list(dict.fromkeys(self.warnings))

    def _seed_resource(self, source, target):
        if source and target and source.kind in RESOURCE_KINDS:
            self.imported[(RESOURCE_KINDS[source.kind], unquote(source.attrs['id']))] = unquote(target.attrs['id'])

    def _validate_and_order(self):
        sections = self.result.sections
        resources = _resources(self.result)
        for section in sections:
            for value in _values(section):
                for ref in references(value):
                    if ref not in resources:
                        raise ValueError(f"Merge would leave a missing {ref[0]}({ref[1]!r}) definition. Update its references before removing a resource.")
        nodes = {node_path(s): s for s in sections if s.kind == 'node'}
        if len(nodes) != sum(s.kind == 'node' for s in sections):
            raise ValueError("Merge would create duplicate node paths.")
        if self.original.sections[0].kind == 'gd_scene' and '.' not in nodes:
            raise ValueError("A scene must contain a root node.")
        instanced = {p for p, s in nodes.items() if 'instance' in s.attrs}
        def exists(path):
            return path in nodes or any(_under(path, p) for p in instanced)
        for path, node in nodes.items():
            if path != '.' and not exists(unquote(node.attrs.get('parent', ''))):
                raise ValueError(f"Target is missing the parent of {path}. Apply its parent node first.")
            if 'name' not in node.attrs:
                raise ValueError("Nodes must have a name.")
        for section in sections:
            if section.kind == 'connection':
                for k in ('from', 'to'):
                    if not exists(unquote(section.attrs.get(k, ''))):
                        raise ValueError("A signal endpoint is absent in the target. Apply its node first.")
            if section.kind == 'editable' and not exists(unquote(section.attrs.get('path', ''))):
                raise ValueError("An editable instance path is absent in the target.")
        if self.original.sections[0].kind == 'gd_resource' and sum(s.kind == 'resource' for s in sections) != 1:
            raise ValueError("A resource file must contain one resource section.")
        # Definitions must precede their uses; keep stable sibling order for nodes.
        ordered, visiting, visited = [], set(), set()
        def visit(ref):
            if ref in visited:
                return
            if ref in visiting:
                raise ValueError("Cyclic embedded resource references cannot be merged safely.")
            if len(visiting) > 128:
                raise ValueError("Resource dependency graph is too deep to merge.")
            visiting.add(ref)
            section = resources[ref]
            for value in _values(section):
                for dep in references(value):
                    visit(dep)
            visiting.remove(ref)
            visited.add(ref)
            ordered.append(section)
        for ref in resources:
            if ref[0] == 'ExtResource':
                visit(ref)
        for ref in resources:
            visit(ref)
        emitted = set()
        def emit_node(path, depth=0):
            if path in emitted or path not in nodes:
                return
            if depth > 256:
                raise ValueError("Node hierarchy is too deep to merge.")
            node = nodes[path]
            if path != '.':
                parent = unquote(node.attrs.get('parent', ''))
                if parent == path or _under(parent, path):
                    raise ValueError("Invalid node parent hierarchy.")
                emit_node(parent, depth + 1)
            ordered.append(node)
            emitted.add(path)
        for path in nodes:
            emit_node(path)
        ordered.extend(s for s in sections if s.kind not in {*RESOURCE_KINDS, 'node', 'gd_scene', 'gd_resource'})
        header = sections[0]
        if 'load_steps' in header.attrs or resources:
            expected = str(len(resources) + 1)
            if header.attrs.get('load_steps') != expected:
                header = copy.deepcopy(header)
                header.attrs['load_steps'] = expected
        self.result.sections = [header, *ordered]


def merge_selection(sources, source, entity, prop=None, name='scene.tscn', imports=None):
    if not isinstance(name, str) or PurePosixPath(name).suffix.lower() not in ('.tscn', '.tres'):
        raise ValueError("Selective merging supports .tscn and .tres files.")
    try:
        merger = SceneMerge(sources, source, imports)
        text, warnings = merger.apply(entity, prop)
        history = {**merger.prior_imports, **merger.imported}
        return text, warnings, [[kind, identity, target] for (kind, identity), target in history.items()]
    except RecursionError as exc:
        raise ValueError("Scene is too deeply nested to merge.") from exc
