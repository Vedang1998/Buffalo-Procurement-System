"""Synthetic PG fixture validation in an ordinary unprivileged container.

Never changes host ownership, cgroups, permissions or production configuration.
This is not a hosted acceptance or native host-capability result.
"""
import hashlib
import json
import os
import re
import signal
import time
import uuid
from pathlib import Path
import subprocess
import sys
import tempfile

if sys.flags.optimize:
    raise SystemExit('Fixture checks require Python assertions enabled')

REPO = Path(__file__).resolve().parents[1]
IMAGE = 'postgres:16@sha256:95206741a5b214807675e14165369d05b93a9cf692223b616d07cca227e74b0b'
if len(sys.argv) != 2 or sys.argv[1] not in {'imports', 'integration'}:
    raise SystemExit('Usage: run-synthetic-postgres-fixtures.py imports|integration')
mode = sys.argv[1]
sys.path.insert(0, str(REPO / 'procurement/tools'))
import hosted_synthetic_source as binding


def source_identity():
    records = binding._tree_records(REPO)
    binding._check_index(REPO, records)
    binding._bind_files(REPO, records)  # Raw bytes, modes and hidden index flags.
    return binding._git(REPO, 'rev-parse', 'HEAD', 'HEAD^{tree}').decode().splitlines()


cancelled = False

def defer_cancellation(signum, frame):
    global cancelled
    cancelled = True


for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
    signal.signal(number, defer_cancellation)

nonce = uuid.uuid4().hex
owned_name = 'buffalo-pg-fixture-' + nonce
label = 'org.buffalo.synthetic-fixture-owner'


def inspect_owned(identifier=None):
    identifier = owned_name if identifier is None else identifier
    result = subprocess.run(['docker', 'container', 'inspect', identifier],
                            capture_output=True, text=True, timeout=15)
    if result.returncode:
        if result.returncode == 1 and any(
            text in result.stderr for text in
            ('No such object: ' + identifier, 'No such container: ' + identifier)
        ):
            return None
        raise RuntimeError('Owned container inventory unavailable')
    records = json.loads(result.stdout)
    if len(records) != 1:
        raise RuntimeError('Container identity differs')
    info = records[0]
    if ((identifier == owned_name and info['Name'] != '/' + owned_name) or
            (identifier != owned_name and info['Id'] != identifier) or
            info['Config'].get('Labels', {}).get(label) != nonce or
            info['Config']['Image'] != IMAGE or info['Config']['User'] != '1000:1000'):
        raise RuntimeError('Foreign container identity refused')
    return info


def attached_run(identifier):
    process = subprocess.Popen(['docker', 'start', '--attach', identifier])
    deadline = time.monotonic() + 300
    try:
        while process.poll() is None:
            if cancelled:
                raise SystemExit(130)
            if time.monotonic() >= deadline:
                raise TimeoutError('Fixture execution deadline expired')
            try:
                process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                pass
        return process.returncode
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


source_binding = source_identity()
def directory_digest(root):
    """Record the actual read-only Python inputs, including relative names."""
    digest = hashlib.sha256()
    for item in sorted(root.rglob('*')):
        if item.is_symlink():
            data = os.readlink(item).encode()
        elif item.is_file():
            data = item.read_bytes()
        else:
            continue
        name = str(item.relative_to(root)).encode()
        digest.update(len(name).to_bytes(8, 'big') + name)
        digest.update(len(data).to_bytes(8, 'big') + data)
    return digest.hexdigest()

python_directories = [Path('/usr/lib/python3.13'), REPO / '.venv/lib/python3.13/site-packages']
python_inputs = {str(path): directory_digest(path) for path in python_directories}
print(json.dumps({'scope':'CONTAINER_FIXTURES_ONLY', 'commit':source_binding[0], 'tree':source_binding[1],
                  'image':IMAGE, 'python_directory_sha256':python_inputs,
                  'native_host_accepted':False}), flush=True)
