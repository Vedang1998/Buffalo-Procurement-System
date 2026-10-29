"""Review-gated native bootstrap for the ephemeral GitHub acceptance VM.

This module is always unprivileged. Only maintained system binaries appear in
its privileged command prefix. CLI execution is deliberately unavailable until
independent review of this exact delta; neither argv nor environment can open
that gate. Planning and deterministic tests grant no live execution authority.
"""
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hosted_synthetic_source import _resource_operation
import hosted_synthetic_launcher as launcher
import hosted_synthetic_namespace as ns


_ENVIRONMENT = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8'}
_UNIT = re.compile(r'buffalo-hosted-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.service\Z')
_CAPABILITIES = ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')
_NATIVE = ('sudo', 'timeout', 'systemd-run', 'systemctl', 'busctl', 'setpriv', 'env', 'true')


BootstrapRefusal = launcher.LauncherRefusal


class BootstrapCleanupError(BootstrapRefusal):
    pass


@dataclass(frozen=True)
class _DiagnosticEvidence:
    encoded: bytes


def snapshot_diagnostic(report, binding):
    """Bounded public observations only; never cleanup or execution authority.

    Called after transport verification. Retain a digest of the original public
    report, but project fixed fields rather than arbitrary nested child data.
    The immutable bytes prevent later mutation of a received report.
    """
    raw = json.dumps(report, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    _require(len(raw) <= 16384 and report.get('commit') == binding.commit
             and report.get('tree') == binding.tree and report.get('source_files') == dict(binding.files)
             and report.get('execution_authority') is False
             and type(report.get('provider_calls')) is int and report['provider_calls'] == 0
             and type(report.get('payload_processes')) is int and report['payload_processes'] == 0
             and report.get('credential_release') == 'WITHHELD', 'diagnostic_evidence_refused')
    result = {'commit': binding.commit, 'tree': binding.tree,
              'report_sha256': hashlib.sha256(raw).hexdigest(),
              'credential_release': 'WITHHELD', 'execution_authority': False,
              'scope': 'OBSERVATION_ONLY_NOT_ACCEPTANCE'}
    prerequisite = report.get('namespace_prerequisite')
    if prerequisite is not None:
        from hosted_synthetic_namespace_prerequisite import _POLICY
        _require(type(prerequisite) is dict and prerequisite.get('source_commit') == binding.commit
                 and prerequisite.get('source_tree') == binding.tree
                 and prerequisite.get('credential_release') == 'WITHHELD'
                 and prerequisite.get('execution_authority') is False)
        operation = prerequisite['operation']
        kind = operation['result']
        _require(kind in ('SYSCALL_SUCCEEDED_ONLY', 'SYSCALL_REFUSED', 'HARNESS_TIMEOUT',
                          'HARNESS_FAILURE', 'HARNESS_MALFORMED_OUTPUT'))
        observed = {'result': kind, 'errno': None, 'attempts': 'NOT_CONFIRMED'}
        if kind in ('SYSCALL_SUCCEEDED_ONLY', 'SYSCALL_REFUSED'):
            number = operation['errno']
            _require(type(number) is int and 0 <= number <= 4095
                     and (number == 0) == (kind == 'SYSCALL_SUCCEEDED_ONLY')
                     and type(operation.get('attempts')) is int and operation['attempts'] == 1
                     and operation.get('operation') == 'unshare_USER_MNT_PID_NET'
                     and operation.get('errno_name') == errno.errorcode.get(number))
            observed.update(errno=number, errno_name=errno.errorcode.get(number), attempts=1,
                            operation='unshare_USER_MNT_PID_NET')
        elif kind == 'HARNESS_FAILURE':
            code = operation['returncode']
            _require(type(code) is int and -(2**31) <= code < 2**31)
            observed['returncode'] = code
        selected = {'operation': observed, 'policy_cause': 'UNCONFIRMED'}
        for field in ('policy_stable', 'attempt_context_matches_supervisor'):
            if field in prerequisite:
                _require(type(prerequisite[field]) is bool)
                selected[field] = prerequisite[field]
        policies = [('policy_before', prerequisite.get('policy_before')),
                    ('policy_after', prerequisite.get('policy_after'))]
        if 'attempt_context_policy' in operation:
            policies.append(('attempt_context_policy', operation['attempt_context_policy']))
        for field, policy in policies:
            _require(type(policy) is dict and set(policy) == set(_POLICY))
            values = {}
            for path, policy_kind in _POLICY.items():
                item = policy[path]; state = item['state']
                _require(state in ('OBSERVED', 'MISSING', 'DENIED', 'READ_ERROR', 'MALFORMED_OR_OVERSIZED'))
                value = {'state': state}
                if state == 'OBSERVED':
                    _require(type(item.get('sha256')) is str and re.fullmatch('[0-9a-f]{64}', item['sha256']))
                    value['sha256'] = item['sha256']
                    if policy_kind == 'profile':
                        _require(item.get('profile') in ('unconfined', 'REDACTED_PROFILE'))
                        value['profile'] = item['profile']
                    else:
                        current = item.get('value')
                        _require((type(current) is int and 0 <= current <= 9999999999)
                                 if policy_kind == 'integer' else current in ('Y', 'N'))
                        value['value'] = current
                elif state != 'MALFORMED_OR_OVERSIZED':
                    _require(type(item.get('errno')) is int and 0 < item['errno'] <= 4095)
                    value['errno'] = item['errno']
                values[path] = value
            selected[field] = values
        result['namespace_prerequisite'] = selected
    namespace = report.get('namespace_attempt')
    if type(namespace) is dict:
        selected = {}
        reason = namespace.get('reason')
        _require(reason in ('INCOMPLETE_PREFLIGHT', 'NAMESPACE_PROOF_REFUSED', 'CLEANUP_FAILED', 'CANCELLED'))
        selected['reason'] = reason
        location = namespace.get('refusal_location')
        if location is not None:
            _require(type(location) is str and re.fullmatch(r'_[a-z_]{1,48}:[1-9][0-9]{0,3}', location))
            selected['refusal_location'] = location
        external = namespace.get('external_observer_failure')
        if external is not None:
            _require(external in ('external_observer_timeout', 'external_observer_ack_refused'))
            selected['external_observer_failure'] = external
        native = namespace.get('postgres_version')
        if native is not None:
            _require(type(native) is dict and native.get('native_execution') == 'FD_BOUND_VERSION_COMPLETED'
                     and native.get('credential_release') == 'WITHHELD' and native.get('integrated_acceptance') is False
                     and type(native.get('generation')) is str and re.fullmatch('[0-9a-f]{64}', native['generation'])
                     and type(native.get('version')) is str
                     and re.fullmatch(r'postgres \(PostgreSQL\) 16\.[0-9]{1,3}(?: \([A-Za-z0-9.+~:\-/ ]{1,160}\))?', native['version']))
            selected['postgres_version'] = {key: native[key] for key in ('native_execution', 'generation', 'version')}
        result['namespace_attempt'] = selected
    return _DiagnosticEvidence(json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())


def diagnostic_failure_evidence(error, *, binding=None):
    """Read fixed observations through cleanup wrappers, without exception text."""
    seen = set()
    for _ in range(8):
        if not isinstance(error, BaseException) or id(error) in seen:
            break
        seen.add(id(error))
        evidence = getattr(error, '_buffalo_diagnostic_evidence', None)
        if type(evidence) is _DiagnosticEvidence:
            result = json.loads(evidence.encoded)
            if binding is not None:
                _require(result['commit'] == binding.commit and result['tree'] == binding.tree,
                         'diagnostic_evidence_source_mismatch')
            return result
        error = error.__cause__
    return None


def _require(value, code='bootstrap_prerequisite_refused'):
    if not value:
        raise BootstrapRefusal(code)


def _positive(value):
    _require(type(value) is int and 0 < value <= 2**31 - 1)
    return str(value)


def _path(value):
    _require(type(value) is str and re.fullmatch(r'/[A-Za-z0-9_./+\-]+', value)
             and '..' not in Path(value).parts and str(Path(value)) == value)
    return value


def _quote(token):
    # ':' disables environment expansion; percent specifiers are independently
    # rejected. No shell, control characters or systemd specifier expansion.
    _require(type(token) is str and token and all(32 <= ord(c) <= 126 for c in token) and '%' not in token)
    return '"' + token.replace('\\', '\\\\').replace('"', '\\"') + '"'


@dataclass(frozen=True)
class NativeBootstrapPlan:
    argv: tuple[str, ...]
    unit: str
    uid: int
    gid: int
    initial_user_namespace: tuple[int, int]
    root: str
    python: str
    source: tuple[str, str, str, str]
    diagnostic: str = "retained"


def plan_native_bootstrap(*, root, python, repository, event, ref, commit,
                          uid, gid, unit, initial_user_namespace, diagnostic="retained"):
    """Pure argv construction. Never creates a unit or calls sudo."""
    from hosted_synthetic_source import REPOSITORY, REF
    _require(diagnostic in ("retained","postgres-version"))
    _path(root); _path(python)
    _positive(uid); _positive(gid)
    _require(repository == REPOSITORY and event == 'push' and ref == REF
             and type(commit) is str and re.fullmatch('[0-9a-f]{40}', commit)
             and type(unit) is str and _UNIT.fullmatch(unit))
    _require(type(initial_user_namespace) is tuple and len(initial_user_namespace) == 2
             and all(type(x) is int and 0 < x < 2**64 for x in initial_user_namespace))
    # Two native exec stages make the required order explicit: clear groups
    # in the first stage, then change IDs and drop capabilities in the second.
    chain = ('/usr/bin/setpriv', '--clear-groups', '--no-new-privs', '--',
             '/usr/bin/setpriv', f'--reuid={uid}', f'--regid={gid}', '--keep-groups',
             '--inh-caps=-all', '--ambient-caps=-all', '--bounding-set=-all', '--no-new-privs', '--',
             '/usr/bin/env', '-i', 'PATH=/usr/bin:/bin', 'LC_ALL=C.UTF-8',
             python, '-I', '-S', '-B', root + '/procurement/tools/hosted_synthetic_launcher.py',
             repository, event, ref, commit, '--native-bootstrap-postgres-version' if diagnostic=='postgres-version' else '--native-bootstrap', unit, str(uid), str(gid),
             str(initial_user_namespace[0]), str(initial_user_namespace[1]))
    properties = (f'User={uid}', f'Group={gid}', 'Delegate=memory pids',
                  'DelegateSubgroup=supervisor', 'NoNewPrivileges=yes',
                  'CapabilityBoundingSet=CAP_SETUID CAP_SETGID CAP_SETPCAP', 'AmbientCapabilities=',
                  'CPUAccounting=no', 'IOAccounting=no', 'IPAccounting=no',
                  'MemoryAccounting=yes', 'TasksAccounting=yes',
                  'TimeoutStartSec=120s', 'TimeoutStopSec=5s', 'KillMode=control-group',
                  'MemoryMax=512M', 'TasksMax=64',
                  'ExecStartEx=:!' + ' '.join(_quote(token) for token in chain))
    # ! omits credential switching only: User/Group still own the delegated
    # unit and NNP/capability/resource controls still apply to native setpriv.
    # Type=oneshot supports two ExecStart records. A failed first command skips
    # the fixed, unprivileged true. TimeoutStartSec bounds the diagnostic start command; outer timeout also
    # bounds the client across the fixed trailing command.
    argv = ('/usr/bin/sudo', '-n', '--', '/usr/bin/env', '-i', 'PATH=/usr/bin:/bin', 'LC_ALL=C.UTF-8',
            '/usr/bin/timeout', '--signal=TERM', '--kill-after=5s', '150s',
            '/usr/bin/systemd-run', '--system', '--no-ask-password', '--quiet', '--wait', '--pipe', '--collect',
            '--service-type=oneshot', '--expand-environment=no', '--slice='+launcher.accounting_slice(unit),
            '--unit=' + unit, '--working-directory=' + root,
            *(('--property=' + value) for value in properties), '--', '/usr/bin/true')
    return NativeBootstrapPlan(tuple(argv), unit, uid, gid, initial_user_namespace, root, python,
                               (repository, event, ref, commit), diagnostic)


@_resource_operation
def inspect_native_prerequisites():
    """Read only. Refuse missing tools or required ancestor controller changes."""
    _require(os.getuid() == os.geteuid() and os.geteuid() > 0, 'bootstrap_operator_identity_refused')
    programs = {}
    for name in _NATIVE:
        path = Path('/usr/bin') / name
        for parent in (Path('/'), Path('/usr'), Path('/usr/bin')):
            info = parent.lstat()
            _require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
        before = path.lstat()
        _require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_mode & 0o111
                 and not before.st_mode & 0o022 and not before.st_mode & (0o2000 if name == 'sudo' else 0o6000)
                 and 0 < before.st_size <= 32 * 1024**2)
        descriptor = ns._acquire(lambda: os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC))
        try:
            _require(descriptor.identity == ns._identity(before))
            data = os.pread(descriptor.number, before.st_size + 1, 0)
            _require(len(data) == before.st_size)
            try:
                os.getxattr(descriptor.number, 'security.capability')
            except OSError as error:
                _require(error.errno == errno.ENODATA)
            else:
                raise BootstrapRefusal('bootstrap_native_file_capability_refused')
            identity = lambda value: (value.st_dev, value.st_ino, value.st_mode, value.st_uid,
                                      value.st_gid, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
            _require(identity(before) == identity(os.fstat(descriptor.number)) == identity(path.lstat()))
            programs[name] = {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(),
                              'identity': list(descriptor.identity)}
        finally:
            descriptor.close()
    ancestors = {}
    for path in ('/sys/fs/cgroup', '/sys/fs/cgroup/system.slice'):
        info = os.lstat(path)
        _require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
        directory = ns._acquire(lambda: os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC))
        try:
            import hosted_synthetic_cgroup as cg
            cg._filesystem(directory.number)
            from hosted_synthetic_user_service import _controllers
            enabled = _controllers(cg._read_at(directory, 'cgroup.subtree_control'))
            _require({b'memory', b'pids'}.issubset(enabled), 'bootstrap_ancestor_controllers_refused')
            _require(cg._read_at(directory, 'cgroup.type') == b'domain\n')
            ancestors[path] = {'identity': list(directory.identity), 'mount_id': ns._mount_id(directory),
                               'controllers': sorted(value.decode() for value in enabled)}
        finally:
            directory.close()
    return {'programs': programs, 'ancestors': ancestors, 'execution_authority': False}


