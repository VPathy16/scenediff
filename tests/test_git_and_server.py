import http.client
import json
import re
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from scenediff.cli import main
from scenediff.gitrepo import GitError, compare_git
from scenediff.report import demo, render
from scenediff.server import create_server


class GitTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo=Path(self.directory.name)
        self.git('init','-b','main')
        self.git('config','user.name','SceneDiff Test')
        self.git('config','user.email','test@example.invalid')
        self.write('project.godot','config_version=5')
        self.base='[gd_scene format=3]\n[node name="World" type="Node3D"]\nx = 1\ny = 1\n'
        self.write('scene.tscn',self.base)
        self.git('add','.'); self.git('commit','-m','base')
        self.git('switch','-c','alice')
        self.write('scene.tscn',self.base.replace('x = 1','x = 2'))
        self.write('new.gd','extends Node\n')
        self.git('add','.'); self.git('commit','-m','alice changes')
        self.git('switch','-c','bob','main')
        self.write('scene.tscn',self.base.replace('y = 1','y = 3'))
        self.git('add','.'); self.git('commit','-m','bob changes')

    def git(self,*args):
        return subprocess.check_output(['git','-C',str(self.repo),*args],stderr=subprocess.DEVNULL).decode().strip()

    def write(self,path,text):
        (self.repo/path).write_text(text,encoding='utf-8')

    def test_git_merge_base_and_new_script_no_checkout(self):
        self.write('uncommitted.txt','preserve me')
        head=self.git('rev-parse','HEAD')
        result=compare_git(str(self.repo),'alice','bob')
        self.assertEqual(result['context']['commits'][0],self.git('rev-parse','main'))
        files={f['path']:f for f in result['files']}
        self.assertEqual(files['scene.tscn']['status'],'combined')
        self.assertEqual(files['new.gd']['exists'],[False,True,False])
        self.assertEqual(self.git('rev-parse','HEAD'),head)
        self.assertEqual((self.repo/'uncommitted.txt').read_text(),'preserve me')

    def test_ref_injection_does_not_execute(self):
        for ref in ['--help','--output=/tmp/scenediff-nope','$(touch /tmp/scenediff-nope)']:
            with self.subTest(ref=ref),self.assertRaises(GitError):
                compare_git(str(self.repo),ref,'bob')

    def test_path_filter(self):
        result=compare_git(str(self.repo),'alice','bob',path='new.gd')
        self.assertEqual([f['path'] for f in result['files']],['new.gd'])

    def test_missing_script_is_reported(self):
        text=self.base.replace('[node','[ext_resource type="Script" path="res://missing.gd" id="1"]\n[node')+'script = ExtResource("1")\n'
        self.write('scene.tscn',text)
        self.git('add','.');self.git('commit','-m','missing dependency')
        result=compare_git(str(self.repo),'alice','bob')
        item=next(f for f in result['files'] if f['path']=='scene.tscn')
        self.assertTrue(any('missing.gd' in w for w in item['warnings']))

    def test_no_changes(self):
        result=compare_git(str(self.repo),'bob','bob')
        self.assertEqual(result['files'],[])

    def test_binary_asset_not_decoded_as_scene(self):
        (self.repo/'asset.res').write_bytes(b'\0binary\xff')
        self.git('add','.');self.git('commit','-m','binary')
        result=compare_git(str(self.repo),'alice','bob')
        self.assertEqual(next(f for f in result['files'] if f['path']=='asset.res')['kind'],'binary')

    def test_nested_project_dependency_paths(self):
        (self.repo/'game').mkdir()
        self.write('game/project.godot','config_version=5')
        self.write('game/hero.gd','extends Node')
        self.write('game/hero.tscn','[gd_scene format=3]\n[ext_resource type="Script" path="res://hero.gd" id="1"]\n[node name="Hero" type="Node"]\nscript = ExtResource("1")')
        self.git('add','.');self.git('commit','-m','nested project')
        result=compare_git(str(self.repo),'alice','bob')
        item=next(f for f in result['files'] if f['path']=='game/hero.tscn')
        self.assertFalse(item['warnings'])