owned = None
volumes = subprocess.check_output(['docker', 'volume', 'ls', '-q'], text=True, timeout=15).splitlines()
with tempfile.TemporaryDirectory(prefix='buffalo-pg-container-') as temporary:
    directory = Path(temporary)
    (directory / 'passwd').write_text('root:x:0:0:root:/root:/bin/sh\nagent:x:1000:1000:fixture:/tmp:/bin/sh\n')
    (directory / 'group').write_text('root:x:0:\nagent:x:1000:\n')
    runner = directory / 'run.py'
    runner.write_text('''import ctypes, json, os, sys, unittest
sys.path[:0] = ['/src/procurement/src', '/src/procurement/tools', '/src/procurement/tests', '/dependencies']
from procurement_os.synthetic_staging_backup_v2 import _postgres_program
import psycopg, argon2, fastapi
assert os.getuid() == os.geteuid() == 1000
assert ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) == 0
print(json.dumps({'scope':'CONTAINER_FIXTURES_ONLY', 'uid':os.getuid(),
                  'pg_dump':_postgres_program('pg_dump'), 'pg_restore':_postgres_program('pg_restore')}), flush=True)
if sys.argv[1] == 'imports':
    raise SystemExit(0)
names = ['test_synthetic_staging_backup_v2.SyntheticStagingBackupV2Tests',
         'test_synthetic_staging_database.SyntheticStagingDatabasePostgresTests',
         'test_synthetic_staging_price_stage.SyntheticStagingPriceStagePostgresTests',
         'test_synthetic_staging_transfer.SyntheticStagingTransferTests']
suite = unittest.defaultTestLoader.loadTestsFromNames(names)
discovered = suite.countTestCases()
assert discovered == 50
result = unittest.TextTestRunner(verbosity=2).run(suite)
reaped = 0
while True:
    try:
        pid, status = os.waitpid(-1, os.WNOHANG)
    except ChildProcessError:
        break
    if pid == 0:
        raise RuntimeError('Owned fixture descendant remains live')
    reaped += 1
print(json.dumps({'discovered':discovered, 'executed':result.testsRun,
                  'failures':len(result.failures), 'error_events':len(result.errors),
                  'skips':len(result.skipped), 'expected_failures':len(result.expectedFailures),
                  'unexpected_successes':len(result.unexpectedSuccesses), 'reaped':reaped}), flush=True)
raise SystemExit(0 if result.wasSuccessful() and result.testsRun == discovered and not result.skipped and not result.expectedFailures and not result.unexpectedSuccesses else 1)
''')
    mounts = [(REPO, '/src'), (REPO / '.venv/lib/python3.13/site-packages', '/dependencies'),
              (Path('/usr/bin/python3.13'), '/usr/bin/python3.13'),
              (Path('/usr/lib/python3.13'), '/usr/lib/python3.13'),
              (directory / 'passwd', '/etc/passwd'), (directory / 'group', '/etc/group'),
              (runner, '/run-fixtures.py')]
    # Enumerate actual root-owned loader dependencies of the read-only software
    # inputs. No image PostgreSQL tool is replaced and no host state is writable.
    binaries = [Path('/usr/bin/python3.13'), Path('/usr/bin/git'),
                *Path('/usr/lib/python3.13/lib-dynload').glob('*.so')]
    libraries = {}
    for binary in binaries:
        linked = subprocess.check_output(['ldd', str(binary)], text=True, timeout=10)
        for raw in re.findall(r'(?:=> )?(/[^\s]+) \(', linked):
            source = Path(raw).resolve(strict=True)
            info = source.stat()
            assert info.st_uid == 0 and not info.st_mode & 0o022
            target = raw.replace('/lib/', '/usr/lib/', 1) if raw.startswith('/lib/') else raw
            libraries[target] = source
    mounts.append((Path('/usr/bin/git'), '/usr/bin/git'))
    mounts.extend((source, target) for target, source in sorted(libraries.items()))
    print(json.dumps({'software_inputs':
        {target: hashlib.sha256(item.read_bytes()).hexdigest()
         for item, target in mounts if item.is_file()}}, sort_keys=True), flush=True)
    command = ['docker', 'create', '--name', owned_name, '--label', label + '=' + nonce, '--network', 'none', '--read-only', '--user', '1000:1000',
               '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
               '--memory', '2g', '--pids-limit', '128',
               '--tmpfs', '/tmp:rw,nosuid,nodev,size=1g,mode=1777',
               '--tmpfs', '/var/lib/postgresql/data:rw,nosuid,nodev,size=1m,mode=700,uid=1000,gid=1000',
               '--env', 'PATH=/usr/lib/postgresql/16/bin:/usr/bin:/bin',
               '--env', 'LANG=C.UTF-8', '--workdir', '/src', '--entrypoint', '/src/.venv/bin/python']
    for source, target in mounts:
        command += ['--mount', f'type=bind,src={source},dst={target},readonly']
    command += [IMAGE, '-I', '-S', '-B', '/run-fixtures.py', mode]
    if cancelled:
        raise SystemExit(130)
    if inspect_owned() is not None:
        raise RuntimeError('Container name already exists')
    create_acknowledged = False
    try:
        owned = subprocess.check_output(command, text=True, timeout=30).strip()
        assert len(owned) == 64 and all(c in '0123456789abcdef' for c in owned)
        create_acknowledged = True
        info = inspect_owned(owned)
        assert info is not None and info['Id'] == owned
        if cancelled:
            raise SystemExit(130)
        assert info['Config']['User'] == '1000:1000' and not info['HostConfig']['Privileged']
        assert info['HostConfig']['NetworkMode'] == 'none' and info['HostConfig']['ReadonlyRootfs']
        assert all(not mount['RW'] for mount in info['Mounts'] if mount['Type'] == 'bind')
        result = attached_run(owned)
        state = inspect_owned(owned)['State']
        print(json.dumps({'container_exit':state['ExitCode'], 'oom_killed':state['OOMKilled'],
                          'scope':'CONTAINER_FIXTURES_ONLY', 'native_host_accepted':False}), flush=True)
        assert state['Status'] == 'exited' and not state['Running']
        code = state['ExitCode'] if not state['OOMKilled'] and result == state['ExitCode'] else 1
    finally:
        # The identity exists before create. Reconcile even if create output was
        # lost; signals only set a flag until bounded resource cleanup finishes.
        cleanup_errors = []
        try:
            identifier = owned if create_acknowledged else None
            info = inspect_owned(identifier)
            if info is not None:
                subprocess.run(['docker', 'rm', '--force', '-v', info['Id']], check=True,
                               stdout=subprocess.DEVNULL, timeout=30)
            if inspect_owned(identifier) is not None:
                raise RuntimeError('Owned container remains after removal')
            if create_acknowledged and inspect_owned() is not None:
                raise RuntimeError('Container name replacement preserved')
            if not create_acknowledged:
                # An unacknowledged daemon request cannot prove no late create.
                raise RuntimeError('Create outcome ambiguous; cleanup is not certified')
            print('Exact owned container absence verified', flush=True)
        except BaseException as error:
            cleanup_errors.append(error)
        for check in (
            lambda: sorted(volumes) == sorted(subprocess.check_output(
                ['docker', 'volume', 'ls', '-q'], text=True, timeout=15).splitlines()),
            lambda: source_identity() == source_binding,
            lambda: {str(path): directory_digest(path) for path in python_directories} == python_inputs,
        ):
            try:
                if not check():
                    raise RuntimeError('Post-run source/software/volume inventory differs')
            except BaseException as error:
                cleanup_errors.append(error)
        if cleanup_errors:
            raise RuntimeError('Fixture cleanup or evidence verification failed') from cleanup_errors[0]
raise SystemExit(130 if cancelled else code)
