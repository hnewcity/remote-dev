#!/usr/bin/env python3
"""Agent-guided SSH enrollment and bounded development task execution."""
import argparse
import base64
from contextlib import contextmanager
import fnmatch
import glob
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

TASKS = {'build', 'test', 'analysis'}
MAX_BYTES = 64 * 1024 * 1024
DEFAULT_ROOT = '~/.cache/agent-remote'
SSH_OPTIONS = ['-o', 'BatchMode=yes', '-o', 'PasswordAuthentication=no',
               '-o', 'KbdInteractiveAuthentication=no', '-o', 'PreferredAuthentications=publickey',
               '-o', 'StrictHostKeyChecking=yes', '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no',
               '-o', 'ClearAllForwardings=yes', '-o', 'PermitLocalCommand=no', '-o', 'RemoteCommand=none',
               '-o', 'ControlMaster=no', '-o', 'ControlPath=none', '-o', 'ConnectTimeout=8',
               '-o', 'ConnectionAttempts=1', '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=2']


def timestamp():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def run_local(args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if p.returncode:
        raise ValueError(p.stderr.strip() or 'Command failed: ' + args[0])
    return p.stdout


def safe_alias(alias):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', alias):
        raise ValueError('Use an explicit SSH Host alias, not a pattern or command')
    return alias


def effective(alias):
    output = run_local(['ssh', '-G', *SSH_OPTIONS, safe_alias(alias)])
    fields = {}
    for line in output.splitlines():
        key, _, value = line.partition(' ')
        fields.setdefault(key, []).append(value)
    proxy = fields.get('proxycommand', [''])[0]
    return dict(hostname=fields['hostname'][0], user=fields['user'][0], port=int(fields['port'][0]),
                identity_files=fields.get('identityfile', []), proxy_jump=fields.get('proxyjump', [''])[0],
                identity_agent=fields.get('identityagent', [''])[0],
                proxy_command_sha256=hashlib.sha256(proxy.encode()).hexdigest() if proxy else '')


def discover_files(paths):
    visited, aliases = set(), set()
    def visit(path, base):
        path = Path(path).expanduser().resolve()
        if path in visited or not path.is_file():
            return
        visited.add(path)
        if len(visited) > 100:
            raise ValueError('SSH Include limit exceeded')
        for line in path.read_text().splitlines():
            parts = shlex.split(line, comments=True)
            if not parts:
                continue
            keyword = parts[0].lower()
            values = parts[1:]
            if '=' in keyword:
                keyword, first = keyword.split('=', 1)
                values.insert(0, first)
            if keyword == 'host':
                aliases.update(x for x in values if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', x))
            elif keyword == 'include':
                for pattern in values:
                    pattern = os.path.expanduser(pattern)
                    if not os.path.isabs(pattern):
                        pattern = str(base / pattern)
                    for child in sorted(glob.glob(pattern)):
                        visit(child, base)
    for path in paths:
        visit(path, Path(path).parent)
    return sorted(aliases)


def validate(registry):
    if not isinstance(registry, dict) or registry.get('version') != 1 or not isinstance(registry.get('hosts'), list):
        raise ValueError('Invalid registry; expected version 1 and hosts array')
    seen = set()
    for host in registry['hosts']:
        if not isinstance(host, dict):
            raise ValueError('Host must be an object')
        alias = safe_alias(host.get('alias', ''))
        if alias in seen:
            raise ValueError('Duplicate host alias')
        seen.add(alias)
        if type(host.get('enabled')) is not bool or type(host.get('allow_source_sync')) is not bool:
            raise ValueError('Host enable/source-sync fields must be booleans')
        if host.get('decision') not in ('pending', 'allowed', 'denied'):
            raise ValueError('Invalid enrollment decision')
        tasks = host.get('allowed_tasks')
        if not isinstance(tasks, list) or any(t not in TASKS for t in tasks):
            raise ValueError('Invalid allowed_tasks')
        if not isinstance(host.get('constraints'), list) or any(not isinstance(c, str) for c in host['constraints']):
            raise ValueError('constraints must be a string array')
        limits = host.get('limits', {})
        if type(limits.get('max_jobs')) is not int or not 1 <= limits['max_jobs'] <= 32:
            raise ValueError('max_jobs must be an integer from 1 to 32')
        for key in ('min_free_memory_gib', 'min_free_disk_gib'):
            value = limits.get(key)
            if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                raise ValueError('Invalid resource floor: ' + key)
        root = host.get('workspace_root', '')
        if not isinstance(root, str) or not (root.startswith('/') or root.startswith('~/')) or PurePosixPath(root).name != 'agent-remote' or '..' in PurePosixPath(root).parts:
            raise ValueError('workspace_root must be a dedicated path ending in agent-remote')
        if host['enabled'] and (host['decision'] != 'allowed' or not host.get('approved_at') or not host.get('connection')):
            raise ValueError('Enabled host lacks a recorded allow decision or SSH binding')
    return registry


def load(path):
    if not path.is_file():
        raise ValueError('No host registry. Run init and guided enrollment; remote execution is disabled.')
    if path.is_symlink():
        raise ValueError('Registry must be a regular local file, not a symlink')
    return validate(json.loads(path.read_text()))


def save(path, registry):
    validate(registry)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.hosts-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(registry, f, ensure_ascii=False, indent=2)
            f.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def registry_lock(path):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def persist_facts(path, updates):
    # A refresh must never restore permissions revoked while its SSH probe ran.
    with registry_lock(path):
        current = load(path)
        for host in current['hosts']:
            update = updates.get(host['alias'])
            if update and host['connection'] == update['connection']:
                host['facts'] = update['facts']
        save(path, current)
        return current


def entry(alias):
    return dict(alias=safe_alias(alias), enabled=False, decision='pending', allowed_tasks=[],
                allow_source_sync=False, constraints=[], workspace_root=DEFAULT_ROOT,
                limits=dict(max_jobs=1, min_free_memory_gib=2, min_free_disk_gib=10),
                connection={}, facts={}, approved_at=None)


def approved(registry, alias, task=None, sync=False):
    host = next((h for h in registry['hosts'] if h['alias'] == alias), None)
    if not host or not host['enabled'] or host['decision'] != 'allowed':
        raise ValueError('Host is not explicitly enabled and allowed: ' + alias)
    if task and task not in host['allowed_tasks']:
        raise ValueError('Task type is not approved for this host')
    if sync and not host['allow_source_sync']:
        raise ValueError('Source transfer is not approved for this host')
    if effective(alias) != host['connection']:
        raise ValueError('SSH binding changed; user re-enrollment is required')
    return host


def rpc(alias, request, timeout=40):
    source = Path(__file__).with_name('worker.py').read_text()
    remote = 'python3 -c ' + shlex.quote(source)
    try:
        # OpenSSH propagates -F to ProxyJump children. Shared constraints go first.
        with tempfile.TemporaryDirectory(prefix='agent-remote-ssh-') as directory:
            wrapper = Path(directory) / 'config'
            lines = ['Host *'] + ['  ' + SSH_OPTIONS[i].replace('=', ' ', 1) for i in range(1, len(SSH_OPTIONS), 2)]
            for include in [Path.home()/'.ssh/config', Path('/etc/ssh/ssh_config')]:
                if include.is_file():
                    lines.append('Include "' + str(include).replace('"', '\\"') + '"')
            wrapper.write_text('\n'.join(lines) + '\n')
            wrapper.chmod(0o600)
            p = subprocess.run(['ssh', '-T', '-F', str(wrapper), safe_alias(alias), remote],
                               input=json.dumps(request), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ValueError('SSH request timed out; submitted job completion is unknown. Inspect its job ID before retrying.')
    try:
        response = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise ValueError('SSH/remote Python failed: ' + p.stderr.strip()[-1500:])
    if not response.get('ok'):
        raise ValueError(response.get('error', 'Remote request failed'))
    if p.returncode:
        raise ValueError('SSH ended unsuccessfully; job completion may be unknown')
    return response['result']


def inspect_host(host):
    return rpc(host['alias'], {'action': 'probe', 'root': host['workspace_root']})


def rejection(host, facts, args):
    reasons = []
    if facts.get('active_jobs', 0) >= host['limits']['max_jobs']:
        reasons.append('All managed job slots are busy')
    if args.os and facts.get('os') != args.os:
        reasons.append('OS mismatch')
    if args.arch and facts.get('architecture') != args.arch:
        reasons.append('Architecture mismatch')
    for name in filter(None, args.tools.split(',')):
        if not facts.get('tools', {}).get(name):
            reasons.append('Missing tool: ' + name)
    for key, floor, requested in [('available_memory_gib', 'min_free_memory_gib', args.memory_gib),
                                   ('free_disk_gib', 'min_free_disk_gib', args.disk_gib)]:
        value = facts.get(key)
        if value is None or value < max(host['limits'][floor], requested):
            reasons.append('Insufficient or unknown ' + key)
    return reasons


def snapshot(repo, output):
    repo = Path(repo).resolve()
    actual_root = Path(run_local(['git', '-C', str(repo), 'rev-parse', '--show-toplevel']).strip()).resolve()
    if actual_root != repo:
        raise ValueError('Pass the Git repository root explicitly; refusing to expand source scope')
    stage = run_local(['git', '-C', str(repo), 'ls-files', '--stage', '-z'])
    if any(item.startswith('160000 ') for item in stage.split('\x00')):
        raise ValueError('Submodules require an explicit materialization/transfer plan')
    names = sorted(set(filter(None, run_local(['git', '-C', str(repo), 'ls-files', '-z', '--cached', '--others', '--exclude-standard']).split('\x00'))))
    omitted, included, size = [], [], 0
    buf = io.BytesIO()
    secret_parts = {'.git', '.ssh', '.aws', '.azure', '.gcloud', 'node_modules', '.venv', 'venv', '__pycache__'}
    patterns = ['.env', '.env.*', '*.pem', '*.key', '*.p12', '*.pfx', '.npmrc', '.pypirc', '.netrc', 'id_rsa*', 'id_ed25519*', 'credentials', 'credentials.json']
    from worker import relative
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for name in names:
            pure = PurePosixPath(name)
            path = repo / name
            if any(part in secret_parts for part in pure.parts) or any(fnmatch.fnmatch(pure.name, pat) for pat in patterns):
                omitted.append({'path': name, 'reason': 'credential/cache exclusion'})
                continue
            current = repo
            symlink = False
            for part in pure.parts:
                current /= part
                symlink = symlink or current.is_symlink()
            if symlink or not path.is_file():
                omitted.append({'path': name, 'reason': 'symlink/deleted/non-file'})
                continue
            relative(name)
            size += path.stat().st_size
            if size > MAX_BYTES:
                raise ValueError('Snapshot exceeds 64 MiB; narrow the source transfer plan')
            data = path.read_bytes()
            if data.startswith(b'version https://git-lfs.github.com/spec/v1'):
                raise ValueError('Git LFS pointer needs explicit materialization: ' + name)
            if re.search(br'-----BEGIN [A-Z ]*PRIVATE KEY-----', data):
                omitted.append({'path': name, 'reason': 'private-key header'})
                continue
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o700 if path.stat().st_mode & 0o111 else 0o600
            tar.addfile(info, io.BytesIO(data))
            included.append(name)
    payload = buf.getvalue()
    if len(payload) > MAX_BYTES or len(included) > 50000:
        raise ValueError('Snapshot exceeds transfer limits')
    output = Path(output)
    manifest_path = Path(str(output) + '.manifest.json')
    if output.exists() or manifest_path.exists():
        raise ValueError('Snapshot destination already exists')
    try:
        head = run_local(['git', '-C', str(repo), 'rev-parse', 'HEAD']).strip()
    except ValueError:
        head = None
    manifest = dict(source_root=str(repo), git_head=head,
                    dirty=bool(run_local(['git', '-C', str(repo), 'status', '--porcelain'])),
                    included=included, omitted=omitted, size_bytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(), created_at=timestamp())
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as f:
        f.write(payload)
    output.chmod(0o600)
    with manifest_path.open('x') as f:
        json.dump(manifest, f, indent=2)
    manifest_path.chmod(0o600)
    return dict(archive=str(output), manifest=str(manifest_path), **manifest)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', default=os.environ.get('AGENT_REMOTE_CONFIG', '~/.config/agent-remote/hosts.json'))
    sub = p.add_subparsers(dest='action', required=True)
    for name in ('init', 'list', 'discover'):
        sub.add_parser(name)
    probe = sub.add_parser('probe'); probe.add_argument('--host', required=True); probe.add_argument('--root', default=DEFAULT_ROOT)
    refresh = sub.add_parser('refresh'); refresh.add_argument('--host', required=True)
    allow = sub.add_parser('allow')
    allow.add_argument('--host', required=True); allow.add_argument('--tasks', required=True)
    allow.add_argument('--source-sync', action='store_true'); allow.add_argument('--root', default=DEFAULT_ROOT)
    allow.add_argument('--constraints', action='append', default=[])
    allow.add_argument('--max-jobs', type=int, default=1)
    allow.add_argument('--memory-gib', type=float, default=2); allow.add_argument('--disk-gib', type=float, default=10)
    deny = sub.add_parser('deny'); deny.add_argument('--host', required=True); deny.add_argument('--reason', required=True)
    select = sub.add_parser('select'); select.add_argument('--task', choices=sorted(TASKS), required=True)
    select.add_argument('--os'); select.add_argument('--arch'); select.add_argument('--tools', default='')
    select.add_argument('--memory-gib', type=float, default=0); select.add_argument('--disk-gib', type=float, default=0)
    snap = sub.add_parser('snapshot'); snap.add_argument('--repo', required=True); snap.add_argument('--output', required=True)
    execute = sub.add_parser('run'); execute.add_argument('--host', required=True); execute.add_argument('--task', choices=sorted(TASKS), required=True)
    execute.add_argument('--snapshot', required=True); execute.add_argument('--command-file', required=True)
    execute.add_argument('--timeout', type=int, default=900); execute.add_argument('--jobs', type=int, default=1)
    execute.add_argument('--memory-gib', type=float, default=0); execute.add_argument('--disk-gib', type=float, default=0)
    execute.add_argument('--os'); execute.add_argument('--arch'); execute.add_argument('--tools', default='')
    for name in ('status', 'fetch', 'cleanup'):
        item = sub.add_parser(name); item.add_argument('--host', required=True); item.add_argument('--job', required=True)
        if name == 'fetch':
            item.add_argument('--paths', required=True); item.add_argument('--output', required=True)
    return p


def main(args):
    config = Path(args.config).expanduser()
    for name in ('memory_gib', 'disk_gib'):
        value = getattr(args, name, 0)
        if not math.isfinite(value) or value < 0:
            raise ValueError('Resource requirements must be finite and nonnegative')
    if args.action == 'init':
        with registry_lock(config):
            if config.exists():
                return {'created': False, 'config': str(config), 'registry': load(config)}
            save(config, {'version': 1, 'hosts': []})
        return {'created': True, 'config': str(config), 'enabled_hosts': 0}
    if args.action == 'discover':
        previous = {h['alias']: h for h in load(config)['hosts']} if config.exists() else {}
        candidates = []
        for alias in discover_files([Path.home()/'.ssh/config', Path('/etc/ssh/ssh_config')]):
            try:
                binding = effective(alias)
                keys = [key for key in binding['identity_files'] if Path(key.replace('%d', str(Path.home()))).expanduser().is_file()]
                candidates.append(dict(alias=alias, connection=binding, existing_identity_files=keys,
                                       decision=previous.get(alias, {}).get('decision', 'pending')))
            except Exception as e:
                candidates.append(dict(alias=alias, error=str(e)))
        return {'candidates': candidates, 'connected_to_hosts': False}
    if args.action == 'probe':
        safe_alias(args.host)
        return rpc(args.host, {'action': 'probe', 'root': args.root})
    if args.action == 'snapshot':
        return snapshot(args.repo, args.output)
    registry = load(config)
    if args.action == 'list':
        return registry
    if args.action in ('allow', 'deny'):
        host = next((h for h in registry['hosts'] if h['alias'] == args.host), None)
        if host is None:
            host = entry(args.host); registry['hosts'].append(host)
        if args.action == 'deny':
            host.update(enabled=False, decision='denied', allowed_tasks=[], allow_source_sync=False,
                        constraints=[args.reason], approved_at=None)
        else:
            tasks = list(filter(None, args.tasks.split(',')))
            if not tasks or any(task not in TASKS for task in tasks):
                raise ValueError('Choose nonempty tasks from build,test,analysis')
            host.update(enabled=True, decision='allowed', allowed_tasks=tasks, allow_source_sync=args.source_sync,
                        workspace_root=args.root, constraints=args.constraints, approved_at=timestamp(),
                        limits=dict(max_jobs=args.max_jobs, min_free_memory_gib=args.memory_gib, min_free_disk_gib=args.disk_gib),
                        connection=effective(args.host))
        with registry_lock(config):
            current = load(config)
            current['hosts'] = [h for h in current['hosts'] if h['alias'] != host['alias']] + [host]
            save(config, current)
        return host
    if args.action == 'select':
        candidates, rejected = [], []
        fact_updates = {}
        seen_machines = set()
        for row in registry['hosts']:
            if not row['enabled'] or row['decision'] != 'allowed' or args.task not in row['allowed_tasks'] or not row['allow_source_sync']:
                continue
            try:
                host = approved(registry, row['alias'], args.task, sync=True)
                measured = inspect_host(host)
                host['facts'] = measured
                fact_updates[host['alias']] = {'connection': host['connection'], 'facts': measured}
                reasons = rejection(host, measured, args)
                fingerprint = measured.get('machine_fingerprint')
                if fingerprint and fingerprint in seen_machines:
                    reasons.append('Same machine as another compatible alias')
                if reasons:
                    rejected.append({'alias': host['alias'], 'reasons': reasons})
                else:
                    if fingerprint:
                        seen_machines.add(fingerprint)
                    candidates.append({'alias': host['alias'], 'facts': measured, 'constraints': host['constraints'], 'limits': host['limits']})
            except Exception as e:
                rejected.append({'alias': row['alias'], 'reasons': [str(e)]})
        latest = persist_facts(config, fact_updates)
        eligible = {h['alias'] for h in latest['hosts'] if h['enabled'] and h['decision'] == 'allowed' and args.task in h['allowed_tasks'] and h['allow_source_sync']}
        candidates = [c for c in candidates if c['alias'] in eligible]
        candidates.sort(key=lambda c: (c['facts'].get('available_memory_gib') or 0, c['facts'].get('logical_cpus') or 0), reverse=True)
        return {'candidates': candidates, 'rejected': rejected, 'remote_execution_possible': bool(candidates)}
    host = approved(registry, args.host, getattr(args, 'task', None), sync=args.action == 'run')
    if args.action == 'refresh':
        measured = inspect_host(host)
        latest = persist_facts(config, {host['alias']: {'connection': host['connection'], 'facts': measured}})
        return next(h for h in latest['hosts'] if h['alias'] == host['alias'])
    if args.action == 'run':
        if not 1 <= args.timeout <= 3600 or not 1 <= args.jobs <= 256:
            raise ValueError('Timeout must be 1–3600 seconds; jobs must be 1–256')
        measured = inspect_host(host)
        reasons = rejection(host, measured, args)
        if reasons:
            raise ValueError('; '.join(reasons))
        archive = Path(args.snapshot)
        manifest = json.loads(Path(str(archive) + '.manifest.json').read_text())
        if archive.stat().st_size > MAX_BYTES:
            raise ValueError('Snapshot exceeds 64 MiB')
        payload = archive.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != manifest['sha256']:
            raise ValueError('Snapshot changed since manifest review')
        command = Path(args.command_file).read_text()
        if not command.strip() or len(command.encode()) > 128 * 1024:
            raise ValueError('Command must be nonempty and at most 128 KiB')
        # Recheck permission immediately before submission, after potentially slow preflight work.
        host = approved(load(config), args.host, args.task, sync=True)
        job_id = uuid.uuid4().hex
        print(json.dumps({'submitting_job_id': job_id, 'host': args.host}), file=sys.stderr, flush=True)
        limits = dict(host['limits'])
        limits['min_free_memory_gib'] = max(limits['min_free_memory_gib'], args.memory_gib)
        limits['min_free_disk_gib'] = max(limits['min_free_disk_gib'], args.disk_gib)
        return rpc(args.host, dict(action='run', root=host['workspace_root'], job_id=job_id, limits=limits,
                                   archive=base64.b64encode(payload).decode(), sha256=digest,
                                   command=command, timeout=args.timeout, jobs=args.jobs), timeout=args.timeout+60)
    request = dict(action=args.action, root=host['workspace_root'], job_id=args.job)
    if args.action == 'fetch':
        destination = Path(args.output)
        if destination.exists():
            raise ValueError('Results destination must not already exist')
        request['paths'] = list(filter(None, args.paths.split(',')))
        if not request['paths']:
            raise ValueError('Specify exact artifact paths')
        result = rpc(args.host, request)
        validated, seen = [], set()
        total = 0
        for item in result['files']:
            p = PurePosixPath(item['path'])
            if p.is_absolute() or '..' in p.parts or '\\' in item['path'] or not p.parts or str(p) in seen:
                raise ValueError('Unsafe or duplicate downloaded path')
            seen.add(str(p))
            content = base64.b64decode(item['content'], validate=True)
            total += len(content)
            if total > MAX_BYTES or hashlib.sha256(content).hexdigest() != item['sha256']:
                raise ValueError('Invalid artifact size/digest')
            validated.append((p, content))
        if seen != {str(PurePosixPath(name)) for name in request['paths']}:
            raise ValueError('Remote artifacts differ from the requested file list')
        destination.mkdir(parents=True, exist_ok=False)
        for p, content in validated:
            target = destination / str(p)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as f:
                f.write(content)
            target.chmod(0o600)
        return {'output': str(destination), 'files': [str(p) for p, _ in validated]}
    return rpc(args.host, request)


if __name__ == '__main__':
    try:
        parsed = parser().parse_args()
        result = main(parsed)
        print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False, indent=2))
        if parsed.action == 'run' and result.get('exit_code') != 0:
            sys.exit(1)
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)