parse_postdrop_status = launcher.parse_postdrop_status

@_resource_operation
def verify_postdrop_identity(*, uid, gid, unit, initial_user_namespace):
    result = launcher.inspect_postdrop_identity(uid=uid, gid=gid, unit=unit,
                                               initial_user_namespace=initial_user_namespace)
    result.update(process=ns._process(os.getpid()), execution_authority=False)
    return result


@_resource_operation
def verify_postdrop_access(root, unit):
    """Check access in the dropped process, before controller writes."""
    import hosted_synthetic_cgroup as cg
    _require(type(unit) is str and _UNIT.fullmatch(unit))
    directory = control = workspace = None
    try:
        workspace = ns._acquire(lambda: os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC))
        info = os.fstat(workspace.number)
        _require(info.st_uid == os.geteuid() and not info.st_mode & 0o022
                 and os.access(root, os.R_OK | os.W_OK | os.X_OK), 'bootstrap_source_access_refused')
        directory = cg._open_directory(Path('/sys/fs/cgroup'+launcher.service_cgroup_path(unit)))
        for name in ('cgroup.procs', 'cgroup.subtree_control'):
            control = ns._acquire(lambda: os.open(name, os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                                                   dir_fd=directory.number))
            control.verify()
            _require(os.fstat(control.number).st_uid == os.geteuid()
                     and not os.fstat(control.number).st_mode & 0o022
                     and ns._mount_id(control) == ns._mount_id(directory), 'bootstrap_cgroup_access_refused')
            control.close()
            control = None
        _require(cg.parse_scalar(cg._read_at(directory, 'memory.max')) == 512 * 1024**2
                 and cg.parse_scalar(cg._read_at(directory, 'pids.max')) == 64,
                 'bootstrap_resource_limits_refused')
        return {'contract': 'BUFFALO_POSTDROP_ACCESS_V1', 'source_identity': list(workspace.identity),
                'source_read_write_search': True, 'delegation_identity': list(directory.identity),
                'delegation_control_open': True, 'outer_memory_max': 512 * 1024**2,
                'outer_pids_max': 64, 'controller_writes': 0, 'execution_authority': False}
    finally:
        errors = []
        for descriptor in (control, directory, workspace):
            if descriptor is not None:
                try:
                    descriptor.close()
                except BaseException as error:
                    errors.append(error)
        if errors:
            raise BootstrapCleanupError('bootstrap_access_cleanup_failed') from errors[0]


