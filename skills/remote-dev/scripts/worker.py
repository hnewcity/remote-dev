#!/usr/bin/env python3
"""Read-only probes and managed foreground jobs. Sent to SSH; never installed."""
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time

MAX_BYTES = 64 * 1024 * 1024
ROOT_MARKER = '.agent-remote-root.json'
JOB_MARKER = '.agent-remote-job.json'


def command(args):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=8)
        return p.stdout.strip() if p.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ''


def write_json(path, data):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.replace(path)


def facts(root):
    family = platform.system()
    count = len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else os.cpu_count()
    total = available = None
    model = ''
    identity = ''
    estimate = False
    if family == 'Linux':
        release = dict(line.split('=', 1) for line in read('/etc/os-release').splitlines() if '=' in line)
        system = release.get('PRETTY_NAME', 'Linux').strip('"')
        for line in read('/proc/cpuinfo').splitlines():
            if line.startswith(('model name', 'Hardware')):
                model = line.split(':', 1)[-1].strip()
                break
        mem = {}
        for line in read('/proc/meminfo').splitlines():
            key, value = line.split(':', 1)
            mem[key] = int(value.strip().split()[0]) * 1024
        total, available = mem.get('MemTotal'), mem.get('MemAvailable')
        # Respect common cgroup-v2 limits as well as VM-visible allocations.
        maximum, used = read('/sys/fs/cgroup/memory.max'), read('/sys/fs/cgroup/memory.current')
        if maximum.isdigit() and used.isdigit():
            total = min(total, int(maximum)) if total else int(maximum)
            available = min(available, max(0, int(maximum)-int(used))) if available is not None else None
        quota = read('/sys/fs/cgroup/cpu.max').split()
        if len(quota) == 2 and quota[0].isdigit() and int(quota[1]) > 0:
            count = min(count, max(1, math.ceil(int(quota[0])/int(quota[1]))))
        identity = read('/etc/machine-id') + read('/proc/sys/kernel/random/boot_id')
    elif family == 'Darwin':
        system = 'macOS ' + command(['/usr/bin/sw_vers', '-productVersion'])
        model = command(['/usr/sbin/sysctl', '-n', 'machdep.cpu.brand_string'])
        total_text = command(['/usr/sbin/sysctl', '-n', 'hw.memsize'])
        total = int(total_text) if total_text.isdigit() else None
        vm = command(['/usr/bin/vm_stat'])
        page = re.search(r'page size of (\d+) bytes', vm)
        page_counts = [int(v) for v in re.findall(r'Pages (?:free|inactive|speculative):\s+(\d+)', vm)]
        if page and len(page_counts) == 3:
            available = int(page.group(1)) * sum(page_counts)
            estimate = True
        identity = command(['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid'])
    else:
        system = platform.platform()
    location = Path(root).expanduser()
    while not location.exists() and location != location.parent:
        location = location.parent
    disk = shutil.disk_usage(location).free
    tools = ['python3', 'node', 'npm', 'pnpm', 'go', 'cargo', 'rustc', 'java', 'javac',
             'cmake', 'ninja', 'make', 'docker', 'xcodebuild']
    slots = Path(root).expanduser() / 'slots'
    active_jobs = sum(1 for p in slots.iterdir() if p.name.isdigit() and p.is_dir()) if slots.is_dir() else 0
    return dict(os=family, os_version=system, architecture=platform.machine(), cpu_model=model,
                logical_cpus=count, memory_gib=total/2**30 if total is not None else None,
                available_memory_gib=available/2**30 if available is not None else None,
                memory_available_is_estimate=estimate, free_disk_gib=disk/2**30, active_jobs=active_jobs,
                tools={tool: shutil.which(tool) for tool in tools},
                machine_fingerprint=hashlib.sha256(identity.encode()).hexdigest() if identity else None,
                observed_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))


