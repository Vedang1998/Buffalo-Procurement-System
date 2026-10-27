"""Fail-closed native core-dump prerequisite; never change a host sysctl.

Normal native exec can reset dumpability. An inherited zero hard core limit is
therefore paired with a real non-piped host core_pattern observation. Linux may
ignore RLIMIT_CORE for piped handlers. This component does not establish process
isolation, prevent sibling memory reads, or grant execution/credential authority.
"""
import hashlib
import os
import resource

import hosted_synthetic_namespace as ns
import hosted_synthetic_worker as worker
from hosted_synthetic_source import _resource_operation

POLICY='BUFFALO_HOSTED_NATIVE_CORE_V1'
_PATH='/proc/sys/kernel/core_pattern'


def parse_pattern(raw):
    ns._require(type(raw) is bytes and 1<len(raw)<=128 and raw.endswith(b'\n'))
    body=raw[:-1]
    # Explicit bounded filename-pattern schema. Unsupported bytes or a handler
    # marker refuse; no unknown policy is silently normalized into a safe name.
    ns._require(body.strip() and all(32<=value<=126 for value in body) and b'|' not in body)
    return hashlib.sha256(raw).hexdigest()


@_resource_operation
def observe_pattern():
    descriptor=ns._acquire(lambda:os.open(_PATH,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW))
    error=None;result=None
    try:
        ns._require(ns.backend._browser_fstatfs_magic(descriptor.number)==0x9fa0)
        result=parse_pattern(os.pread(descriptor.number,129,0))
    except BaseException as failure:error=failure
    try:descriptor.close()
    except BaseException as cleanup:
        raise ns.NamespaceCleanupError('native_core_pattern_close_refused') from (error or cleanup)
    if error is not None:raise error
    return result


def _entry():
    ns._require(os.getresuid()==os.getresgid()==(1000,)*3 and os.getgroups()==[])
    current=worker._status()
    ns._require(current['uid']==current['gid']==[1000]*4 and current['groups']==[]
        and current['no_new_privs']==1 and all(current[key]==0 for key in worker._CAPS)
        and current['Seccomp']==2 and current['Seccomp_filters']>0
        and worker._get_prctl(27)==worker._SECUREBITS and worker._get_prctl(3)==0)


def _zero_limit():
    # Only the current disposable process and its future descendants are
    # affected. Lowering a hard limit cannot be rolled back by this worker.
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    ns._require(resource.getrlimit(resource.RLIMIT_CORE)==(0,0))


def enforce_native_core():
    """Before native exec, after actual capability erasure/fixed filter setup.

    The external lifecycle observer must bind this host pattern and actual
    child limits again through the credential-bearing lifetime. A host/root
    policy change is not an accepted silent transition to piped core handling.
    """
    _entry()
    pattern=observe_pattern()
    _zero_limit()
    ns._require(observe_pattern()==pattern)
    _entry()
    return {'policy':POLICY,'core_pattern_sha256':pattern,'handler':'NON_PIPED',
        'soft_limit':0,'hard_limit':0,'scope':'PRE_EXEC_COMPONENT',
        'external_lifetime_observation':'NOT_PROVEN','execution_authority':False}
