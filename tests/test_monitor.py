from contextlib import closing
import importlib.util
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import runpy
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import uuid
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

    def write_rollout(self, session_id, *events):
        folder = self.path/'sessions'/datetime.now().strftime('%Y/%m/%d')
        folder.mkdir(parents=True, exist_ok=True)
        transcript = folder/f'rollout-{session_id}.jsonl'
        header = ('2026-11-26T10:00:00Z', 'session_meta', {'id':session_id, 'source':'exec', 'cwd':'/workspace/demo'})
        transcript.write_text('\n'.join(
            json.dumps(dict(timestamp=stamp, type=kind, payload=payload))
            for stamp, kind, payload in (header, *events)
        ), encoding='utf-8')
        return transcript

    def test_aborted_and_errored_rollouts_are_terminal(self):
        start, end = '2026-11-26T10:00:00Z', '2026-11-26T10:00:25Z'
        outcomes = (
            ('aborted', {'type':'turn_aborted'}),
            ('failed', {'type':'task_complete', 'error':{'message':'run failed'}}),
            ('failed', {'type':'task_complete', 'status':'failed'}),
        )
        dispatches = []
        for idx, (expected, event) in enumerate(outcomes):
            sid = f's{idx}'
            self.write_rollout(sid, (start, 'turn_context', {'turn_id':'t1'}),
                               (end, 'event_msg', dict(event, turn_id='t1')))
            dispatches.append({'session_id':sid, 'started_at':start, 'status':'running', 'dispatch_id':f'd{idx}'})
        self.dashboard.DISPATCH_LOG.write_text(json.dumps(dispatches), encoding='utf-8')
        sessions = {s['session_id']:s for s in self.dashboard.build_state()['sessions']}
        for idx, (expected, _) in enumerate(outcomes):
            session = sessions[f's{idx}']
            turn = session['turns'][0]
            with self.subTest(session=idx):
                self.assertEqual(session['status'], expected)
                self.assertEqual(turn['status'], expected)
                self.assertEqual(turn['dispatch_id'], f'd{idx}')
                self.assertEqual(turn['duration_s'], 25)

    def test_dispatch_migrates_and_resumes_with_a_new_id(self):
        start, resumed = '2026-11-26T10:00:00Z', '2026-11-26T10:01:00Z'
        first = {'dispatch_id':'first', 'status':'running', 'started_at':start, 'target_root':'/workspace/demo',
                 'prompt_preview':'Inspect cache', 'dispatcher_model':'dispatcher'}
        log = self.dashboard.DISPATCH_LOG
        log.write_text(json.dumps([first]), encoding='utf-8')
        pending, = self.dashboard.build_state()['sessions']
        self.assertEqual((pending['session_id'], pending['dispatch_ids']), ('first', ['first']))
        self.assertEqual(pending['turns'][0]['dispatch_id'], 'first')

        message = {'type':'item_completed', 'turn_id':'t1',
                   'item':{'type':'UserMessage', 'content':[{'text':'Inspect cache'}]}}
        self.write_rollout('sid', (start, 'turn_context', {'turn_id':'t1'}), (start, 'event_msg', message))
        inferred, = self.dashboard.build_state()['sessions']
        self.assertEqual((inferred['session_id'], inferred['turns'][0]['dispatch_id']), ('sid', 'first'))

        first.update(session_id='sid', status='completed')
        second = dict(first, dispatch_id='second', status='running', started_at=resumed, dispatcher_model='new-dispatcher')
        log.write_text(json.dumps([second, first]), encoding='utf-8')
        self.write_rollout('sid', (start, 'turn_context', {'turn_id':'t1'}),
                           (start, 'event_msg', {'type':'task_complete', 'turn_id':'t1'}))
        session, = self.dashboard.build_state()['sessions']
        self.assertEqual([(t['dispatch_id'], t['status']) for t in session['turns']],
                         [('first', 'completed'), ('second', 'running')])

        self.write_rollout('sid', (start, 'turn_context', {'turn_id':'t1'}),
                           (start, 'event_msg', {'type':'task_complete', 'turn_id':'t1'}),
                           (resumed, 'turn_context', {'turn_id':'t2'}))
        session, = self.dashboard.build_state()['sessions']
        self.assertCountEqual(session['dispatch_ids'], ['first', 'second'])
        self.assertEqual([(t['turn_id'], t['dispatch_id'], t['dispatcher_model']) for t in session['turns']],
                         [('t1', 'first', 'dispatcher'), ('t2', 'second', 'new-dispatcher')])

    def test_same_directory_pending_dispatches_stay_distinct(self):
        old, start = '2026-11-26T09:59:59Z', '2026-11-26T10:00:00Z'
        message = {'type':'item_completed', 'turn_id':'t1',
                   'item':{'type':'UserMessage', 'content':[{'text':'Inspect cache'}]}}
        self.write_rollout('older', (old, 'turn_context', {'turn_id':'t1'}), (old, 'event_msg', message))
        self.write_rollout('new', (start, 'turn_context', {'turn_id':'t1'}), (start, 'event_msg', message))
        common = {'started_at':start, 'target_root':'/workspace/demo', 'status':'running', 'prompt_preview':'Inspect cache'}
        entries = [dict(common, dispatch_id='a'), dict(common, dispatch_id='b'),
                   dict(common, dispatch_id='unknown-sid', session_id='not-written')]
        self.dashboard.DISPATCH_LOG.write_text(json.dumps(entries), encoding='utf-8')
        sessions = {s['session_id']:s for s in self.dashboard.build_state()['sessions']}
        self.assertEqual(set(sessions), {'a', 'b', 'older', 'new', 'not-written'})
        self.assertEqual(sessions['older']['dispatch_ids'], [])
        self.assertEqual(sessions['new']['dispatch_ids'], [])
        for sid, dispatch in [('a', 'a'), ('b', 'b'), ('not-written', 'unknown-sid')]:
            self.assertEqual(sessions[sid]['turns'][0]['dispatch_id'], dispatch)
        entries[0]['session_id'] = 'new'
        self.dashboard.DISPATCH_LOG.write_text(json.dumps(entries), encoding='utf-8')
        sessions = {s['session_id']:s for s in self.dashboard.build_state()['sessions']}
        self.assertNotIn('a', sessions)
        self.assertEqual(sessions['new']['dispatch_ids'], ['a'])
        self.assertEqual(sessions['b']['dispatch_ids'], ['b'])

    def test_failed_dispatches_update_the_matching_turn(self):
        first, second = '2026-11-26T10:00:00Z', '2026-11-26T10:01:00Z'
        self.write_rollout(
            's1', (first, 'turn_context', {'turn_id':'t1'}),
            (first, 'event_msg', {'type':'task_complete', 'turn_id':'t1'}),
            (second, 'turn_context', {'turn_id':'t2'}),
            (second, 'event_msg', {'type':'task_complete', 'turn_id':'t2'}),
        )
        for failure in ({'status':'failed'}, {'status':'completed', 'exit_code':'1'}):
            with self.subTest(failure=failure):
                dispatches = [{'session_id':'s1', 'started_at':first, 'status':'completed', 'exit_code':'0'},
                              dict(failure, session_id='s1', started_at=second, dispatch_id='last'),
                              {'dispatch_id':'before-start', 'started_at':second, 'status':'failed'}]
                self.dashboard.DISPATCH_LOG.write_text(json.dumps(dispatches), encoding='utf-8')
                sessions = {s['session_id']:s for s in self.dashboard.build_state()['sessions']}
                self.assertEqual(sessions['s1']['status'], 'failed')
                self.assertEqual([t['status'] for t in sessions['s1']['turns']], ['completed', 'failed'])
                self.assertEqual(sessions['s1']['turns'][-1]['dispatch_id'], 'last')
                self.assertEqual(sessions['before-start']['turns'][0]['status'], 'failed')
                self.assertEqual(sessions['before-start']['turns'][0]['dispatch_id'], 'before-start')

        failed_resume = {'session_id':'s1', 'started_at':'2026-11-26T10:01:02Z', 'status':'failed'}
        self.dashboard.DISPATCH_LOG.write_text(json.dumps([failed_resume]), encoding='utf-8')
        session = self.dashboard.build_state()['sessions'][0]
        self.assertEqual(session['status'], 'failed')
        self.assertEqual([t['status'] for t in session['turns']], ['completed', 'completed', 'failed'])

    def test_sqlite_path_with_uri_metacharacters(self):
        home = self.path/'codex #% home'
        home.mkdir()
        with closing(sqlite3.connect(home/'state_1.sqlite')) as db, db:
            db.execute('CREATE TABLE threads (id, rollout_path, cwd, created_at, updated_at, model, reasoning_effort, tokens_used, source)')
            db.execute("INSERT INTO threads VALUES ('s1', '', '', 1, 2, '', '', 0, 'exec')")
        with patch.dict(os.environ, {'CODEX_HOME':os.path.relpath(home)}):
            dashboard = load('dashboard_relative_home', ROOT/'plugin/dashboard_server.py')
        self.assertEqual(dashboard.CODEX_HOME, home)
        self.assertEqual(dashboard.collect_exec_threads()[0]['session_id'], 's1')

    def test_http_requires_loopback_host_and_disables_caching(self):
        class QuietHandler(self.dashboard.DashboardHandler):
            def log_message(self, *_args):
                pass

        with self.dashboard.ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            port = server.server_port
            try:
                for host, expected in ((f'127.0.0.1:{port}', 200), (f'localhost:{port}', 200),
                                       (f'example.test:{port}', 403), ('localhost', 403), (None, 403)):
                    with self.subTest(host=host):
                        connection = HTTPConnection('127.0.0.1', port, timeout=5)
                        try:
                            connection.putrequest('GET', '/api/state', skip_host=True)
                            if host is not None:
                                connection.putheader('Host', host)
                            connection.endheaders()
                            response = connection.getresponse()
                            response.read()
                            self.assertEqual(response.status, expected)
                            self.assertEqual(response.getheader('Cache-Control'), 'no-store')
                        finally:
                            connection.close()
            finally:
                server.shutdown()
                worker.join()

    def test_path_case(self):
        d = self.dashboard
        with patch.object(d.os, 'name', 'nt'):
            self.assertEqual(d.path_key('A/B'),d.path_key('a/b'))
        with patch.object(d.os, 'name', 'posix'):
            self.assertNotEqual(d.path_key('A/B'),d.path_key('a/b'))

    def server_modules(self, calls=None):
        class FastMCP:
            def __init__(self,*a,**k): pass
            def tool(self,*a,**k): return lambda f:f
            def resource(self,*a,**k): return lambda f:f
            def run(self):
                if calls is not None:
                    calls.append('mcp')
        modules = {'dashboard_server':self.dashboard,'mcp':types.ModuleType('mcp'),'mcp.server':types.ModuleType('mcp.server'),'mcp.server.fastmcp':types.ModuleType('mcp.server.fastmcp'),'mcp.types':types.ModuleType('mcp.types')}
        modules['mcp.server.fastmcp'].FastMCP = FastMCP
        modules['mcp.types'].CallToolResult = types.SimpleNamespace
        modules['mcp.types'].TextContent = types.SimpleNamespace
        return modules

    def load_server(self):
        with patch.dict(sys.modules, self.server_modules()):
            return load('monitor_server', ROOT/'plugin/server.py')

    def test_compact_and_resource(self):
        server = self.load_server()
        huge = {'sessions':[{'turns':[{'events':[{'kind':'command','output':'x'*1000000}]}]}]}
        with patch.object(self.dashboard,'build_state',return_value=huge):
            state = server._compact_state()
        self.assertLess(len(json.dumps(state).encode()),750000)
        self.assertIn('<html',server.codex_monitor_html().lower())

    def test_server_targets_rollout_outside_default_window(self):
        stamp = '2026-11-26T10:00:00Z'
        self.write_rollout('target', (stamp, 'turn_context', {'turn_id':'t1'}))
        old_dir = self.path/'sessions/2020/01/01'
        old_dir.mkdir(parents=True)
        rollout = next(self.path.glob('sessions/*/*/*/rollout-target.jsonl')).rename(old_dir/'rollout-target.jsonl')
        db_path = self.path/'state_1.sqlite'
        with closing(sqlite3.connect(db_path)) as db, db:
            db.execute('CREATE TABLE threads (id PRIMARY KEY, rollout_path, cwd, created_at, updated_at, model, reasoning_effort, tokens_used, source)')
            for i in range(16):
                path = self.write_rollout(f'recent-{i}', (stamp, 'turn_context', {'turn_id':'t1'}))
                db.execute('INSERT INTO threads VALUES (?, ?, ?, 1, ?, "", "", 0, "exec")',
                           (f'recent-{i}', str(path), '/workspace/demo', i+2))
            db.execute('INSERT INTO threads VALUES ("target", ?, ?, 1, 1, "", "", 0, "exec")',
                       (str(rollout), '/workspace/demo'))
        self.dashboard.DISPATCH_LOG.write_text(json.dumps([
            {'session_id':'target', 'dispatch_id':'queued', 'status':'completed', 'started_at':stamp}
        ]), encoding='utf-8')
        server = self.load_server()
        with patch.object(self.dashboard, 'STATE_DB', db_path):
            self.assertNotIn('target', {s['session_id'] for s in server.open_codex_monitor().structuredContent['sessions']})
            opened = server.open_codex_monitor(dispatch_id='queued').structuredContent
            self.assertEqual(opened['focus_dispatch_id'], 'queued')
            self.assertIsInstance(opened['opened_at_ms'], int)
            self.assertIn('target', {s['session_id'] for s in opened['sessions']})
            state = server.get_codex_state(limit=1, session_id='target').structuredContent
            target = next(s for s in state['sessions'] if s['session_id'] == 'target')
            self.assertEqual(target['turns'][0]['dispatch_id'], 'queued')
            self.assertFalse(server.get_codex_state(state['fingerprint'], limit=1, session_id='target').structuredContent['changed'])
            with rollout.open('a', encoding='utf-8') as f:
                f.write('\n' + json.dumps(dict(timestamp=stamp, type='event_msg', payload={'type':'task_complete', 'turn_id':'t1'})))
            updated = server.get_codex_state(state['fingerprint'], limit=1, session_id='target').structuredContent
            self.assertTrue(updated['changed'])
            target = next(s for s in updated['sessions'] if s['session_id'] == 'target')
            self.assertEqual(target['turns'][0]['status'], 'completed')

    def test_compaction_prioritizes_dispatch_session_and_running(self):
        server = self.load_server()
        def state():
            return {'sessions':[
                {'session_id':sid, 'status':status, 'dispatch_ids':[did], 'turns':[{'prompt':'x'*1000}]}
                for sid, status, did in [('recent', 'completed', 'a'), ('run', 'running', 'b'),
                                         ('session-focus', 'completed', 'c'), ('dispatch-focus', 'completed', 'd')]
            ]}
        for max_bytes, ids in ((1200, ['dispatch-focus']), (2300, ['session-focus', 'dispatch-focus']),
                               (3400, ['run', 'session-focus', 'dispatch-focus'])):
            with self.subTest(max_bytes=max_bytes):
                compact = server._fit_state(state(), max_bytes, 'session-focus', 'd')
                self.assertEqual([s['session_id'] for s in compact['sessions']], ids)

    def test_compaction_preserves_target_turn_and_final_utf8_budget(self):
        start, resume = '2026-11-26T10:00:00Z', '2026-11-26T10:01:00Z'
        huge = '测🙂\ud800' * 140_000
        self.write_rollout('target', (start, 'turn_context', {'turn_id':'t1'}),
                           (start, 'event_msg', {'type':'task_complete', 'turn_id':'t1', 'last_agent_message':huge}),
                           (resume, 'turn_context', {'turn_id':'t2'}))
        self.dashboard.DISPATCH_LOG.write_text(json.dumps([
            {'session_id':'target', 'dispatch_id':'first', 'status':'completed', 'started_at':start},
            {'session_id':'target', 'dispatch_id':'second', 'status':'running', 'started_at':resume},
        ]), encoding='utf-8')
        server = self.load_server()
        full = server.open_codex_monitor(session_id='target', dispatch_id='first').structuredContent
        self.assertLessEqual(len(json.dumps(full, ensure_ascii=False).encode('utf-8')), 750_000)
        self.assertEqual((full['focus_session_id'], full['focus_dispatch_id'], full['changed']), ('target', 'first', True))
        with patch.object(server, 'MAX_PAYLOAD_BYTES', 1000):
            small = server.open_codex_monitor(session_id='target', dispatch_id='first').structuredContent
        self.assertLessEqual(len(json.dumps(small, ensure_ascii=False).encode('utf-8')), 1000)
        session, = small['sessions']
        turn, = session['turns']
        self.assertEqual((session['session_id'], turn['turn_id'], turn['dispatch_id']), ('target', 't1', 'first'))
        self.assertEqual(set(session), set(full['sessions'][0]))
        self.assertEqual(set(turn), set(full['sessions'][0]['turns'][0]))

    def test_server_fingerprint_and_recovery(self):
        server = self.load_server()
        old = server.get_codex_state().structuredContent['fingerprint']
        self.assertFalse(server.get_codex_state(old).structuredContent['changed'])
        for args in ({'session_id':'target'}, {'dispatch_id':'target'}, {'limit':1}):
            with self.subTest(args=args):
                payload = server.get_codex_state(old, **args).structuredContent
                self.assertTrue(payload['changed'])
                self.assertNotEqual(payload['fingerprint'], old)
                self.assertFalse(server.get_codex_state(payload['fingerprint'], **args).structuredContent['changed'])
        with patch.object(server, 'MAX_PAYLOAD_BYTES', 2):
            for result in (server.open_codex_monitor('a', 'b'), server.get_codex_state(), server.get_codex_state(old)):
                self.assertTrue(result.isError)
                self.assertIn('byte budget', result.structuredContent['error'])
        with patch.object(self.dashboard, 'build_state', side_effect=OSError('read failed')):
            self.assertTrue(server.open_codex_monitor().isError)
            self.assertTrue(server.get_codex_state().isError)
        self.assertTrue(server.open_codex_monitor().structuredContent['changed'])
        self.assertTrue(server.get_codex_state().structuredContent['changed'])

    def test_mcp_http_is_opt_in(self):
        path = ROOT/'plugin/server.py'
        for flags, env, expected in (([], {}, False), ([], {'CODEX_MONITOR_ENABLE_HTTP':'0'}, False),
                                     (['--enable-http'], {}, True), ([], {'CODEX_MONITOR_ENABLE_HTTP':'1'}, True)):
            with self.subTest(flags=flags, env=env):
                calls = []
                with patch.dict(sys.modules, self.server_modules(calls)), patch.dict(os.environ, env, clear=True), \
                        patch.object(sys, 'argv', [str(path), *flags]), \
                        patch.object(self.dashboard, 'ThreadingHTTPServer', side_effect=OSError) as http:
                    runpy.run_path(str(path), run_name='__main__')
                self.assertEqual(http.called, expected)
                self.assertEqual(calls, ['mcp'])

    def test_installer_lifecycle_and_conflict(self):
        installer = load('installer', ROOT/'install.py')
        target = self.path/'installed'
        skill = self.path/'.gemini/config/skills/codex-worker'
        skill.mkdir(parents=True)
        args = types.SimpleNamespace(target_dir=target, python=sys.executable, force=False)
        with patch.object(installer.Path,'home',return_value=self.path), patch.object(installer,'discover_python',return_value={'path':sys.executable,'mcp_version':'test'}), patch.object(installer,'check_target',side_effect=lambda x:x):
            self.assertTrue(installer.doctor(args)['standalone_skill_conflict'])
            installer.install(args)
            config = json.loads((target/'mcp_config.json').read_text(encoding='utf-8'))
            self.assertEqual(config['mcpServers'], {'monitor': {
                'command':sys.executable, 'args':['${PLUGIN_ROOT}/server.py'],
            }})
            self.assertEqual((target/'LICENSE').read_bytes(), (ROOT/'LICENSE').read_bytes())
            fake = self.path/'fake.py'
            fake.write_text('''import json,sys\nfor line in sys.stdin:\n m=json.loads(line); method=m.get("method"); i=m.get("id")\n if i is None: continue\n if method=="initialize": result={}\n elif method=="tools/list": result={"tools":[{"name":"open_codex_monitor"},{"name":"get_codex_state"}]}\n else: result={"contents":[{"text":"<html>ok</html>"}]}\n print(json.dumps({"jsonrpc":"2.0","id":i,"result":result}),flush=True)\n''',encoding='utf-8')
            config['mcpServers']['monitor']['args'] = [str(fake)]
            (target/'mcp_config.json').write_text(json.dumps(config),encoding='utf-8')
            self.assertTrue(installer.verify(args)['verified'])
            installer.uninstall(args)
            self.assertFalse(target.exists())
            self.assertTrue(skill.exists())

    def test_installer_refuses_foreign_plugin_and_codex_home(self):
        installer = load('installer', ROOT/'install.py')
        foreign = self.path/'foreign'
        foreign.mkdir()
        manifest = foreign/'plugin.json'
        manifest.write_text('{"name":"foreign-plugin"}', encoding='utf-8')
        codex_home = self.path/'codex-home'
        args = types.SimpleNamespace(target_dir=foreign, python=None, force=True)
        with patch.object(installer, 'ROOT', self.path/'source'), patch.object(installer.Path, 'home', return_value=self.path/'home'), patch.dict(
            os.environ, {'CODEX_HOME':os.path.relpath(codex_home)}
        ):
            for operation in (installer.install, installer.uninstall):
                with self.subTest(operation=operation.__name__):
                    with self.assertRaisesRegex(ValueError, 'Refusing'):
                        operation(args)
                    self.assertEqual(json.loads(manifest.read_text(encoding='utf-8'))['name'], 'foreign-plugin')
            for target in (codex_home, codex_home/'nested'):
                with self.subTest(target=target), self.assertRaisesRegex(ValueError, 'Unsafe target'):
                    installer.check_target(target)
            self.assertEqual(installer.check_target(foreign), foreign)

    def test_explicit_python_only_and_virtualenv_path_preserved(self):
        installer = load('installer', ROOT/'install.py')
        invalid = str(self.path/'missing-python')
        with patch.object(installer, 'run_checked', side_effect=FileNotFoundError) as probe:
            self.assertIsNone(installer.discover_python(invalid))
            probe.assert_called_once()
            self.assertEqual(probe.call_args.args[0][0], invalid)
        interpreter = self.path/'venv'/'bin'/'python'
        result = types.SimpleNamespace(returncode=0, stdout=f'{interpreter}\n1.0\n')
        with patch.object(installer, 'run_checked', return_value=result), patch.object(
            installer.Path, 'is_file', return_value=True
        ), patch.object(installer.Path, 'resolve', return_value=Path(sys.executable)):
            self.assertEqual(installer.discover_python(str(interpreter))['path'], str(interpreter))

    def test_failed_upgrade_restores_existing_install(self):
        installer = load('installer', ROOT/'install.py')
        target = self.path/'installed'
        target.mkdir()
        (target/'plugin.json').write_text('{"name":"antigravity-codex-monitor"}', encoding='utf-8')
        marker = target/'previous.txt'
        marker.write_text('previous install', encoding='utf-8')
        args = types.SimpleNamespace(target_dir=target, python=None, force=True)
        rename = Path.rename

        def fail_publish(source, destination):
            if source.name == 'plugin' and destination == target:
                raise OSError('publish failed')
            return rename(source, destination)

        with patch.object(installer, 'check_target', return_value=target), patch.object(
            installer, 'doctor', return_value={'python':{'path':sys.executable}, 'standalone_skill_conflict':False}
        ), patch.object(Path, 'rename', fail_publish):
            with self.assertRaisesRegex(OSError, 'publish failed'):
                installer.install(args)
        self.assertEqual(marker.read_text(encoding='utf-8'), 'previous install')
        self.assertEqual(list(self.path.iterdir()), [target])

    def test_static_and_shell_syntax(self):
        manifest = json.loads((ROOT/'plugin/plugin.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['version'],'1.0.1')
        ps = (ROOT/'plugin/scripts/run-codex.ps1').read_text(encoding='utf-8')
        sh = (ROOT/'plugin/scripts/run-codex.sh').read_text(encoding='utf-8')
        self.assertNotRegex(ps, r'File\]::Replace\([^\n]*,\s*\$null\s*\)')
        self.assertRegex(ps, r'File\]::Replace\([^\n]*,\s*\[NullString\]::Value\)')
        self.assertRegex(ps, r'if \(-not \$ResumeSessionId[^)\n]*\)[^\n]*--worktree')
        self.assertRegex(sh, r'(?m)^if .* -z "\$resume" .*--worktree')
        self.assertNotRegex(sh, r'(?m)^\s*export\b[^\n]*\bMONITOR_PROMPT\b')
        forbidden = ['liyi'+'lin','App_'+'Downloads','codex_'+'to_ag','antigravity-'+'official.png',
                     '<meta name='+'"generator"','gpt-6.2'+'-sol']
        for parent in ('plugin','ui-src'):
            for file in (ROOT/parent).rglob('*'):
                if file.is_file() and 'node_modules' not in file.parts and '__pycache__' not in file.parts:
                    text = file.read_text(encoding='utf-8')
                    for term in forbidden: self.assertNotIn(term.lower(),text.lower(),str(file))
        bash = shutil.which('bash')
        if bash:
            subprocess.run([bash,'-n','plugin/scripts/run-codex.sh'],cwd=ROOT,check=True,stdin=subprocess.DEVNULL,timeout=15)
        shell = shutil.which('powershell') or shutil.which('pwsh')
        if shell:
            command = '$e=$null; $t=$null; [System.Management.Automation.Language.Parser]::ParseFile($env:MONITOR_SCRIPT,[ref]$t,[ref]$e) | Out-Null; if ($e.Count) { $e | Out-String | Write-Error; exit 1 }'
            env = dict(os.environ, MONITOR_SCRIPT=str(ROOT/'plugin/scripts/run-codex.ps1'))
            subprocess.run([shell,'-NoProfile','-Command',command],check=True,stdin=subprocess.DEVNULL,env=env,timeout=15)

    def launcher_fixture(self):
        bin_dir = self.path/'test bin'
        bin_dir.mkdir()
        target = self.path/'target dir'
        target.mkdir()
        prompt = self.path/'prompt.txt'
        prompt.write_text('p' * 140_000, encoding='utf-8')
        result = self.path/'argv.json'
        stub = self.path/'codex_stub.py'
        stub.write_text('''import json, os, pathlib, sys, time
prompt = sys.stdin.read()
thread_id = os.environ.get("TEST_THREAD_ID", "test-session")
dispatch_id = os.environ.get("TEST_DISPATCH_ID")
if os.environ.get("TEST_EXIT_BEFORE_START"):
    sys.exit(int(os.environ["TEST_EXIT_BEFORE_START"]))
print(json.dumps({"type":"thread.started", "thread_id":thread_id}), flush=True)
if os.environ.get("TEST_REQUIRE_LIVE") == "1":
    log = pathlib.Path(os.environ["CODEX_HOME"])/"codex-worker-dispatches.json"
    for _ in range(100):
        try:
            rows = json.loads(log.read_text(encoding="utf-8-sig"))
        except OSError:
            rows = []
        if any(row.get("session_id") == thread_id and row.get("status") == "running"
               and (dispatch_id is None or row.get("dispatch_id") == dispatch_id) for row in rows):
            break
        time.sleep(0.05)
    else:
        sys.exit(91)
pathlib.Path(os.environ["TEST_RESULT"]).write_text(json.dumps({
    "args":sys.argv[1:], "prompt_size":len(prompt.lstrip("\\ufeff").rstrip("\\r\\n")),
    "metadata":[key for key in os.environ if key.startswith("MONITOR_")],
}), encoding="utf-8")
sys.exit(int(os.environ.get("TEST_EXIT", "0")))
''', encoding='utf-8')
        env = {key:value for key, value in os.environ.items() if not key.startswith('MONITOR_')}
        env.update(TEST_PYTHON=Path(sys.executable).as_posix(), TEST_STUB=stub.as_posix(),
                   TEST_BIN=bin_dir.as_posix(), TEST_RESULT=result.as_posix(), TEST_GIT_ROOT=target.as_posix())
        return bin_dir, target, prompt, result, env

    def check_launcher(self, command, result, env, expected_args, expected_exit=0, dispatch_id=None):
        completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=env, cwd=ROOT, timeout=20, encoding='utf-8', errors='replace')
        self.assertEqual(completed.returncode, expected_exit, completed.stdout + completed.stderr)
        captured = json.loads(result.read_text(encoding='utf-8'))
        actual_args = captured['args']
        if '-C' in expected_args:
            self.assertEqual(actual_args[:2], expected_args[:2])
            self.assertEqual(Path(actual_args[2]).resolve(), Path(expected_args[2]).resolve())
            actual_args, expected_args = actual_args[3:], expected_args[3:]
        self.assertEqual(actual_args, expected_args)
        self.assertEqual(captured['prompt_size'], 140_000)
        self.assertEqual(captured['metadata'], [])
        ids = [line.split(': ', 1)[1] for line in completed.stdout.splitlines() if line.startswith('DISPATCH_ID: ')]
        self.assertEqual(len(ids), 2)
        self.assertEqual(ids[0], ids[1])
        if dispatch_id:
            self.assertEqual(ids[0], dispatch_id)
        else:
            self.assertEqual(uuid.UUID(ids[0]).version, 4)
        return ids[0]

    def check_dispatch_log_concurrency(self, command, flag, env):
        def invoke(extra_args, **overrides):
            return subprocess.Popen(command + extra_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env={**env, **overrides}, cwd=ROOT, encoding='utf-8', errors='replace')

        log = self.dashboard.DISPATCH_LOG
        before = log.read_bytes()
        for invalid in ('', ' \t', 'with space', 'with\x7fcontrol', 'x'*129):
            with self.subTest(invalid=invalid), invoke([flag, invalid]) as process:
                process.communicate(timeout=20)
                self.assertNotEqual(process.returncode, 0)
        self.assertEqual(log.read_bytes(), before)

        with invoke([flag, 'startup-failure'], TEST_EXIT_BEFORE_START='11') as process:
            out, err = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 11, out + err)
            self.assertEqual(out.splitlines().count('DISPATCH_ID: startup-failure'), 2)
        failure = next(row for row in json.loads(log.read_bytes()) if row['dispatch_id'] == 'startup-failure')
        self.assertEqual((failure['session_id'], failure['status'], int(failure['exit_code'])), ('', 'failed', 11))

        runs = []
        try:
            for i, dispatch in enumerate(('Concurrent', 'concurrent', 'third')):
                runs.append((dispatch, f'thread-{i}', invoke([flag, dispatch], TEST_EXIT='0', TEST_REQUIRE_LIVE='1',
                             TEST_THREAD_ID=f'thread-{i}', TEST_DISPATCH_ID=dispatch, TEST_RESULT=str(self.path/f'argv-{i}.json'))))
            for dispatch, _, process in runs:
                out, err = process.communicate(timeout=30)
                self.assertEqual(process.returncode, 0, out + err)
                self.assertEqual(out.splitlines().count(f'DISPATCH_ID: {dispatch}'), 2)
        finally:
            for _, _, process in runs:
                if process.poll() is None:
                    process.kill()
                process.communicate()
        rows = {row['dispatch_id']:row for row in json.loads(log.read_bytes())}
        for dispatch, sid, _ in runs:
            self.assertEqual((rows[dispatch]['session_id'], rows[dispatch]['status'], rows[dispatch]['model']), (sid, 'completed', ''))

    @unittest.skipUnless(shutil.which('bash'), 'Bash is unavailable')
    def test_bash_launch_arguments_and_prompt_scope(self):
        bin_dir, target, prompt, result, env = self.launcher_fixture()
        scripts = {
            'python3':'exec "$TEST_PYTHON" "$@"',
            'codex':'exec "$TEST_PYTHON" "$TEST_STUB" "$@"',
            'git':'[[ -d "$2" ]] || exit 128\nprintf "%s\\n" "$TEST_GIT_ROOT"',
        }
        for name, script in scripts.items():
            path = bin_dir/name
            path.write_text('#!/usr/bin/env bash\n' + script + '\n', encoding='utf-8', newline='\n')
            path.chmod(0o755)
        env['TMPDIR'] = self.path.name
        env['CODEX_HOME'] = self.path.name
        env['TEST_REQUIRE_LIVE'] = '1'
        command = [shutil.which('bash'), '-c',
                   'export PATH="$(CDPATH= cd "$TEST_BIN" && pwd -P):$PATH" CDPATH=.; '
                   'exec bash plugin/scripts/run-codex.sh "$@"',
                   'launcher', '--target-path', target.relative_to(ROOT).as_posix(), '--prompt-file', prompt.as_posix(),
                   '--model', 'test-model']
        options = ['-s', 'workspace-write', '-c', 'model_reasoning_effort="medium"']
        generated = self.check_launcher(command, result, env,
                                        ['exec', '-C', str(target), *options, '--worktree', '-m', 'test-model', '--json', '-'])
        self.check_launcher(command+['--resume', 'test-session', '--dispatch-id', '/explicit/bash'], result, env,
                            ['exec', *options, '-m', 'test-model', 'resume', 'test-session', '--json', '-'],
                            dispatch_id='/explicit/bash')
        records = json.loads(self.dashboard.DISPATCH_LOG.read_text(encoding='utf-8'))
        self.assertEqual([d['dispatch_id'] for d in records], [generated, '/explicit/bash'])
        self.assertEqual([d['session_id'] for d in records], ['test-session', 'test-session'])
        self.assertEqual(records[-1]['prompt_preview'], 'p' * 4000)
        self.check_dispatch_log_concurrency(command[:-2], '--dispatch-id', env)

    @unittest.skipUnless(os.name == 'nt' and shutil.which('powershell'), 'Windows PowerShell is unavailable')
    def test_windows_powershell_launch_and_live_dispatch(self):
        bin_dir, target, prompt, result, env = self.launcher_fixture()
        (bin_dir/'codex.cmd').write_text('@"%TEST_PYTHON%" "%TEST_STUB%" %*\n', encoding='utf-8')
        (bin_dir/'git.cmd').write_text(
            '@if "%TEST_GIT_ROOT%"=="" (\n  @echo fatal: not a git repository 1>&2\n  @exit /b 128\n)\n'
            '@echo %TEST_GIT_ROOT%\n', encoding='utf-8',
        )
        env['PATH'] = str(bin_dir) + os.pathsep + env['PATH']
        env['TEST_REQUIRE_LIVE'] = '1'
        log = self.dashboard.DISPATCH_LOG
        log.write_text(json.dumps([{'dispatch_id':str(i)} for i in range(200)]), encoding='utf-8')
        command = [shutil.which('powershell'), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                   str(ROOT/'plugin/scripts/run-codex.ps1'), '-TargetPath', str(target), '-PromptFile', str(prompt),
                   '-Model', 'test-model']
        options = ['-s', 'workspace-write', '-c', 'model_reasoning_effort="medium"']
        self.check_launcher(command, result, env,
                            ['exec', '-C', str(target), *options, '--worktree', '-m', 'test-model', '--json', '-'])
        self.check_launcher(command+['-ResumeSessionId', 'test-session', '-DispatchId', 'ps:explicit'], result, env,
                            ['exec', *options, '-m', 'test-model', 'resume', 'test-session', '--json', '-'],
                            dispatch_id='ps:explicit')
        env.update(TEST_GIT_ROOT='', TEST_EXIT='9')
        self.check_launcher(command, result, env,
                            ['exec', '-C', str(target), *options, '--skip-git-repo-check', '-m', 'test-model', '--json', '-'],
                            expected_exit=9)
        entries = json.loads(log.read_text(encoding='utf-8'))
        self.assertEqual(len(entries), 200)
        self.assertEqual(entries[0]['dispatch_id'], '3')
        self.assertEqual([row['session_id'] for row in entries[-3:]], ['test-session'] * 3)
        self.assertEqual(entries[-2]['dispatch_id'], 'ps:explicit')
        self.assertEqual(entries[-1]['status'], 'failed')
        self.assertEqual(entries[-1]['exit_code'], 9)
        self.check_dispatch_log_concurrency(command[:-2], '-DispatchId', env)

    @unittest.skipUnless(shutil.which('node'), 'Node.js is unavailable')
    def test_rich_text_blank_bullets_and_turn_badges(self):
        script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { transformSync } = require('esbuild');
