import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from datetime import datetime

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.path = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'CODEX_HOME': str(self.path)})
        self.env.start()
        self.dashboard = load('dashboard_server', ROOT/'plugin/dashboard_server.py')

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_home_and_session_parsing(self):
        d = self.dashboard
        self.assertEqual(d.CODEX_HOME, self.path)
        day = datetime.now()
        folder = self.path/'sessions'/day.strftime('%Y/%m/%d')
        folder.mkdir(parents=True)
        transcript = folder/'rollout-test.jsonl'
        stamp = '2026-11-26T10:00:00Z'
        events = [
            {'type':'session_meta','payload':{'id':'s1','source':'exec','cwd':'/workspace/demo'}},
            {'type':'turn_context','payload':{'turn_id':'t1','cwd':'/workspace/demo','model':'codex','effort':'high'}},
            {'type':'event_msg','payload':{'type':'item_completed','turn_id':'t1','item':{'type':'UserMessage','content':[{'text':'Inspect cache'}]}}},
            {'type':'event_msg','payload':{'type':'item_completed','turn_id':'t1','item':{'type':'CommandExecution','command':['test'],'exit_code':0,'aggregated_output':'passed'}}},
            {'type':'event_msg','payload':{'type':'item_completed','turn_id':'t1','item':{'type':'FileChange','changes':{'src/a.py':{'type':'edit','unified_diff':'-old\n+new'}}}}},
            {'type':'event_msg','payload':{'type':'token_count','info':{'last_token_usage':{'input_tokens':100,'cached_input_tokens':40,'output_tokens':20,'total_tokens':120}}}},
            {'type':'event_msg','payload':{'type':'task_complete','turn_id':'t1','last_agent_message':'Done'}}]
        transcript.write_text('\n'.join(json.dumps(dict(x,timestamp=stamp)) for x in events),encoding='utf-8')
        (self.path/'codex-worker-dispatches.json').write_text(json.dumps([{'direction':'ag_to_codex','session_id':'s1','dispatcher_model':'dispatcher','started_at':stamp}]),encoding='utf-8')
        state = d.build_state()
        self.assertEqual(len(state['sessions']),1)
        turn = state['sessions'][0]['turns'][0]
        self.assertEqual(turn['prompt'],'Inspect cache')
        self.assertEqual(turn['dispatcher_model'],'dispatcher')
        self.assertEqual(turn['tokens']['cached'],40)
        self.assertEqual(turn['events'][0]['output'],'passed')
        self.assertIn('+new',turn['events'][1]['changes'][0]['diff'])
        self.assertEqual(turn['status'],'completed')
        self.assertTrue(d.compute_fingerprint())

    def test_excludes_other_direction(self):
        (self.path/'codex-worker-dispatches.json').write_text(json.dumps([{'direction':'other','status':'running'}]))
        self.assertEqual(self.dashboard.build_state()['sessions'],[])

    def test_path_case(self):
        d = self.dashboard
        with patch.object(d.os, 'name', 'nt'):
            self.assertEqual(d.path_key('A/B'),d.path_key('a/b'))
        with patch.object(d.os, 'name', 'posix'):
            self.assertNotEqual(d.path_key('A/B'),d.path_key('a/b'))

    def test_compact_and_resource(self):
        class FastMCP:
            def __init__(self,*a,**k): pass
            def tool(self,*a,**k): return lambda f:f
            def resource(self,*a,**k): return lambda f:f
        modules = {'dashboard_server':self.dashboard,'mcp':types.ModuleType('mcp'),'mcp.server':types.ModuleType('mcp.server'),'mcp.server.fastmcp':types.ModuleType('mcp.server.fastmcp'),'mcp.types':types.ModuleType('mcp.types')}
        modules['mcp.server.fastmcp'].FastMCP = FastMCP
        modules['mcp.types'].CallToolResult = object
        modules['mcp.types'].TextContent = object
        with patch.dict(sys.modules, modules):
            server = load('monitor_server', ROOT/'plugin/server.py')
            huge = {'sessions':[{'turns':[{'events':[{'kind':'command','output':'x'*1000000}]}]}]}
            with patch.object(self.dashboard,'build_state',return_value=huge):
                state = server._compact_state()
            self.assertLess(len(json.dumps(state).encode()),750000)
            self.assertIn('<html',server.codex_monitor_html().lower())

    def test_installer_lifecycle_and_conflict(self):
        installer = load('installer', ROOT/'install.py')
        target = self.path/'installed'
        skill = self.path/'.gemini/config/skills/codex-worker'
        skill.mkdir(parents=True)
        args = types.SimpleNamespace(target_dir=target, python=sys.executable, force=False)
        with patch.object(installer.Path,'home',return_value=self.path), patch.object(installer,'discover_python',return_value={'path':sys.executable,'mcp_version':'test'}), patch.object(installer,'check_target',side_effect=lambda x:x):
            self.assertTrue(installer.doctor(args)['standalone_skill_conflict'])
            installer.install(args)
            self.assertTrue((target/'mcp_config.json').exists())
            fake = self.path/'fake.py'
            fake.write_text('''import json,sys\nfor line in sys.stdin:\n m=json.loads(line); method=m.get("method"); i=m.get("id")\n if i is None: continue\n if method=="initialize": result={}\n elif method=="tools/list": result={"tools":[{"name":"open_codex_monitor"},{"name":"get_codex_state"}]}\n else: result={"contents":[{"text":"<html>ok</html>"}]}\n print(json.dumps({"jsonrpc":"2.0","id":i,"result":result}),flush=True)\n''',encoding='utf-8')
            config={'mcpServers':{'antigravity-codex-monitor':{'command':sys.executable,'args':[str(fake)]}}}
            (target/'mcp_config.json').write_text(json.dumps(config),encoding='utf-8')
            self.assertTrue(installer.verify(args)['verified'])
            installer.uninstall(args)
            self.assertFalse(target.exists())
            self.assertTrue(skill.exists())

    def test_static_and_shell_syntax(self):
        manifest = json.loads((ROOT/'plugin/plugin.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['version'],'1.0.0')
        forbidden = ['liyi'+'lin','App_'+'Downloads','codex_'+'to_ag','antigravity-'+'official.png','<meta name='+'"generator"']
        for parent in ('plugin','ui-src'):
            for file in (ROOT/parent).rglob('*'):
                if file.is_file() and 'node_modules' not in file.parts and '__pycache__' not in file.parts:
                    text = file.read_text(encoding='utf-8')
                    for term in forbidden: self.assertNotIn(term.lower(),text.lower(),str(file))
        bash = shutil.which('bash')
        if bash:
            subprocess.run([bash,'-n',str(ROOT/'plugin/scripts/run-codex.sh')],check=True,stdin=subprocess.DEVNULL)
        shell = shutil.which('pwsh') or shutil.which('powershell')
        if shell:
            command = '$e=$null; $t=$null; [System.Management.Automation.Language.Parser]::ParseFile($env:MONITOR_SCRIPT,[ref]$t,[ref]$e) | Out-Null; if ($e.Count) { $e | Out-String | Write-Error; exit 1 }'
            env = dict(os.environ, MONITOR_SCRIPT=str(ROOT/'plugin/scripts/run-codex.ps1'))
            subprocess.run([shell,'-NoProfile','-Command',command],check=True,stdin=subprocess.DEVNULL,env=env)

if __name__ == '__main__': unittest.main()
