import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from scenediff.cli import main
from scenediff.conflicts import has_conflict_markers, split_conflicts
from scenediff.semantic import compare_file, compare_local

MERGE = """[gd_scene load_steps=3 format=3]
<<<<<<< HEAD
[ext_resource type="Texture2D" path="res://a.png" id="1_a"]
=======
[ext_resource type="Texture2D" path="res://b.png" id="1_a"]
>>>>>>> origin/feature
[node name="Root" type="Control"]
<<<<<<< HEAD
modulate = Color(1, 0, 0, 1)
=======
modulate = Color(0, 0, 1, 1)
>>>>>>> origin/feature
visible = true
"""

DIFF3 = """[gd_scene format=3]
[node name="Root" type="Node2D"]
<<<<<<< HEAD
x = 2
||||||| merged common ancestors
x = 1
=======
x = 3
>>>>>>> theirs
"""


def entity(payload, key):
    return next(e for e in payload["files"][0]["entities"] if e["key"] == key)


class ConflictMarkerTests(unittest.TestCase):
    def test_split_merge_style(self):
        split = split_conflicts(MERGE)
        self.assertEqual((split.ours_label, split.theirs_label, split.regions, split.base), ("HEAD", "origin/feature", 2, None))
        self.assertIn("a.png", split.ours); self.assertNotIn("b.png", split.ours)
        self.assertIn("b.png", split.theirs); self.assertIn("visible = true", split.theirs)

    def test_split_diff3_and_crlf(self):
        split = split_conflicts(DIFF3.replace("\n", "\r\n"))
        self.assertEqual(split.base_label, "merged common ancestors")
        self.assertTrue(split.base.endswith("x = 1\r\n"))
        self.assertNotIn("<<<<<<<", split.ours + split.base + split.theirs)

    def test_malformed_markers_rejected(self):
        for text in ["<<<<<<< HEAD\nx = 1\n", "x\n>>>>>>> b\n", "<<<<<<< a\n<<<<<<< b\n=======\n>>>>>>> c\n"]:
            with self.assertRaises(ValueError):
                split_conflicts(text)
        self.assertFalse(has_conflict_markers('[node name="<<<<<<<"]\n'))

    def test_single_conflicted_scene_is_compared_semantically(self):
        payload = compare_local("level.tscn", None, MERGE, None)
        self.assertEqual(payload["labels"][1:], ["HEAD", "origin/feature"])
        self.assertFalse(payload["three_way"])
        self.assertEqual(entity(payload, "node:.")["status"], "modified")
        self.assertIn("2 unresolved Git conflict regions", payload["files"][0]["warnings"][0])

    def test_diff3_ancestor_enables_conflict_detection(self):
        payload = compare_local("level.tscn", None, DIFF3, None)
        self.assertTrue(payload["three_way"])
        self.assertEqual(entity(payload, "node:.")["status"], "conflict")
        self.assertEqual(payload["summary"]["conflicts"], 1)

    def test_other_file_is_ignored_and_reported(self):
        payload = compare_local("level.tscn", None, MERGE, '[gd_scene format=3]\n[node name="X" type="Node"]\n')
        self.assertIn("Ignored B", payload["files"][0]["warnings"][1])

    def test_explicit_base_used_with_merge_style(self):
        base = '[gd_scene format=3]\n[ext_resource type="Texture2D" path="res://o.png" id="1_a"]\n[node name="Root" type="Control"]\nvisible = true\n'
        payload = compare_local("level.tscn", base, MERGE, None)
        self.assertTrue(payload["three_way"])
        self.assertTrue(payload["summary"]["conflicts"])

    def test_instanced_subscenes_inside_conflicts(self):
        scene = """[gd_scene load_steps=2 format=3]
[ext_resource type="PackedScene" path="res://player.tscn" id="1_pl"]
[node name="Level" type="Node2D"]
[node name="Player" parent="." instance=ExtResource("1_pl")]
<<<<<<< HEAD
position = Vector2(10, 20)
=======
position = Vector2(99, 20)
>>>>>>> origin/feature
[node name="Sprite2D" parent="Player" index="0"]
modulate = Color(1, 0, 0, 1)
[editable path="Player"]
"""
        payload = compare_local("level.tscn", None, scene, None)
        self.assertEqual(payload["files"][0]["kind"], "scene")
        self.assertEqual(entity(payload, "node:Player")["status"], "modified")
        self.assertEqual(entity(payload, "node:Player/Sprite2D")["status"], "unchanged")
        self.assertTrue(any("Instanced or inherited" in w for w in payload["files"][0]["warnings"]))

    def test_requires_b_without_markers(self):
        with self.assertRaises(ValueError):
            compare_local("a.tscn", None, "[gd_scene format=3]\n", None)

    def test_git_comparison_warns_about_committed_markers(self):
        result = compare_file("level.tscn", None, MERGE, "[gd_scene format=3]\n", three_way=False)
        self.assertIn("A contains unresolved Git conflict markers", result["warnings"][0])

    def test_cli_accepts_single_conflicted_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "level.tscn"
            path.write_bytes(MERGE.replace("\n", "\r\n").encode())
            out = StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["files", str(path), "--json"]), 0)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["files"][0]["kind"], "scene")
            self.assertEqual(payload["labels"][1], "HEAD")


if __name__ == "__main__":
    unittest.main()
