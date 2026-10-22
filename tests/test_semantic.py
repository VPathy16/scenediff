import json
import unittest

from scenediff.parser import ParseError, canonical, parse, references
from scenediff.semantic import compare_file, compare_scene


def scene(properties="", children="", resources=""):
    return f'[gd_scene format=3]\n{resources}\n[node name="World" type="Node3D"]\n{properties}\n{children}'


def entity(result, key="node:."):
    return next(e for e in result["entities"] if e["key"] == key)


def row(result, prop, key="node:."):
    return next(r for r in entity(result,key)["rows"] if r["property"] == prop)


class ParserTests(unittest.TestCase):
    def test_multiline_nested_variants_comments_and_strings(self):
        source = scene('''metadata/data = {
"dialog": "[node fake] ; = \\\"hello\\\"",
"items": Array[Vector2]([Vector2(1, 2), Vector2(3, 4)])
} ; comment
visible = true
''')
        doc = parse(source)
        self.assertIn('Array[Vector2]',doc.sections[-1].props["metadata/data"])
        self.assertEqual(doc.sections[-1].props["visible"],"true")

    def test_header_with_spaces_typed_groups_and_resource_constructor(self):
        doc = parse('[gd_scene format=3]\n[node name="A B" groups=["a", "b"] instance=ExtResource("x")]')
        self.assertEqual(doc.sections[1].attrs["name"], '"A B"')
        self.assertEqual(doc.sections[1].attrs["groups"], '["a", "b"]')
        self.assertEqual(references(doc.sections[1].attrs["instance"]), [("ExtResource","x")])

    def test_reference_text_inside_string_is_literal(self):
        value = '"SubResource(\\\"wrong\\\")"'
        self.assertEqual(references(value), [])
        self.assertEqual(canonical(value),canonical(value, {("SubResource","wrong"):"changed"}))

    def test_whitespace_ignored_but_string_whitespace_preserved(self):
        self.assertEqual(canonical('Vector2(1, 2)'),canonical('Vector2( 1 ,2 )'))
        self.assertNotEqual(canonical('"a b"'),canonical('"ab"'))

    def test_bad_delimiters_and_conflict_markers_rejected(self):
        for source in [scene('value = [1, 2'),scene('value = "hello'),scene('<<<<<<< HEAD'),scene('value = Vector2(1,2]')]:
            with self.subTest(source=source),self.assertRaises(ParseError):
                parse(source)

    def test_duplicate_property_and_node_are_rejected(self):
        with self.assertRaises(ParseError): parse(scene('x = 1\nx = 2'))
        result = compare_file('a.tscn',None,scene(),scene(children='[node name="Other" type="Node"]'),three_way=False)
        self.assertTrue(result['warnings'])
        self.assertEqual(result['kind'],'text')

    def test_unknown_sections_retained_with_warning(self):
        result = compare_scene(scene(),scene(),scene(children='[future_section thing="x"]\nvalue = 3'))
        self.assertTrue(result['warnings'])
        self.assertTrue(any(e['kind']=='future_section' for e in result['entities']))