class ServerAndReportTests(unittest.TestCase):
    def setUp(self):
        self.server=create_server(initial=demo())
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def request(self,path='/',method='GET',body=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        connection.request(method,path,body,headers or {})
        response=connection.getresponse()
        status,data=response.status,response.read().decode()
        connection.close()
        return status,data

    def test_local_ui_and_files_api(self):
        status,html=self.request()
        self.assertEqual(status,200)
        boot=json.loads(re.search(r'<script type="application/json" id="boot">(.*?)</script>',html,re.S)[1])
        body=json.dumps({'left':'[gd_scene format=3]\n[node name="A" type="Node"]','right':'[gd_scene format=3]\n[node name="B" type="Node"]','name':'a.tscn'})
        status,response=self.request('/api/files','POST',body,{'X-SceneDiff-Token':boot['token'],'Content-Type':'application/json'})
        self.assertEqual(status,200)
        self.assertFalse(json.loads(response)['three_way'])

    def test_merge_api_returns_updated_scene_and_rejects_invalid_edits(self):
        _,html=self.request()
        boot=json.loads(re.search(r'<script type="application/json" id="boot">(.*?)</script>',html,re.S)[1])
        headers={'X-SceneDiff-Token':boot['token'],'Content-Type':'application/json'}
        scene='[gd_scene format=3]\n[node name="World" type="Node"]\nx = 1\ny = 1'
        request={'sources':[None,scene.replace('x = 1','x = 2'),scene.replace('y = 1','y = 3')],
                 'source':1,'entity':'node:.','property':'x','name':'a.tscn'}
        self.assertEqual(self.request('/api/merge','POST',json.dumps(request))[0],403)
        status,response=self.request('/api/merge','POST',json.dumps(request),headers)
        self.assertEqual(status,200)
        merged=json.loads(response)
        self.assertEqual(merged['merge']['target'],2)
        self.assertIn('x = 2',merged['file']['sources'][2])
        self.assertIn('y = 3',merged['file']['sources'][2])
        self.assertEqual(merged['file']['sources'][1],request['sources'][1])
        for field,value in [('source',0),('source',1.0),('source',[]),('sources','invalid'),('property','@name'),('entity',None),('name','file.gd')]:
            with self.subTest(field=field,value=value):
                status,response=self.request('/api/merge','POST',json.dumps({**request,field:value}),headers)
                self.assertEqual(status,400)
                self.assertIn('error',json.loads(response))

    def test_rejects_cross_origin_and_missing_token(self):
        self.assertEqual(self.request('/api/files','POST','{}')[0],403)
        self.assertEqual(self.request(headers={'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request(headers={'Host':'evil.example'})[0],403)

    def test_source_cannot_break_out_of_boot_data(self):
        payload=demo()
        payload['files'][0]['sources'][1]='</script><script>alert(1)</script>'
        html=render(payload)
        self.assertNotIn('</script><script>alert(1)',html)
        boot=json.loads(re.search(r'<script type="application/json" id="boot">(.*?)</script>',html,re.S)[1])
        self.assertEqual(boot['report']['files'][0]['sources'][1],'</script><script>alert(1)</script>')

    def test_export_will_not_overwrite_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'scene.tscn';path.write_text('original')
            self.assertEqual(main(['demo','--no-open','-o',str(path)]),2)
            self.assertEqual(path.read_text(),'original')

    def boot_headers(self):
        _, html = self.request()
        boot = json.loads(re.search(r'<script type="application/json" id="boot">(.*?)</script>', html, re.S)[1])
        return {'X-SceneDiff-Token': boot['token'], 'Content-Type': 'application/json'}

    def test_legacy_node_match_confirmation_undo_and_invalid_request(self):
        headers = self.boot_headers()
        fixtures = Path(__file__).parent / 'fixtures' / 'godot2'
        sources = [(fixtures / (n + '.tscn')).read_text() for n in ('base', 'renamed', 'edited')]
        request = {'name': 'legacy.tscn', 'sources': sources, 'three_way': True, 'warnings': [],
                   'node_matches': [{'side': 'left', 'base_path': 'Player', 'target_path': 'Actors/Hero'}]}
        status, body = self.request('/api/match', 'POST', json.dumps(request), headers)
        self.assertEqual(status, 200)
        updated = json.loads(body)['file']
        self.assertEqual(sum(e['status'] == 'conflict' for e in updated['entities']), 0)
        self.assertEqual(updated['sources'], sources)
        for bad in [None, [{'side': 'left', 'base_path': 'Nope', 'target_path': 'Actors/Hero'}]]:
            self.assertEqual(self.request('/api/match', 'POST', json.dumps({**request, 'node_matches': bad}), headers)[0], 400)
        status, body = self.request('/api/match', 'POST', json.dumps({**request, 'node_matches': []}), headers)
        self.assertEqual(status, 200)
        self.assertGreater(sum(e['status'] == 'conflict' for e in json.loads(body)['file']['entities']), 0)

    def test_match_and_merge_carry_warnings_from_outside_the_comparison(self):
        headers = self.boot_headers()
        fixtures = Path(__file__).parent / 'fixtures' / 'godot2'
        sources = [(fixtures / (n + '.tscn')).read_text() for n in ('base', 'renamed', 'edited')]
        warning = 'A: referenced file res://player.gd is absent from this commit.'
        matches = [{'side': 'left', 'base_path': 'Player', 'target_path': 'Actors/Hero'}]
        request = {'name': 'legacy.tscn', 'sources': sources, 'three_way': True, 'node_matches': matches, 'warnings': [warning]}
        status, body = self.request('/api/match', 'POST', json.dumps(request), headers)
        self.assertEqual(status, 200)
        matched = json.loads(body)['file']
        self.assertIn(warning, matched['warnings'])
        self.assertEqual(matched['extra_warnings'], [warning])
        # Apply B's speed onto A's renamed node; the match and the warning survive.
        merge = {**request, 'warnings': matched['warnings'], 'extra_warnings': matched['extra_warnings'],
                 'source': 2, 'entity': 'node:Player', 'property': 'speed'}
        status, body = self.request('/api/merge', 'POST', json.dumps(merge), headers)
        self.assertEqual(status, 200, body)
        merged = json.loads(body)['file']
        self.assertIn(warning, merged['warnings'])
        self.assertEqual(merged['node_matches'], matches)
        self.assertIn('speed = 300', merged['sources'][1])

if __name__ == '__main__': unittest.main()