def _collect_native_client(argv, *, cwd, observer=None, lifecycle=None, operation_deadline=None):
    """Bounded direct-child ownership; stderr is never included in reports."""
    process = None
    if operation_deadline is not None:_require(type(operation_deadline) in (int,float) and time.monotonic()<operation_deadline,'bootstrap_client_timeout')
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=_ENVIRONMENT, close_fds=True,
                                   stdin=subprocess.PIPE if observer is not None else subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, bufsize=0)
        deadline = time.monotonic() + 165  # native timeout150 + kill5 + reap grace
        if operation_deadline is not None:deadline=min(deadline,operation_deadline)
        if observer is not None:observer.lifecycle_deadline=deadline
        output = {process.stdout: bytearray(), process.stderr: bytearray()}
        limits = {process.stdout: 65536, process.stderr: 4096}
        observer_acknowledged=False
        with selectors.DefaultSelector() as selector:
            for stream in output:
                selector.register(stream, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                _require(remaining > 0, 'bootstrap_client_timeout')
                ready = selector.select(remaining)
                _require(ready, 'bootstrap_client_timeout')
                for key, _ in ready:
                    stream = key.fileobj
                    chunk = os.read(stream.fileno(), min(4096, limits[stream] + 1 - len(output[stream])))
                    if not chunk:
                        selector.unregister(stream)
                        continue
                    output[stream].extend(chunk)
                    _require(len(output[stream]) <= limits[stream], 'bootstrap_client_output_refused')
                    if observer is not None and stream is process.stdout and not observer_acknowledged and b'\n' in output[stream]:
                        from hosted_synthetic_observer import _PREFIX
                        line,_,tail=output[stream].partition(b'\n')
                        if line.startswith(_PREFIX):
                            _require(len(line)<=4096,'observer_frame_limit')
                            frame=json.loads(line[len(_PREFIX):])
                            _require(_PREFIX+json.dumps(frame,sort_keys=True,separators=(',',':')).encode()==line,
                                     'observer_frame_schema_refused')
                            acknowledgment=observer.bind_ready(frame)
                            mode='SEND' if lifecycle is None else lifecycle.before_ack(observer,frame)
                            _require(mode in ('SEND','HOLD','DROP'),'lifecycle_ack_mode_refused')
                            if mode=='SEND':
                                _require(len(acknowledgment)<=256 and os.write(process.stdin.fileno(),acknowledgment)==len(acknowledgment),
                                         'observer_acknowledgment_failed')
                                process.stdin.close();observer.acknowledge_sent()
                                if lifecycle is not None:lifecycle.after_ack(observer)
                            elif mode=='DROP':process.stdin.close()
                            observer_acknowledged=True
                            output[stream]=bytearray(tail)
        remaining = deadline - time.monotonic()
        _require(remaining > 0, 'bootstrap_client_timeout')
        code = process.wait(timeout=remaining)
        return code, bytes(output[process.stdout])
    finally:
        if process is not None:
            errors = []
            try:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                process.wait(timeout=5)
            except BaseException as error:
                errors.append(error)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is None:continue
                try:
                    stream.close()
                except BaseException as error:
                    errors.append(error)
            if errors:
                raise BootstrapCleanupError('bootstrap_client_cleanup_failed') from errors[0]


def _observe_unit_absence(unit):
    _require(type(unit) is str and _UNIT.fullmatch(unit))
    # Read-only queries. Never stop/delete a service by an unbound name.
    for _ in range(5):
        result = subprocess.run(['/usr/bin/timeout', '--signal=TERM', '--kill-after=1s', '3s',
                                 '/usr/bin/systemctl', '--system', '--no-ask-password', 'show', unit,
                                 '--property=LoadState', '--value'], env=_ENVIRONMENT,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=5, check=False)
        if result.returncode in (0, 1) and result.stdout == b'not-found\n':
            return 'NOT_FOUND_OBSERVED'
        time.sleep(0.1)
    raise BootstrapRefusal('bootstrap_unit_lifecycle_unverified')


def _decode_report(raw, plan):
    _require(type(raw) is bytes and len(raw) <= 65536, 'bootstrap_report_refused')
    lines = raw.splitlines()
    _require(len(lines) == 2 and len(lines[0]) <= 16384, 'bootstrap_report_refused')
    _require(lines[1] == b'evidence_sha256=' + hashlib.sha256(lines[0]).hexdigest().encode(), 'bootstrap_report_refused')
    try:
        report = json.loads(lines[0])
    except (ValueError, UnicodeError):
        raise BootstrapRefusal('bootstrap_report_refused') from None
    _require(type(report) is dict and json.dumps(report, sort_keys=True, separators=(',', ':'), allow_nan=False).encode() == lines[0]
             and report.get('commit') == plan.source[3] and report.get('repository') == plan.source[0]
             and report.get('ref') == plan.source[2] and report.get('execution_authority') is False
             and report.get('contract') == 'BUFFALO_HOSTED_NAMESPACE_DIAGNOSTIC_V1'
             and report.get('HOST_CAPABILITY') == 'FAIL' and report.get('sentinel_exec') == 'NOT_RUN'
             and report.get('runtime') == 'HOST_RUNTIME_UNSEALED' and report.get('railway_compatibility') == 'NOT_PROVEN'
             and type(report.get('provider_calls')) is int and report['provider_calls'] == 0
             and type(report.get('payload_processes')) is int and report['payload_processes'] == 0
             and report.get('credential_release') == 'WITHHELD', 'bootstrap_report_refused')
    return report


@_resource_operation
def run_native_plan(plan, *, source_root=None, account=None, lifecycle=None):
    """Dormant execution implementation: no CLI/workflow path calls this yet.

    Independent review must precede its later activation. The source-bound
    caller is unprivileged; only the fixed native argv prefix runs privileged.
    A lifecycle observation is not exclusive-access or containment acceptance.
    """
    from hosted_synthetic_source import bind_hosted_source
    from hosted_synthetic_capability import _require_clean_supervisor
    _require_clean_supervisor()
    _require(type(plan) is NativeBootstrapPlan)
    repository, event, ref, commit = plan.source
    rebuilt = plan_native_bootstrap(root=plan.root, python=plan.python, repository=repository,
                                    event=event, ref=ref, commit=commit, uid=plan.uid, gid=plan.gid,
                                    unit=plan.unit, initial_user_namespace=plan.initial_user_namespace,diagnostic=plan.diagnostic)
    _require(plan == rebuilt and ns._identity(os.stat('/proc/self/ns/user')) == plan.initial_user_namespace)
    # The historical shared-runner identity route is no longer executable.
    from hosted_synthetic_identity import AccountLease
    _require(type(account) is AccountLease and account._owner is account,
             'dedicated_account_required')
    account.verify_root()
    selected=account.identity
    _require(os.getresuid()==(selected.operator_uid,)*3 and os.getresgid()==(selected.operator_gid,)*3
             and plan.uid==selected.uid and plan.gid==selected.gid
             and plan.root==selected.root+'/repo' and plan.unit==selected.unit
             and type(source_root) is str and source_root!=plan.root)
    binding = bind_hosted_source(Path(source_root), repository=repository, event=event, ref=ref, sha=commit)
    if lifecycle is not None:
        from hosted_synthetic_lifecycle import require_case
        require_case(lifecycle,account.identity,commit)
        _require(lifecycle.binding==binding)
    before = inspect_native_prerequisites()
    if lifecycle is not None:lifecycle.native_observed(before)
    from hosted_synthetic_observer import ExternalObserver
    observer=None;error=None;cleanup_errors=[];code=raw=report=None;evidence=None
    try:
        _require(account.service_attempted is False and account.service_acknowledged is False,
                 'native_attempt_cannot_be_retried')
        observer=ExternalObserver.create(account,binding)
        if lifecycle is not None:lifecycle.observer_created(observer)
        account.journal.record('service_start_attempt')
        account.service_attempted=True
        code,raw=_collect_native_client(plan.argv,cwd=source_root,observer=observer,**({} if lifecycle is None else {'lifecycle':lifecycle}))
        _require(code in (0,1),'bootstrap_client_refused')
        report=_decode_report(raw,plan)
        _require(report.get('tree')==binding.tree and report.get('source_files')==dict(binding.files),
                 'bootstrap_report_source_refused')
        evidence=snapshot_diagnostic(report,binding)
        if lifecycle is not None:lifecycle.report_received(report)
        observation=report.get('namespace_attempt')
        namespace_clean=(report.get('reason')=='INCOMPLETE_PREFLIGHT' and type(observation) is dict
                         and observation.get('cleanup_complete') is True and observation.get('reason')=='INCOMPLETE_PREFLIGHT'
                         and type(observation.get('resource_cleanup')) is dict
                         and observation['resource_cleanup'].get('generation_child_removed') is True)
        if plan.diagnostic=='postgres-version':
            native=observation.get('postgres_version') if type(observation) is dict else None
            namespace_clean=namespace_clean and type(native) is dict and native.get('native_execution')=='FD_BOUND_VERSION_COMPLETED'
        # This proof comes from the outside observer, not the child's report.
        # A failed operation can have verified cleanup; it still cannot pass.
        if observer.work_acknowledged:
            observer.finish_service(operation_succeeded=namespace_clean)
        else:
            observer.finish_pre_ack_exit(report,client_returncode=code)
            namespace_clean=False
        if report.get('reason')=='CLEANUP_FAILED':
            raise BootstrapCleanupError('native_diagnostic_cleanup_failed')
        _require(namespace_clean,'native_diagnostic_incomplete')
    except BaseException as selected:error=selected
    if observer is not None:
        if observer.proof is None and observer.work_acknowledged and not observer.failed:
            try:observer.finish_service(operation_succeeded=False)
            except BaseException as selected:cleanup_errors.append(selected)
        try:observer.close()
        except BaseException as selected:cleanup_errors.append(selected)
        if observer.proof is not None and observer.closed and not observer.failed:
            account.drain_proof=observer.proof
            account.service_acknowledged=True
    try:
        _require(inspect_native_prerequisites()==before,'bootstrap_native_or_ancestor_drift')
    except BaseException as selected:cleanup_errors.append(selected)
    if cleanup_errors:
        account.uncertain=True
        failure=BootstrapCleanupError('bootstrap_cleanup_unverified')
        if evidence is not None:failure._buffalo_diagnostic_evidence=evidence
        raise failure from (error or cleanup_errors[0])
    if error is not None:
        if evidence is not None:error._buffalo_diagnostic_evidence=evidence
        raise error
    _require(observer is not None and account.drain_proof is not None)
    proof=account.drain_proof
    return {'contract':'BUFFALO_NATIVE_BOOTSTRAP_OBSERVATION_V1','report':report,
            'native_prerequisites':before,'client_returncode':code,
            'service_absence':'EXACT_OWNED_SUBTREE_REMOVAL_OBSERVED','ancestors_unchanged':True,
            'external_cleanup':{'generation':proof.generation,'commit':proof.source_commit,'tree':proof.source_tree,
                'aggregate':proof.aggregate,'whole_service_lifetime_observed':True,
                'operation_succeeded':proof.operation_succeeded,'observer_descriptors_closed':True},
            'execution_authority':False,'runtime_closure':'UNSEALED'}


def main(arguments=None):
    # Deliberately unconditional. No flag, caller-supplied PASS, review file or
    # ambient variable activates privilege. Activation awaits actual Claude
    # findings and a separate reviewed wiring change.
    print('{"bootstrap_execution":"DISABLED_PENDING_INDEPENDENT_REVIEW","privileged_processes_started":0}')
    return 78


if __name__ == '__main__':
    raise SystemExit(main())