class SemanticTests(unittest.TestCase):
    def test_independent_properties_combine_and_same_property_conflicts(self):
        base=scene('position = Vector3(0,0,0)\nspeed = 5\nenergy = 1')
        left=scene('position = Vector3(2,0,0)\nspeed = 5\nenergy = 2')
        right=scene('position = Vector3(0,0,0)\nspeed = 7\nenergy = 3')
        result=compare_scene(base,left,right)
        self.assertEqual(row(result,'position')['status'],'left')
        self.assertEqual(row(result,'speed')['status'],'right')
        self.assertEqual(row(result,'energy')['status'],'conflict')

    def test_independent_edits_do_not_mark_entity_as_conflict(self):
        result=compare_scene(scene('x = 1\ny = 1'),scene('x = 2\ny = 1'),scene('x = 1\ny = 2'))
        self.assertEqual(entity(result)['status'],'combined')

    def test_identical_changes_are_both(self):
        result=compare_scene(scene('x = 1'),scene('x = 2'),scene('x = 2'))
        self.assertEqual(row(result,'x')['status'],'both')

    def test_two_way_does_not_claim_conflict(self):
        result=compare_scene(None,scene('x = 1'),scene('x = 2'),three_way=False)
        self.assertEqual(row(result,'x')['status'],'modified')

    def test_new_node_and_child_properties_visible(self):
        left=scene(children='[node name="Goblin" type="Node3D" parent="."]\nhealth = 30\n[node name="Body" type="Node3D" parent="Goblin"]')
        result=compare_scene(scene(),left,scene())
        self.assertEqual(entity(result,'node:Goblin')['exists'],[False,True,False])
        self.assertEqual(row(result,'health','node:Goblin')['status'],'left')
        self.assertEqual(entity(result,'node:Goblin/Body')['status'],'left')

    def test_add_add_different_properties_is_structural_conflict(self):
        a=scene(children='[node name="Chest" type="Node" parent="."]\ncoins = 3')
        b=scene(children='[node name="Chest" type="Node" parent="."]\ngems = 1')
        result=compare_scene(scene(),a,b)
        self.assertEqual(entity(result,'node:Chest')['structural_conflict'],'add/add')

    def test_parent_deletion_and_descendant_addition_conflict(self):
        base=scene(children='[node name="Enemies" type="Node" parent="."]')
        right=base+'\n[node name="Goblin" type="Node" parent="Enemies"]'
        result=compare_scene(base,scene(),right)
        self.assertEqual(entity(result,'node:Enemies')['status'],'conflict')
        self.assertEqual(entity(result,'node:Enemies/Goblin')['structural_conflict'],'ancestor deletion')

    def test_delete_versus_edit_conflict(self):
        base=scene(children='[node name="Sun" type="Node" parent="."]\nx = 1')
        result=compare_scene(base,scene(),base.replace('x = 1','x = 2'))
        self.assertEqual(entity(result,'node:Sun')['structural_conflict'],'delete/edit')

    def test_removed_override_is_not_default_or_null(self):
        result=compare_scene(scene('x = 1'),scene(),scene('x = null'))
        r=row(result,'x')
        self.assertEqual(r['values'],['1',None,'null'])
        self.assertEqual(r['status'],'conflict')

    def test_external_resource_id_churn_is_ignored(self):
        base=scene('script = ExtResource("1")',resources='[ext_resource type="Script" path="res://a.gd" id="1"]')
        right=base.replace('"1"','"99"')
        result=compare_file('a.tscn',base,base,right)
        self.assertEqual(result['status'],'unchanged')
        self.assertTrue(result['raw_diff'])

    def test_embedded_resource_id_churn_uses_reference_slots(self):
        base=scene('material = SubResource("M_1")',resources='[sub_resource type="StandardMaterial3D" id="M_1"]\nroughness = 0.5')
        right=base.replace('M_1','M_99')
        self.assertEqual(compare_file('a.tscn',base,base,right)['status'],'unchanged')

    def test_material_property_diff_and_shared_users(self):
        resources='[sub_resource type="StandardMaterial3D" id="mat"]\nroughness = 0.5\nmetallic = 0.0'
        base=scene('material = SubResource("mat")',children='[node name="Shield" type="Node3D" parent="."]\nmaterial = SubResource("mat")',resources=resources)
        result=compare_scene(base,base.replace('roughness = 0.5','roughness = 0.8'),base.replace('metallic = 0.0','metallic = 0.6'))
        material=next(e for e in result['entities'] if e['kind']=='sub_resource')
        self.assertEqual(material['status'],'combined')
        self.assertEqual(len(material['used_by'][0]),2)

    def test_unique_duplicate_material_is_not_collapsed(self):
        base=scene('material = SubResource("a")',children='[node name="Shield" type="Node3D" parent="."]\nmaterial = SubResource("a")',resources='[sub_resource type="StandardMaterial3D" id="a"]\nroughness = 0.5')
        left=base.replace('[node name="World"','[sub_resource type="StandardMaterial3D" id="b"]\nroughness = 0.5\n\n[node name="World"').replace('parent="."]\nmaterial = SubResource("a")','parent="."]\nmaterial = SubResource("b")')
        result=compare_scene(base,left,base)
        self.assertEqual(len([e for e in result['entities'] if e['kind']=='sub_resource']),2)
        self.assertEqual(row(result,'material','node:Shield')['status'],'left')

    def test_missing_resource_definition_warning(self):
        result=compare_scene(scene(),scene('script = ExtResource("missing")'),scene())
        self.assertTrue(any('missing' in w for w in result['warnings']))

    def test_cycles_in_resources_do_not_recurse_forever(self):
        text=scene('data = SubResource("a")',resources='[sub_resource type="Resource" id="a"]\nother = SubResource("b")\n[sub_resource type="Resource" id="b"]\nother = SubResource("a")')
        self.assertEqual(compare_file('a.tscn',text,text,text)['status'],'unchanged')

    def test_sibling_order_changes_are_visible(self):
        a='[node name="A" type="Node" parent="."]'
        b='[node name="B" type="Node" parent="."]'
        result=compare_scene(scene(children=a+'\n'+b),scene(children=b+'\n'+a),scene(children=a+'\n'+b))
        self.assertEqual(entity(result,'order:.')['status'],'left')

    def test_signal_arguments_are_compared_as_properties(self):
        base=scene(children='[connection signal="pressed" from="Button" to="." method="start" flags=0]')
        result=compare_scene(base,base,base.replace('flags=0','flags=1'))
        signal=next(e for e in result['entities'] if e['kind']=='connection')
        self.assertEqual(next(r for r in signal['rows'] if r['property']=='@flags')['status'],'right')

    def test_tres_uses_same_semantics(self):
        base='[gd_resource type="StandardMaterial3D" format=3]\n[resource]\nroughness = 0.5'
        result=compare_file('mat.tres',base,base.replace('0.5','0.8'),base.replace('0.5','0.9'))
        self.assertEqual(result['kind'],'resource')
        self.assertEqual(result['status'],'conflict')

    def test_malformed_input_falls_back_to_raw_diff(self):
        result=compare_file('a.tscn',scene(),scene(),scene('value = [1'))
        self.assertEqual(result['kind'],'text')
        self.assertTrue(result['warnings'])
        self.assertIn('+value = [1',result['raw_diff'])


if __name__ == '__main__': unittest.main()
