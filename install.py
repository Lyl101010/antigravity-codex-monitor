import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parent
DEFAULT_TARGET = Path.home() / '.gemini/config/plugins/antigravity-codex-monitor'
APP_URI = 'ui://antigravity-codex-monitor/app.html'
PLUGIN_NAME = 'antigravity-codex-monitor'


def run_checked(args):
    return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=12, check=False)


def python_candidates():
    candidates = [[sys.executable]]
    for name in ('python3', 'python'):
        found = shutil.which(name)
        if found:
            candidates.append([found])
    if os.name == 'nt' and shutil.which('py'):
        candidates.append([shutil.which('py'), '-3'])
    for p in (Path.home()/'.local/bin/python3', Path.home()/'.venv/bin/python', Path.home()/'.venv/Scripts/python.exe'):
        if p.is_file():
            candidates.append([str(p)])
    return candidates


def discover_python(explicit=None):
    probe = 'import sys,importlib.metadata; import mcp.server.fastmcp; print(sys.executable); print(importlib.metadata.version("mcp"))'
    for cmd in ([[explicit]] if explicit is not None else python_candidates()):
        try:
            p = run_checked(cmd + ['-c', probe])
            lines = p.stdout.strip().splitlines()
            if p.returncode == 0 and len(lines) >= 2 and Path(lines[-2]).is_file():
                return {'path': os.path.abspath(lines[-2]), 'mcp_version': lines[-1]}
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def doctor(args):
    python = discover_python(args.python)
    codex = shutil.which('codex')
    version = None
    if codex:
        try:
            result = run_checked([codex, '--version'])
            version = result.stdout.strip() or result.stderr.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    conflict = Path.home()/'.gemini/config/skills/codex-worker'
    return {'python':python, 'codex':codex, 'codex_version':version, 'installed':(args.target_dir/'plugin.json').is_file(), 'source_ui_exists':(ROOT/'plugin/ui/app.html').is_file(), 'installed_ui_exists':(args.target_dir/'ui/app.html').is_file(), 'standalone_skill_conflict':conflict.exists(), 'warning':'Standalone codex-worker skill exists; it will not be overwritten.' if conflict.exists() else None}


def check_target(target):
    target = target.expanduser().resolve()
    default_home = (Path.home()/'.codex').resolve()
    codex_home = Path(os.path.abspath(os.path.expanduser(os.environ.get('CODEX_HOME') or default_home))).resolve()
    if (
        target == target.parent or target == Path.home().resolve()
        or target == ROOT or target in ROOT.parents or ROOT in target.parents
        or any(target == home or home in target.parents for home in (default_home, codex_home))
    ):
        raise ValueError('Unsafe target directory')
    return target


def require_owned_plugin(target):
    manifest = target/'plugin.json'
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding='utf-8'))
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get('name') == PLUGIN_NAME:
            return
    raise ValueError(f'Refusing to modify a directory without an owned {PLUGIN_NAME} manifest')


def install(args):
    target = check_target(args.target_dir)
    if target.exists():
        require_owned_plugin(target)
        if not args.force:
            raise FileExistsError('Target exists; use --force to replace it')
    info = doctor(args)
    py = info['python']
    warning = None
    if not py:
        if args.python is not None:
            raise RuntimeError(f'Explicit Python interpreter failed the mcp check: {args.python}')
        if not args.force:
            raise RuntimeError('No Python with mcp found. Run: python -m pip install mcp')
        py = {'path':os.path.abspath(sys.executable), 'mcp_version':None}
        warning = 'Forced installation without a verified mcp interpreter; run python -m pip install mcp.'
    config = {'mcpServers':{'monitor':{'command':py['path'], 'args':['${PLUGIN_ROOT}/server.py']}}}
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=f'.{target.name}-') as staging:
        staged = Path(staging)/'plugin'
        shutil.copytree(ROOT/'plugin', staged, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        (staged/'mcp_config.json').write_text(json.dumps(config, indent=2)+'\n',encoding='utf-8')
        if os.name != 'nt':
            (staged/'scripts/run-codex.sh').chmod(0o755)
        backup = None
        if target.exists():
            require_owned_plugin(target)
            if not args.force:
                raise FileExistsError('Target exists; use --force to replace it')
            backup = Path(staging + '.backup')
        try:
            if backup:
                target.rename(backup)
            staged.rename(target)
        except BaseException:
            if backup and backup.exists() and not target.exists():
                backup.rename(target)
            raise
        if backup:
            shutil.rmtree(backup)
    return {'installed':str(target), 'python':py, 'warning':warning, 'standalone_skill_conflict':info['standalone_skill_conflict']}


def verify(args):
    target = check_target(args.target_dir)
    config = json.loads((target/'mcp_config.json').read_text(encoding='utf-8'))
    entry = config['mcpServers']['monitor']
    command = args.python or entry['command']
    cmd = [command] + [x.replace('${PLUGIN_ROOT}', str(target)) for x in entry['args']]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1)
    responses = queue.Queue()
    def read():
        for line in proc.stdout:
            try:
                responses.put(json.loads(line))
            except json.JSONDecodeError:
                continue
        responses.put(None)
    threading.Thread(target=read, daemon=True).start()
    def send(message):
        proc.stdin.write(json.dumps(message)+'\n')
        proc.stdin.flush()
    def request(number, method, params):
        send({'jsonrpc':'2.0','id':number,'method':method,'params':params})
        while True:
            try:
                item = responses.get(timeout=15)
            except queue.Empty as exc:
                raise RuntimeError('MCP response timed out: '+method) from exc
            if item is None:
                raise RuntimeError('MCP server closed unexpectedly: '+method)
            if item.get('id') == number:
                if 'error' in item:
                    raise RuntimeError(str(item['error']))
                return item['result']
    try:
        request(1,'initialize',{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'monitor-verify','version':'1.0'}})
        send({'jsonrpc':'2.0','method':'notifications/initialized'})
        tools = request(2,'tools/list',{})
        names = {x['name'] for x in tools['tools']}
        if not {'open_codex_monitor','get_codex_state'} <= names:
            raise RuntimeError('Missing MCP tools')
        resource = request(3,'resources/read',{'uri':APP_URI})
        html = resource['contents'][0].get('text','')
        size = len(html.encode('utf-8'))
        if not 0 < size < 10*1024*1024 or '<html' not in html.lower():
            raise RuntimeError('Invalid UI resource')
        return {'verified':True, 'html_bytes':size, 'tools':sorted(names)}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait()
        proc.stdin.close(); proc.stdout.close()


def uninstall(args):
    target = check_target(args.target_dir)
    if target.exists():
        require_owned_plugin(target)
        shutil.rmtree(target)
    return {'uninstalled':str(target)}


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--target-dir', type=Path, default=DEFAULT_TARGET)
    common.add_argument('--python')
    common.add_argument('--json', action='store_true')
    common.add_argument('--force', action='store_true')
    parser = argparse.ArgumentParser(parents=[common])
    subs = parser.add_subparsers(dest='command', required=True)
    for name in ('doctor','install','verify','uninstall'):
        sub = subs.add_parser(name)
        for action in common._actions:
            if action.dest != 'help':
                import copy
                clone = copy.copy(action)
                clone.default = argparse.SUPPRESS
                sub._add_action(clone)
    args = parser.parse_args(argv)
    try:
        result = globals()[args.command](args)
        print(json.dumps(result, ensure_ascii=False, indent=None if args.json else 2))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error':str(exc)}, ensure_ascii=False) if args.json else str(exc), file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())
