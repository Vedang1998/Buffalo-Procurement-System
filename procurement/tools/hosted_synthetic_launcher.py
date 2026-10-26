"""Post-native-drop launcher; repository code is never a root bootstrap."""
import os
import re
from pathlib import Path
import subprocess
import sys


class LauncherRefusal(RuntimeError):
    pass


def _require(condition, code='bootstrap_prerequisite_refused'):
    if not condition:
        raise LauncherRefusal(code)


def _positive(value):
    _require(type(value) is int and 0 < value <= 2**31 - 1)


_CAPABILITIES = ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')
_UNIT = re.compile(r'buffalo-hosted-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.service\Z')


def parse_postdrop_status(raw, *, uid, gid):
    """Kernel readback, not a claim inferred from setpriv's exit status."""
    _positive(uid); _positive(gid)
    _require(type(raw) is bytes and 0 < len(raw) <= 16384 and raw.endswith(b'\n'), 'bootstrap_status_refused')
    required = {'Uid', 'Gid', 'Groups', 'NoNewPrivs', *_CAPABILITIES}
    fields = {}
    for row in raw.splitlines():
        key, separator, value = row.partition(b':')
        try:
            key = key.decode('ascii')
        except UnicodeDecodeError:
            raise LauncherRefusal('bootstrap_status_refused') from None
        if key in required:
            _require(separator and key not in fields, 'bootstrap_status_refused')
            fields[key] = value.strip()
    _require(set(fields) == required, 'bootstrap_status_refused')
    ids = {}
    for name, expected in (('Uid', uid), ('Gid', gid)):
        values = fields[name].split()
        _require(len(values) == 4 and all(re.fullmatch(rb'(?:0|[1-9][0-9]{0,9})', x) for x in values), 'bootstrap_status_refused')
        ids[name] = [int(x) for x in values]
        _require(ids[name] == [expected]*4, 'bootstrap_ids_refused')
    _require(fields['Groups'] == b'', 'bootstrap_groups_refused')
    _require(fields['NoNewPrivs'] == b'1', 'bootstrap_nnp_refused')
    for name in _CAPABILITIES:
        _require(re.fullmatch(rb'[0-9a-f]{16}', fields[name]) is not None, 'bootstrap_status_refused')
        _require(int(fields[name], 16) == 0, 'bootstrap_capabilities_refused')
    return {'uids': ids['Uid'], 'gids': ids['Gid'], 'groups': [],
            'capabilities': {key: 0 for key in _CAPABILITIES}, 'no_new_privs': 1}



def inspect_postdrop_identity(*, uid, gid, unit, initial_user_namespace):
    _require(type(unit) is str and _UNIT.fullmatch(unit), 'bootstrap_unit_refused')
    with open('/proc/self/status', 'rb') as stream:
        result = parse_postdrop_status(stream.read(16385), uid=uid, gid=gid)
    _require(os.getresuid() == (uid,)*3 and os.getresgid() == (gid,)*3
             and os.getgroups() == [], 'bootstrap_ids_refused')
    info = os.stat('/proc/self/ns/user')
    _require(type(initial_user_namespace) is tuple and len(initial_user_namespace) == 2
             and all(type(x) is int and x > 0 for x in initial_user_namespace)
             and (info.st_dev, info.st_ino) == initial_user_namespace, 'bootstrap_initial_namespace_refused')
    with open('/proc/self/cgroup', 'rb') as stream:
        raw = stream.read(4097)
    expected = f'/system.slice/{unit}/supervisor'
    _require(raw == ('0::' + expected + '\n').encode(), 'bootstrap_scope_refused')
    result.update(contract='BUFFALO_POSTDROP_IDENTITY_V1', initial_user_namespace=list(initial_user_namespace),
                  cgroup=expected, execution_authority=False)
    return result


def main(arguments=None):
    # Refuse even to dispatch a repository child if a native drop was omitted.
    if os.getuid() == 0 or os.geteuid() == 0:
        return 78
    args = list(sys.argv[1:] if arguments is None else arguments)
    if len(args) != 10 or args[4] != '--native-bootstrap':
        return 78
    try:
        if any(not value.isascii() or not value.isdecimal() or value.startswith('0') or len(value)>20 for value in args[6:]):
            return 78
        inspect_postdrop_identity(uid=int(args[6]), gid=int(args[7]), unit=args[5],
                                 initial_user_namespace=(int(args[8]), int(args[9])))
    except (LauncherRefusal, OSError, ValueError):
        return 78
    try:
        return subprocess.run([sys.executable, '-I', '-S', '-B',
                               str(Path(__file__).with_name('hosted_synthetic_capability.py')), *args],
                              close_fds=True, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8'},
                              stdin=subprocess.DEVNULL, timeout=110).returncode
    except subprocess.TimeoutExpired:
        # systemd's exact owned control group and stop bound cover descendants.
        # Timeout is never a successful cleanup or capability result.
        return 124


if __name__ == '__main__':
    raise SystemExit(main())
