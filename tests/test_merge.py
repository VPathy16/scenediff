import unittest

from scenediff.merge import merge_selection
from scenediff.parser import node_path, parse, references, unquote
from scenediff.semantic import compare_scene


def scene(props='', children='', resources=''):
    return f'[gd_scene format=3]\n{resources}\n[node name="World" type="Node3D"]\n{props}\n{children}\n'


def apply(a, b, key='node:.', prop=None, source=1, base=None, name='scene.tscn'):
    return merge_selection([base, a, b], source, key, prop, name)[0]


def nodes(text):
    return {node_path(s): s for s in parse(text).sections if s.kind == 'node'}


def resource_key(a, b, kind='sub_resource', base=None, contains=None):
    return next(e['key'] for e in compare_scene(base, a, b, three_way=base is not None)['entities']
                if e['kind'] == kind and (contains is None or contains in e['label']))


class MergeTests(unittest.TestCase):
    def test_property_preserves_target_other_edits_and_untouched_comments(self):
        a=scene('position = Vector3(1, 2, 3)\nspeed = 5')
        b=scene('position = Vector3(0, 0, 0)\nspeed = 9', children='[node name="Child" type="Node" parent="."]\n; keep this comment\nx =  7')
        result=apply(a,b,prop='position')
        self.assertEqual(nodes(result)['.'].props, {'position':'Vector3(1, 2, 3)', 'speed':'9'})
        self.assertIn('; keep this comment\nx =  7', result)
        self.assertEqual(nodes(b)['.'].props['position'], 'Vector3(0, 0, 0)')

    def test_reverse_direction_and_remove_override_not_null(self):
        a=scene('x = null\ny = 1')
        b=scene('y = 2')
        result=apply(a,b,prop='x',source=2)
        self.assertEqual(nodes(result)['.'].props, {'y':'1'})
        self.assertEqual(nodes(apply(a,b,prop='x'))['.'].props['x'], 'null')

    def test_three_way_keys_and_resource_id_churn(self):
        base=scene('material = SubResource("m")',resources='[sub_resource type="Resource" id="m"]\nx = 1\ny = 1')
        a=base.replace('x = 1','x = 2').replace('"m"','"new"')
        b=base.replace('y = 1','y = 3')
        key=resource_key(a,b,base=base)
        result=apply(a,b,key,'x',base=base)
        material=next(s for s in parse(result).sections if s.kind=='sub_resource')
        self.assertEqual(material.attrs['id'],'"m"')
        self.assertEqual(material.props,{'x':'2','y':'3'})

    def test_node_add_includes_subtree_nested_resources_and_signals(self):
        resources='[sub_resource type="Resource" id="inner"]\nx = 3\n[sub_resource type="Resource" id="outer"]\ninner = SubResource("inner")'
        a=scene(children='[node name="Enemy" type="Node" parent="."]\ndata = SubResource("outer")\n[node name="Body" type="Node" parent="Enemy"]\n[connection signal="ready" from="Enemy" to="." method="spawned"]',resources=resources)
        result=apply(a,scene(),'node:Enemy')
        self.assertEqual(set(nodes(result)),{'.','Enemy','Enemy/Body'})
        sections=parse(result).sections
        self.assertEqual([unquote(s.attrs['id']) for s in sections if s.kind=='sub_resource'],['inner','outer'])
        self.assertTrue(any(s.kind=='connection' for s in sections))
        self.assertEqual(sections[0].attrs['load_steps'],'3')

    def test_node_delete_removes_children_connections_editable(self):
        a=scene()
        b=scene(children='[node name="Enemy" type="Node" parent="."]\n[node name="Body" type="Node" parent="Enemy"]\n[connection signal="ready" from="Enemy/Body" to="." method="spawned"]\n[editable path="Enemy"]')
        result=apply(a,b,'node:Enemy')
        self.assertEqual(set(nodes(result)),{'.'})
        self.assertFalse(any(s.kind in ('connection','editable') for s in parse(result).sections))

    def test_subtree_replace_removes_target_only_children_and_retains_other_nodes(self):
        a=scene(children='[node name="Enemy" type="Node" parent="."]\nx = 2')
        b=scene(children='[node name="Enemy" type="Node" parent="."]\nx = 1\n[node name="Body" type="Node" parent="Enemy"]\n[node name="Friend" type="Node" parent="."]\ny = 4')
        result=apply(a,b,'node:Enemy')
        self.assertEqual(set(nodes(result)),{'.','Enemy','Friend'})
        self.assertEqual(nodes(result)['Friend'].props,{'y':'4'})

    def test_conflicting_ids_import_graph_without_changing_existing_users(self):
        a=scene('material = SubResource("m")',resources='[sub_resource type="Resource" id="m"]\nx = 2')
        b=scene('material = SubResource("m")',children='[node name="Other" type="Node" parent="."]\nmaterial = SubResource("m")',resources='[sub_resource type="Resource" id="m"]\nx = 1')
        result=apply(a,b,prop='material')
        refs={p:references(s.props['material'])[0][1] for p,s in nodes(result).items()}
        self.assertNotEqual(refs['.'],refs['Other'])
        resources={unquote(s.attrs['id']):s.props for s in parse(result).sections if s.kind=='sub_resource'}
        self.assertEqual(resources[refs['.']]['x'],'2')
        self.assertEqual(resources[refs['Other']]['x'],'1')

    def test_transitive_dependency_diff_is_not_reused(self):
        resources='[sub_resource type="Resource" id="i"]\nx = 1\n[sub_resource type="Resource" id="o"]\nchild = SubResource("i")'
        b=scene('data = SubResource("o")',resources=resources)
        a=b.replace('x = 1','x = 2')
        result=apply(a,b,prop='data')
        resources={unquote(s.attrs['id']):s for s in parse(result).sections if s.kind=='sub_resource'}
        outer=references(nodes(result)['.'].props['data'])[0][1]
        inner=references(resources[outer].props['child'])[0][1]
        self.assertEqual(resources[inner].props['x'],'2')
        self.assertEqual(resources['i'].props['x'],'1')

    def test_equal_resource_graph_reuses_regenerated_id(self):
        a=scene('data = SubResource("old")',resources='[sub_resource type="Resource" id="old"]\nx = 1')
        b=a.replace('"old"','"new"')
        result=apply(a,b,prop='data')
        self.assertEqual(sum(s.kind=='sub_resource' for s in parse(result).sections),1)
        self.assertEqual(references(nodes(result)['.'].props['data']),[('SubResource','new')])

    def test_external_collision_and_reference_like_strings(self):
        a=scene('script = ExtResource("1")\ntext = "ExtResource(\\"1\\")"',resources='[ext_resource type="Script" path="res://a.gd" id="1"]')
        b=scene('script = ExtResource("1")',resources='[ext_resource type="Script" path="res://b.gd" id="1"]')
        result,warnings,_=merge_selection([None,a,b],1,'node:.')
        self.assertEqual(nodes(result)['.'].props['text'],'"ExtResource(\\"1\\")"')
        self.assertNotEqual(nodes(result)['.'].props['script'],'ExtResource("1")')
        self.assertTrue(any('res://a.gd' in w for w in warnings))

    def test_remove_used_resource_fails(self):
        a=scene()
        b=scene('data = SubResource("m")',resources='[sub_resource type="Resource" id="m"]\nx = 1')
        with self.assertRaisesRegex(ValueError,'missing SubResource'):
            apply(a,b,resource_key(a,b))

    def test_copy_resource_changes_shared_users_in_place(self):
        b=scene('data = SubResource("m")',children='[node name="Other" type="Node" parent="."]\ndata = SubResource("m")',resources='[sub_resource type="Resource" id="m"]\nx = 1')
        a=b.replace('x = 1','x = 2')
        result=apply(a,b,resource_key(a,b))
        self.assertEqual(sum(s.kind=='sub_resource' for s in parse(result).sections),1)
        self.assertTrue(all(s.props['data']=='SubResource("m")' for s in nodes(result).values()))
        self.assertEqual(next(s for s in parse(result).sections if s.kind=='sub_resource').props['x'],'2')

    def test_child_order_keeps_subtree_valid(self):
        x='[node name="A" type="Node" parent="."]\n[node name="Child" type="Node" parent="A"]'
        y='[node name="B" type="Node" parent="."]'
        result=apply(scene(children=y+'\n'+x),scene(children=x+'\n'+y),'order:.')
        self.assertEqual(list(nodes(result)),['.','B','A','A/Child'])

    def test_order_requires_matching_children(self):
        a=scene(children='[node name="A" type="Node" parent="."]')
        with self.assertRaisesRegex(ValueError,'differing child nodes'):
            apply(a,scene(),'order:.')

    def test_missing_parent_requires_parent_first(self):
        a=scene(children='[node name="A" type="Node" parent="."]\n[node name="B" type="Node" parent="A"]')
        with self.assertRaisesRegex(ValueError,'missing the parent'):
            apply(a,scene(),'node:A/B')

    def test_root_delete_and_partial_node_identity_blocked(self):
        with self.assertRaisesRegex(ValueError,'root node'):
            apply(None,scene())
        with self.assertRaises(ValueError):
            apply(scene(),scene(),prop='@name')

    def test_unique_ids_remain_unique(self):
        a=scene(children='[node name="A" type="Node" parent="." unique_id=12]')
        b=scene(children='[node name="B" type="Node" parent="." unique_id=12]')
        result=apply(a,b,'node:A')
        self.assertNotIn('unique_id',nodes(result)['A'].attrs)
        self.assertEqual(nodes(result)['B'].attrs['unique_id'],'12')

    def test_tres_merge(self):
        a='[gd_resource type="StandardMaterial3D" format=3]\n[resource]\nroughness = 0.2\nmetallic = 0.0'
        b=a.replace('0.2','0.8').replace('0.0','0.6')
        result=apply(a,b,'resource','roughness',name='mat.tres')
        self.assertEqual(parse(result).sections[-1].props,{'roughness':'0.2','metallic':'0.6'})

    def test_format2_numeric_resource_ids(self):
        a=scene('data = SubResource("1")',resources='[sub_resource type="Resource" id="1"]\nx = 2').replace('format=3','format=2').replace('id="1"','id=1').replace('SubResource("1")','SubResource( 1 )')
        b=a.replace('x = 2','x = 1')
        result=apply(a,b,prop='data')
        resources=[s for s in parse(result).sections if s.kind=='sub_resource']
        self.assertEqual([s.attrs['id'] for s in resources],['1','2'])
        self.assertEqual(references(nodes(result)['.'].props['data']),[('SubResource','2')])

    def test_malformed_unknown_mismatched_and_invalid_requests(self):
        cases=[([None,scene('x = ['),scene()],1,'node:.'),
               ([None,scene(children='[future x=1]'),scene()],1,'node:.'),
               ([None,scene().replace('format=3','format=2'),scene()],1,'node:.'),
               ([None,scene(),None],1,'node:.'),
               ([None,scene(),scene()],0,'node:.'),
               ([None,scene(),scene()],True,'node:.'),
               ([None,scene(),scene()],1,'does-not-exist'),
               ([None,scene(),scene()],1,None)]
        for sources,source,key in cases:
            with self.subTest(key=key,source=source),self.assertRaises(ValueError):
                merge_selection(sources,source,key)

    def test_cycle_rejected_without_hang(self):
        a=scene('data = SubResource("a")',resources='[sub_resource type="Resource" id="a"]\nother = SubResource("b")\n[sub_resource type="Resource" id="b"]\nother = SubResource("a")')
        with self.assertRaisesRegex(ValueError,'Cyclic'):
            apply(a,scene(),prop='data')

    def test_repeated_apply_preserves_source_sharing_and_revalidates_history(self):
        a=scene('first = SubResource("m")\nsecond = SubResource("m")',resources='[sub_resource type="Resource" id="m"]\nx = 2')
        b=a.replace('x = 2','x = 1')
        first,_,history=merge_selection([None,a,b],1,'node:.','first')
        second,_,_=merge_selection([None,a,first],1,'node:.','second',imports=history)
        self.assertEqual(nodes(second)['.'].props['first'],nodes(second)['.'].props['second'])
        self.assertEqual(sum(s.kind=='sub_resource' for s in parse(second).sections),2)
        # Cached mappings are hints: an edited definition must be copied again.
        modified=first.replace('x = 2','x = 3')
        second,_,_=merge_selection([None,a,modified],1,'node:.','second',imports=history)
        self.assertNotEqual(nodes(second)['.'].props['first'],nodes(second)['.'].props['second'])

    def test_external_path_and_uid_remain_paired(self):
        a=scene('script = ExtResource("1")',resources='[ext_resource type="Script" path="res://a.gd" uid="uid://same" id="1"]')
        b=a.replace('res://a.gd','res://b.gd')
        result=apply(a,b,resource_key(a,b,'ext_resource'),'@path')
        ext=next(s for s in parse(result).sections if s.kind=='ext_resource')
        self.assertEqual(ext.attrs['path'],'"res://a.gd"')
        self.assertEqual(ext.attrs['uid'],'"uid://same"')

    def test_node_add_restores_source_sibling_position(self):
        one='[node name="One" type="Node" parent="."]'
        two='[node name="Two" type="Node" parent="."]'
        self.assertEqual(list(nodes(apply(scene(children=one+'\n'+two),scene(children=two),'node:One'))),['.','One','Two'])


if __name__=='__main__':
    unittest.main()