def relative(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or '\\' in name or '\x00' in name:
        raise ValueError('Unsafe relative path: ' + repr(name))
    if any(part in (ROOT_MARKER, JOB_MARKER, 'job.json', 'command.sh', 'stdout.log', 'stderr.log') for part in p.parts):
        raise ValueError('Reserved job path: ' + name)
    return p


def unpack(data, destination):
    if len(data) > MAX_BYTES:
        raise ValueError('Archive exceeds 64 MiB')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        members = archive.getmembers()
        if len(members) > 50000 or sum(m.size for m in members) > MAX_BYTES:
            raise ValueError('Expanded snapshot exceeds file/size limit')
        seen = set()
        for member in members:
            name = str(relative(member.name))
            if not member.isfile() or name in seen:
                raise ValueError('Snapshot requires unique regular files only')
            seen.add(name)
        for member in members:
            target = destination / str(relative(member.name))
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as output:
                output.write(archive.extractfile(member).read())
            target.chmod(0o700 if member.mode & 0o111 else 0o600)


def managed_root(value, create=False):
    root = Path(value).expanduser()
    if not root.is_absolute() or root.name != 'agent-remote' or root.is_symlink():
        raise ValueError('Workspace must be a dedicated non-symlink directory ending in agent-remote')
    if not root.exists():
        if not create:
            raise ValueError('Managed workspace does not exist')
        root.mkdir(parents=True, mode=0o700)
    marker = root / ROOT_MARKER
    if not marker.exists():
        if not create or any(root.iterdir()):
            raise ValueError('Refusing nonempty/unmarked workspace')
        try:
            with marker.open('x') as f:
                json.dump({'format': 'agent-remote-v1'}, f)
        except FileExistsError:
            pass
    if marker.is_symlink() or json.loads(marker.read_text()).get('format') != 'agent-remote-v1':
        raise ValueError('Invalid workspace ownership marker')
    for name in ('jobs', 'slots'):
        child = root / name
        if child.is_symlink():
            raise ValueError('Managed workspace child is a symlink')
        if create:
            child.mkdir(exist_ok=True, mode=0o700)
    return root


def job_path(root, job_id):
    if not re.fullmatch(r'[0-9a-f]{32}', job_id):
        raise ValueError('Invalid job ID')
    path = root / 'jobs' / job_id
    if path.is_symlink():
        raise ValueError('Job directory is a symlink')
    marker = path / JOB_MARKER
    if marker.is_symlink() or json.loads(marker.read_text()).get('id') != job_id:
        raise ValueError('Unowned job directory')
    return path


def tail(path):
    if not path.exists() or path.is_symlink():
        return ''
    with path.open('rb') as f:
        f.seek(max(0, path.stat().st_size - 6000))
        return f.read(6000).decode(errors='replace')


def execute(request):
    limits = request['limits']
    current = facts(request['root'])
    for field, threshold in [('available_memory_gib', limits['min_free_memory_gib']), ('free_disk_gib', limits['min_free_disk_gib'])]:
        if current[field] is None or current[field] < threshold:
            raise ValueError('Insufficient or unknown ' + field)
    count = limits['max_jobs']
    if not isinstance(count, int) or not 1 <= count <= 32:
        raise ValueError('Invalid job-slot limit')
    if current['active_jobs'] >= count:
        raise ValueError('All managed job slots are busy')
    root = managed_root(request['root'], create=True)
    job_id = request['job_id']
    if not re.fullmatch(r'[0-9a-f]{32}', job_id):
        raise ValueError('Invalid job ID')
    slot = None
    for i in range(count):
        candidate = root / 'slots' / str(i)
        try:
            candidate.mkdir(mode=0o700)
            slot = candidate
            (slot / 'owner').write_text(job_id)
            break
        except FileExistsError:
            pass
    if slot is None:
        raise ValueError('All managed job slots are busy')
    path = root / 'jobs' / job_id
    status = dict(job_id=job_id, state='preparing', exit_code=None)
    try:
        path.mkdir(mode=0o700)
        write_json(path / JOB_MARKER, {'id': job_id})
        write_json(path / 'job.json', status)
        payload = base64.b64decode(request['archive'], validate=True)
        if hashlib.sha256(payload).hexdigest() != request['sha256']:
            raise ValueError('Snapshot digest mismatch')
        unpack(payload, path)
        (path / 'command.sh').write_text(request['command'])
        jobs = max(1, min(int(request['jobs']), current['logical_cpus'] or 1))
        env = os.environ.copy()
        env.update(AGENT_REMOTE_JOB_ID=job_id, CMAKE_BUILD_PARALLEL_LEVEL=str(jobs),
                   CARGO_BUILD_JOBS=str(jobs), OMP_NUM_THREADS=str(jobs), MAKEFLAGS='-j' + str(jobs))
        status.update(state='running', started_at=time.time())
        write_json(path / 'job.json', status)
        with (path / 'stdout.log').open('wb') as stdout, (path / 'stderr.log').open('wb') as stderr:
            proc = subprocess.Popen(['/bin/sh', '-eu', 'command.sh'], cwd=path, env=env,
                                    stdout=stdout, stderr=stderr, start_new_session=True)
            try:
                code = proc.wait(timeout=request['timeout'])
                status.update(state='finished', exit_code=code)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                status.update(state='timed_out', exit_code=124)
        status['finished_at'] = time.time()
        write_json(path / 'job.json', status)
        return dict(status, stdout_tail=tail(path/'stdout.log'), stderr_tail=tail(path/'stderr.log'))
    except Exception as e:
        if (path / JOB_MARKER).exists():
            status.update(state='failed', error=str(e))
            write_json(path / 'job.json', status)
        raise
    finally:
        if slot is not None and (slot/'owner').read_text() == job_id:
            shutil.rmtree(slot)


def handle(request):
    action = request['action']
    if action == 'probe':
        return facts(request['root'])
    if action == 'run':
        return execute(request)
    root = managed_root(request['root'])
    path = job_path(root, request['job_id'])
    status = json.loads((path/'job.json').read_text())
    if action == 'status':
        return dict(status, stdout_tail=tail(path/'stdout.log'), stderr_tail=tail(path/'stderr.log'))
    if status['state'] in ('preparing', 'running'):
        raise ValueError('Job may still be running; inspect before fetching or cleanup')
    if action == 'cleanup':
        shutil.rmtree(path)
        return {'removed_job_id': request['job_id']}
    if action == 'fetch':
        files = []
        total = 0
        for name in request['paths']:
            p = PurePosixPath(name)
            if not name or p.is_absolute() or '..' in p.parts or '\\' in name:
                raise ValueError('Unsafe artifact path')
            current = path
            for part in p.parts:
                current = current / part
                if current.is_symlink():
                    raise ValueError('Artifact symlinks are not allowed')
            if not current.is_file():
                raise ValueError('Artifact is not a regular file: ' + name)
            total += current.stat().st_size
            if total > MAX_BYTES:
                raise ValueError('Artifact download exceeds 64 MiB')
            content = current.read_bytes()
            files.append(dict(path=str(p), sha256=hashlib.sha256(content).hexdigest(),
                              content=base64.b64encode(content).decode()))
        return {'files': files}
    raise ValueError('Unknown action')


if __name__ == '__main__':
    try:
        print(json.dumps({'ok': True, 'result': handle(json.load(sys.stdin))}))
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        sys.exit(1)
