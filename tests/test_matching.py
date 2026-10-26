import unittest
from pathlib import Path

from scenediff.matching import MatchError
from scenediff.semantic import compare_scene, compare_file

FIXTURES = Path(__file__).parent / "fixtures" / "godot2"


def legacy(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def matched(side="left", old="Player", new="Actors/Hero"):
    return {"side": side, "base_path": old, "target_path": new}


def node(result, key):
    return next(e for e in result["entities"] if e["key"] == "node:" + key)


def prop(entity, name):
    return next(r for r in entity["rows"] if r["property"] == name)


class LegacyMatchingTests(unittest.TestCase):
    def setUp(self):
        self.base, self.left, self.right = [legacy(n + ".tscn") for n in ("base", "renamed", "edited")]

    def test_rename_reparent_is_suggested_but_not_automatically_applied(self):
        result = compare_scene(self.base, self.left, self.right)
        suggestion = next(s for s in result["match_suggestions"] if s["base_path"] == "Player")
        self.assertEqual(suggestion["target_path"], "Actors/Hero")
        self.assertIn("Same script", suggestion["reasons"])
        self.assertIn("Same relative child structure", suggestion["reasons"])
        self.assertEqual(node(result, "Player")["exists"], [True, False, True])
        self.assertEqual(node(result, "Actors/Hero")["exists"], [False, True, False])
        self.assertEqual(result["node_matches"], [])

    def test_confirmation_aligns_rename_with_other_branch_edit(self):
        result = compare_scene(self.base, self.left, self.right, node_matches=[matched()])
        player = node(result, "Player")
        self.assertEqual(player["exists"], [True, True, True])
        self.assertEqual(player["paths"], ["Player", "Actors/Hero", "Player"])
        self.assertEqual(player["status"], "combined")
        self.assertEqual(prop(player, "@name")["values"], ['"Player"', '"Hero"', '"Player"'])
        self.assertEqual(prop(player, "@parent")["status"], "left")
        self.assertEqual(prop(player, "speed")["status"], "right")
        self.assertEqual(prop(player, "script/script")["status"], "unchanged")
        self.assertFalse(any(s["base_path"] == "Player" for s in result["match_suggestions"]))

    def test_children_and_resource_anchors_follow_confirmed_parent(self):
        result = compare_scene(self.base, self.left, self.right, node_matches=[matched()])
        collision = node(result, "Player/Collision")
        self.assertEqual(collision["status"], "unchanged")
        resources = [e for e in result["entities"] if e["kind"] == "sub_resource"]
        self.assertEqual(len(resources), 1)
        self.assertEqual(resources[0]["status"], "unchanged")

    def test_renaming_does_not_report_false_child_reordering(self):
        renamed = self.base.replace('name="Player"', 'name="Hero"').replace('parent="Player"', 'parent="Hero"')
        result = compare_scene(self.base, renamed, self.base, node_matches=[matched(new="Hero")])
        orders = [e for e in result["entities"] if e["kind"] == "order"]
        self.assertTrue(all(e["status"] == "unchanged" for e in orders))

    def test_different_renames_on_both_branches_remain_a_conflict(self):
        b = self.base.replace('name="Player"', 'name="Champion"').replace('parent="Player"', 'parent="Champion"')
        result = compare_scene(self.base, self.left, b, node_matches=[matched(), matched("right", new="Champion")])
        self.assertEqual(prop(node(result, "Player"), "@name")["status"], "conflict")

    def test_property_conflict_remains_after_rename_confirmation(self):
        a = self.left.replace("speed = 200", "speed = 400")
        result = compare_scene(self.base, a, self.right, node_matches=[matched()])
        self.assertEqual(prop(node(result, "Player"), "speed")["status"], "conflict")

    def test_confirmation_can_be_undone_by_empty_mapping(self):
        confirmed = compare_file("level.tscn", self.base, self.left, self.right, node_matches=[matched()])
        undone = compare_file("level.tscn", *confirmed["sources"], node_matches=[])
        self.assertEqual(node(undone, "Player")["exists"], [True, False, True])
        self.assertEqual(confirmed["sources"], [self.base, self.left, self.right])

    def test_identical_duplicates_are_explicitly_ambiguous(self):
        base = '[gd_scene format=1]\n[node name="Root" type="Node"]\n'
        base += '[node name="Old1" type="Sprite" parent="."]\nopacity = 0.5\n'
        base += '[node name="Old2" type="Sprite" parent="."]\nopacity = 0.5\n'
        a = base.replace("Old1", "New1").replace("Old2", "New2")
        result = compare_scene(base, a, base)
        self.assertEqual(len(result["match_suggestions"]), 4)
        self.assertTrue(all(s["ambiguous"] for s in result["match_suggestions"]))
        self.assertEqual(result["node_matches"], [])

    def test_different_types_are_not_suggested(self):
        result = compare_scene(self.base, self.left.replace('type="KinematicBody2D"', 'type="Area2D"'), self.base)
        self.assertFalse(any(s["base_path"] == "Player" for s in result["match_suggestions"]))

    def test_type_alone_is_not_enough_evidence(self):
        base = '[gd_scene format=1]\n[node name="Root" type="Node"]\n[node name="Old" type="Node" parent="."]'
        self.assertEqual(compare_scene(base, base.replace("Old", "New"), base)["match_suggestions"], [])

    def test_invalid_non_bijective_and_stale_maps_are_rejected(self):
        invalid = [[matched(), matched()], [matched(old="Missing")], [matched(new="Missing")],
                   [matched(side="base")], [{"side": "left"}], "bad", [matched(old=".")]]
        for matches in invalid:
            with self.subTest(matches=matches), self.assertRaises(MatchError):
                compare_scene(self.base, self.left, self.right, node_matches=matches)

    def test_existing_original_path_is_not_stealable_by_rename(self):
        left = self.left + '\n[node name="Player" type="KinematicBody2D" parent="."]\n'
        with self.assertRaises(MatchError):
            compare_scene(self.base, left, self.right, node_matches=[matched()])

    def test_two_way_matches_a_to_b(self):
        result = compare_scene(None, self.base, self.left, three_way=False, node_matches=[matched(side="right")])
        self.assertEqual(node(result, "Player")["exists"], [False, True, True])
        self.assertEqual(node(result, "Player")["status"], "modified")

    def test_full_newer_script_property_also_used_as_evidence(self):
        base = self.base.replace("script/script", "script")
        left = self.left.replace("script/script", "script")
        result = compare_scene(base, left, base)
        suggestion = next(s for s in result["match_suggestions"] if s["base_path"] == "Player")
        self.assertIn("Same script", suggestion["reasons"])

    def test_new_parent_removed_on_other_branch_is_a_structural_conflict(self):
        # Moving a node under a parent removed by B must not appear safe.
        b = self.base.replace('[node name="Actors" type="Node2D" parent="."]\n', '')
        result = compare_scene(self.base, self.left, b, node_matches=[matched()])
        self.assertEqual(node(result, "Actors")["status"], "conflict")
        self.assertEqual(node(result, "Player")["structural_conflict"], "ancestor deletion")


if __name__ == "__main__":
    unittest.main()
