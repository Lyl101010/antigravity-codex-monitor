from contextlib import closing
import importlib.util
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
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

    def write_rollout(self, session_id, *events):
        folder = self.path/'sessions'/datetime.now().strftime('%Y/%m/%d')
        folder.mkdir(parents=True, exist_ok=True)
        transcript = folder/f'rollout-{session_id}.jsonl'
        header = ('2026-11-26T10:00:00Z', 'session_meta', {'id':session_id, 'source':'exec'})
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
            dispatches.append({'session_id':sid, 'started_at':start, 'status':'running'})
        self.dashboard.DISPATCH_LOG.write_text(json.dumps(dispatches), encoding='utf-8')
        sessions = {s['session_id']:s for s in self.dashboard.build_state()['sessions']}
        for idx, (expected, _) in enumerate(outcomes):
            session = sessions[f's{idx}']
            turn = session['turns'][0]
            with self.subTest(session=idx):
                self.assertEqual(session['status'], expected)
                self.assertEqual(turn['status'], expected)
                self.assertEqual(turn['duration_s'], 25)

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
                              dict(failure, session_id='s1', started_at=second),
                              {'dispatch_id':'before-start', 'started_at':second, 'status':'failed'}]
                self.dashboard.DISPATCH_LOG.write_text(json.dumps(dispatches), encoding='utf-8')
                sessions = {s['session_id']:s for s in self.dashboard.build_state()['sessions']}
                self.assertEqual(sessions['s1']['status'], 'failed')
                self.assertEqual([t['status'] for t in sessions['s1']['turns']], ['completed', 'failed'])
                self.assertEqual(sessions['before-start']['turns'][0]['status'], 'failed')

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
        with patch.object(installer, 'ROOT', self.path/'source'), patch.dict(
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
        self.assertEqual(manifest['version'],'1.0.0')
        ps = (ROOT/'plugin/scripts/run-codex.ps1').read_text(encoding='utf-8')
        sh = (ROOT/'plugin/scripts/run-codex.sh').read_text(encoding='utf-8')
        self.assertNotRegex(ps, r'File\]::Replace\([^\n]*,\s*\$null\s*\)')
        self.assertRegex(ps, r'File\]::Replace\([^\n]*,\s*\[NullString\]::Value\)')
        self.assertRegex(ps, r'if \(-not \$ResumeSessionId[^)\n]*\)[^\n]*--worktree')
        self.assertRegex(sh, r'(?m)^if .* -z "\$resume" .*--worktree')
        self.assertNotRegex(sh, r'(?m)^\s*export\b[^\n]*\bMONITOR_PROMPT\b')
        forbidden = ['liyi'+'lin','App_'+'Downloads','codex_'+'to_ag','antigravity-'+'official.png','<meta name='+'"generator"']
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
print(json.dumps({"type":"thread.started", "thread_id":"test-session"}), flush=True)
if os.environ.get("TEST_REQUIRE_LIVE") == "1":
    log = pathlib.Path(os.environ["CODEX_HOME"])/"codex-worker-dispatches.json"
    for _ in range(100):
        try:
            rows = json.loads(log.read_text(encoding="utf-8-sig"))
        except OSError:
            rows = []
        if rows and rows[-1]["session_id"] == "test-session" and rows[-1]["status"] == "running":
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

    def check_launcher(self, command, result, env, expected_args, expected_exit=0):
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
        command = [shutil.which('bash'), '-c',
                   'export PATH="$(CDPATH= cd "$TEST_BIN" && pwd -P):$PATH" CDPATH=.; '
                   'exec bash plugin/scripts/run-codex.sh "$@"',
                   'launcher', '--target-path', target.relative_to(ROOT).as_posix(), '--prompt-file', prompt.as_posix(),
                   '--model', 'test-model']
        options = ['-s', 'workspace-write', '-c', 'model_reasoning_effort="medium"']
        self.check_launcher(command, result, env,
                            ['exec', '-C', str(target), *options, '--worktree', '-m', 'test-model', '--json', '-'])
        self.check_launcher(command+['--resume', 'test-session'], result, env,
                            ['exec', *options, '-m', 'test-model', 'resume', 'test-session', '--json', '-'])
        records = json.loads(self.dashboard.DISPATCH_LOG.read_text(encoding='utf-8'))
        self.assertEqual([d['session_id'] for d in records], ['test-session', 'test-session'])
        self.assertEqual(records[-1]['prompt_preview'], 'p' * 4000)

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
        self.check_launcher(command+['-ResumeSessionId', 'test-session'], result, env,
                            ['exec', *options, '-m', 'test-model', 'resume', 'test-session', '--json', '-'])
        env.update(TEST_GIT_ROOT='', TEST_EXIT='9')
        self.check_launcher(command, result, env,
                            ['exec', '-C', str(target), *options, '--skip-git-repo-check', '-m', 'test-model', '--json', '-'],
                            expected_exit=9)
        entries = json.loads(log.read_text(encoding='utf-8'))
        self.assertEqual(len(entries), 200)
        self.assertEqual(entries[0]['dispatch_id'], '3')
        self.assertEqual([row['session_id'] for row in entries[-3:]], ['test-session'] * 3)
        self.assertEqual(entries[-1]['status'], 'failed')
        self.assertEqual(entries[-1]['exit_code'], 9)

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

if __name__ == '__main__': unittest.main()
