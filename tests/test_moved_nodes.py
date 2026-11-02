"""Applying changes when one side moved or re-parented a node and the other edited it."""
import unittest

from scenediff.merge import merge_selection
from scenediff.parser import node_path, parse
from scenediff.semantic import compare_file

BASE = '''[gd_scene format=3]

[node name="Root" type="Control"]

[node name="Panel" type="Panel" parent="."]

[node name="Icon" type="Sprite2D" parent="Panel"]
position = Vector2(10, 10)
modulate = Color(1, 1, 1, 1)
'''
EDITED = BASE.replace("Color(1, 1, 1, 1)", "Color(1, 0, 0, 1)")  # B: same hierarchy, recoloured
MOVED = BASE.replace('parent="Panel"]\nposition', 'parent="."]\nposition')
WRAPPED_ICON = BASE.replace('[node name="Icon" type="Sprite2D" parent="Panel"]',
                            '[node name="Group" type="Node2D" parent="Panel"]\n\n[node name="Icon" type="Sprite2D" parent="Panel/Group"]')
WRAPPED_PANEL = (BASE.replace('[node name="Panel" type="Panel" parent="."]',
                              '[node name="Frame" type="Control" parent="."]\n\n[node name="Panel" type="Panel" parent="Frame"]')
                 .replace('parent="Panel"]', 'parent="Frame/Panel"]'))
CASES = [  # (A, confirmed match, where Icon ends up in A)
    (MOVED, ("Panel/Icon", "Icon"), "Icon"),
    (WRAPPED_ICON, ("Panel/Icon", "Panel/Group/Icon"), "Panel/Group/Icon"),
    (WRAPPED_PANEL, ("Panel", "Frame/Panel"), "Frame/Panel/Icon"),
]


def nodes(text):
    return {node_path(s): s for s in parse(text).sections if s.kind == "node"}


def match(old, new):
    return [{"side": "left", "base_path": old, "target_path": new}]


class MovedNodeTests(unittest.TestCase):
    def test_each_move_is_suggested(self):
        for a, (old, new), where in CASES:
            with self.subTest(where=where):
                suggestions = compare_file("s.tscn", BASE, a, EDITED)["match_suggestions"]
                self.assertIn((old, new), [(s["base_path"], s["target_path"]) for s in suggestions])

    def test_matched_property_lands_on_the_moved_node(self):
        for a, (old, new), where in CASES:
            with self.subTest(where=where):
                text, _, _ = merge_selection([BASE, a, EDITED], 2, "node:Panel/Icon", "modulate", "s.tscn", node_matches=match(old, new))
                result = nodes(text)
                self.assertEqual([p for p in result if p.endswith("Icon")], [where])
                self.assertEqual(result[where].props["modulate"], "Color(1, 0, 0, 1)")
                after = compare_file("s.tscn", BASE, text, EDITED, node_matches=match(old, new))
                self.assertFalse(any(e["status"] == "conflict" for e in after["entities"]))

    def test_matched_whole_node_keeps_the_target_location(self):
        for a, (old, new), where in CASES:
            with self.subTest(where=where):
                text, _, _ = merge_selection([BASE, a, EDITED], 2, "node:Panel/Icon", None, "s.tscn", node_matches=match(old, new))
                result = nodes(text)
                self.assertEqual([p for p in result if p.endswith("Icon")], [where])
                self.assertEqual(result[where].props["modulate"], "Color(1, 0, 0, 1)")

    def test_unmatched_whole_node_is_not_duplicated(self):
        for a, _, where in CASES[:2]:
            with self.subTest(where=where):
                with self.assertRaisesRegex(ValueError, f"already has Icon at {where}"):
                    merge_selection([BASE, a, EDITED], 2, "node:Panel/Icon", None, "s.tscn")

    def test_genuinely_new_node_with_common_name_is_allowed(self):
        other = BASE.replace("[node name=\"Panel\"", "[node name=\"Panel2\" type=\"Panel\" parent=\".\"]\n\n[node name=\"Icon\" type=\"Sprite2D\" parent=\"Panel2\"]\n\n[node name=\"Panel\"")
        added = BASE + '\n[node name="Badge" type="Control" parent="."]\n\n[node name="Icon" type="Sprite2D" parent="Badge"]\n'
        text, _, _ = merge_selection([BASE, other, added], 2, "node:Badge", None, "s.tscn")
        self.assertIn("Badge/Icon", nodes(text))

    def test_two_way_guard_needs_strong_similarity(self):
        with self.assertRaisesRegex(ValueError, "already has Icon at Icon"):
            merge_selection([None, MOVED, BASE], 2, "node:Panel/Icon", None, "s.tscn")
        different = MOVED.replace("Vector2(10, 10)", "Vector2(99, 1)").replace("Color(1, 1, 1, 1)", "Color(0, 0, 0, 1)")
        text, _, _ = merge_selection([None, different, BASE], 2, "node:Panel/Icon", None, "s.tscn")
        self.assertEqual(sorted(p for p in nodes(text) if p.endswith("Icon")), ["Icon", "Panel/Icon"])

    def test_child_order_follows_a_renamed_parent(self):
        kids = '\n[node name="One" type="Node" parent="Panel"]\n\n[node name="Two" type="Node" parent="Panel"]\n'
        base = BASE + kids
        renamed = base.replace('name="Panel"', 'name="Box"').replace('parent="Panel"', 'parent="Box"')
        reordered = BASE + kids.replace('"One"', '"Tmp"').replace('"Two"', '"One"').replace('"Tmp"', '"Two"')
        text, _, _ = merge_selection([base, renamed, reordered], 2, "order:Panel", None, "s.tscn", node_matches=match("Panel", "Box"))
        order = [p for p in nodes(text) if p.startswith("Box/") and p.count("/") == 1]
        self.assertEqual(order, ["Box/Icon", "Box/Two", "Box/One"])


if __name__ == "__main__":
    unittest.main()