const source = fs.readFileSync('app.ts', 'utf8');
const start = source.indexOf('function esc(');
const end = source.indexOf('function applyIncoming(');
const { code } = transformSync(source.slice(start, end), { loader: 'ts' });
const feed = {};
const context = {
  document: { getElementById: (id) => id === 'chat-feed' ? feed : null, querySelectorAll: () => [] },
  expandedKeys: new Set(), userToggledTraces: new Map(),
  GEMINI_SVG: '', OPENAI_SVG: '', autoFollow: false, searchQuery: '', selectedSid: null,
  pinnedTarget: null, runningAtPinRelease: null,
};
vm.runInNewContext(code, context);
assert.equal(context.formatRichText('hello\n- \nnext'), '<p>hello<br />-<br />next</p>');
assert.equal(context.formatRichText('hello\n* \nnext'), '<p>hello<br />*<br />next</p>');
assert.match(context.formatRichText('hello\n- item\n  - sub\nnext'), /<li class="nested-li">sub<\/li><\/ul><p>next<\/p>/);
for (const [status, label, events] of [
  ['failed', '执行失败', []],
  ['aborted', '已中止', [{kind:'commentary', text:'Started'}]],
]) {
  context.state = {sessions:[{session_id:'s1', status, turns:[{turn_id:'t1', status, events}]}]};
  context.render();
  assert.ok(feed.innerHTML.includes(label));
  assert.doesNotMatch(feed.innerHTML, /正在执行|已完成执行/);
}
'''
        subprocess.run([shutil.which('node'), '-e', script], cwd=ROOT/'ui-src', check=True, timeout=10)

    @unittest.skipUnless(shutil.which('node'), 'Node.js is unavailable')
    def test_ui_focus_and_poll_races(self):
        script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { transformSync } = require('esbuild');
const source = fs.readFileSync('app.ts', 'utf8');
assert.doesNotMatch(source, /\/api\/events/);
const { code } = transformSync(source.replace(/^import \{[\s\S]*?\} from '[^']+';/, '').replace('void boot();', ''), { loader: 'ts' });

function fixture({ web = false, search = '' } = {}) {
  const badge = {}, labels = {}, input = {}, selector = {}, scrolls = [], intervals = [], requests = [];
  const feed = { children: { item: (index) => ({ scrollIntoView: () => scrolls.push(index) }) } };
  const follow = { classList: { toggle() {} }, setAttribute() {} };
  const nodes = {
    'live-pill': { classList: { toggle: (key, value) => { badge[key] = value; } } },
    'live-text': labels, 'side-search': input, 'follow-btn': follow, 'sess-select': selector,
    'chat-feed': feed, 'chat-scroll': { scrollTo: () => scrolls.push('bottom') },
  };
  const bridge = { callServerTool: (args) => new Promise((resolve) => requests.push({ args, resolve })) };
  const window = {};
  window.parent = web ? window : {};
  const context = vm.createContext({
    window, location: { search }, URLSearchParams, App: function () { return bridge; },
    fetch: async (url) => ({ ok: url === '/api/state', json: async () => ({ sessions: [] }) }),
    setInterval: (_fn, delay) => intervals.push(delay),
    document: { getElementById: (id) => nodes[id], querySelectorAll: () => [], documentElement: { setAttribute() {} } },
  });
  vm.runInContext(code, context);
  const read = (expression) => vm.runInContext(expression, context);
  const query = (value) => { input.value = value; input.oninput(); };
  return { read, bridge, query, input, follow, selector, badge, feed, scrolls, intervals, requests };
}

function turn(turn_id, status, dispatch_id) {
  return { turn_id, status, dispatch_id, prompt: `task ${turn_id}`, final_message: '', events: [], files: [] };
}
function session(session_id, project, turns) {
  return { session_id, project, cwd: project, model: 'default', status: turns.at(-1).status, turns };
}

(async () => {
  const f = fixture();
  f.read('wireControls()');
  const send = (sessions, extra = {}) => f.bridge.ontoolresult({ structuredContent: { sessions, ...extra } });
  const stale = f.read('pollNow()');
  const existing = session('existing', 'existing project', [turn('old', 'running')]);
  const queued = session('job', 'cache', [turn('pending', 'running', 'job')]);
  send([existing]);
  f.query('existing');
  send([existing, queued], { focus_session_id: 'existing', focus_dispatch_id: 'job', fingerprint: 'opened' });
  assert.equal(f.read('selectedSid'), 'job');
  assert.equal(f.input.value, '');
  assert.equal(f.read('searchQuery'), '');
  assert.match(f.read('lastSessionTurnKey'), /^job:pending:/);
  f.requests[0].resolve({ structuredContent: { sessions: [existing], fingerprint: 'stale' } });
  await stale;
  assert.equal(f.read('selectedSid'), 'job');
  assert.equal(f.read('lastFingerprint'), 'opened');

  const poll = async (content, isError = false) => {
    const result = f.read('pollNow()');
    const request = f.requests.at(-1);
    request.resolve({ structuredContent: content, isError });
    await result;
    return request.args.arguments;
  };
  const real = session('real', 'cache', [turn('target-turn', 'running', 'job'), turn('other-turn', 'running', 'other')]);
  const focusedArgs = await poll({ sessions: [existing, real], fingerprint: 'migrated' });
  assert.equal(focusedArgs.session_id, 'existing');
  assert.equal(focusedArgs.dispatch_id, 'job');
  assert.equal(f.read('selectedSid'), 'real');
  assert.equal(f.scrolls.at(-1), 0);

  f.query('cache');
  send([existing, real], { focus_dispatch_id: 'job' });
  assert.equal(f.input.value, 'cache');
  f.query('existing');
  await poll({ sessions: [existing, real] });
  assert.equal(f.input.value, 'existing');
  send([existing], { focus_dispatch_id: 'future' });
  assert.equal(f.input.value, 'existing');
  const future = session('future-real', 'other project', [turn('future-turn', 'running', 'future')]);
  await poll({ sessions: [existing, future] });
  assert.equal(f.input.value, '');
  assert.equal(f.read('selectedSid'), 'future-real');

  const done = session('future-real', 'other project', [turn('future-turn', 'aborted', 'future')]);
  await poll({ sessions: [existing, done] });
  assert.equal(f.read('pinnedTarget'), null);
  assert.equal(f.read('selectedSid'), 'future-real');
  await poll({ sessions: [existing, done] });
  assert.equal(f.read('selectedSid'), 'future-real');
  const manual = session('manual', 'CLI', [turn('new', 'running')]);
  const heldArgs = await poll({ sessions: [existing, manual, done] });
  assert.equal(heldArgs.session_id, 'future-real');
  assert.equal(heldArgs.dispatch_id, '');
  assert.equal(f.read('selectedSid'), 'manual');

  send([existing, real], { focus_dispatch_id: 'job' });
  f.selector.value = 'existing';
  f.selector.onchange();
  assert.equal(f.read('pinnedTarget'), null);
  assert.equal(f.read('selectedSid'), 'existing');
  send([existing, real], { focus_dispatch_id: 'job' });
  f.follow.onclick();
  assert.equal(f.read('pinnedTarget'), null);
  assert.equal(f.read('autoFollow'), false);
  const html = f.feed.innerHTML;
  f.bridge.ontoolresult({ isError: true, structuredContent: { sessions: [] } });
  assert.equal(f.badge.offline, true);
  assert.equal(f.read('autoFollow'), false);
  assert.equal(f.read('selectedSid'), 'real');
  assert.equal(f.feed.innerHTML, html);
  await poll({ sessions: [] }, true);
  assert.equal(f.feed.innerHTML, html);
  await poll({ sessions: 'invalid', fingerprint: 'malformed' });
  await poll({ sessions: [{ ...real, turns: [{ ...real.turns[0], events: 'invalid' }] }], fingerprint: 'malformed' });
  assert.equal(f.feed.innerHTML, html);
  assert.notEqual(f.read('lastFingerprint'), 'malformed');
  assert.equal(f.badge.offline, true);

  const demo = fixture({ web: true, search: '?demo=1' });
  await demo.read('boot()');
  assert.equal(demo.read('state.sessions.length'), 2);
  assert.equal(demo.intervals.length, 0);
  const web = fixture({ web: true });
  await web.read('boot()');
  assert.ok(web.intervals.includes(800));
  assert.equal(web.badge.offline, false);
})().catch((error) => { console.error(error); process.exitCode = 1; });
'''
        subprocess.run([shutil.which('node'), '-e', script], cwd=ROOT/'ui-src', check=True, timeout=10)

if __name__ == '__main__': unittest.main()
