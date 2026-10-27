"""Private bounded attribution before ephemeral identity/resource mutations.

No credentials, private emails, command output or source data are recorded.
An uncertain native result leaves its journal and reserves its UID/GID. Only
verified owned-resource removal may mark it resolved; no automatic rollback.
"""
import json
import os
from pathlib import Path
import re
import stat

import hosted_synthetic_bootstrap as boot
import hosted_synthetic_namespace as ns


ROOT=Path('/tmp')
PREFIX='buffalo-identity-quarantine-'
_MAXIMUM=32768
_STAGES=frozenset(('RESERVED','accounting_create_attempt','accounting_created','service_start_attempt',
    'external_generation_bound','external_service_drained','external_accounting_removed','RESOLVED',
    *(f'provision_attempt_{index}' for index in range(8)),*(f'provision_ack_{index}' for index in range(8)),
    *(f'remove_attempt_{index}' for index in range(4)),*(f'remove_ack_{index}' for index in range(4))))


def _encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode('ascii')+b'\n'


def _details(value):
    boot._require(type(value) is dict and set(value)<= {'generation','invocation_id','mount_id','actors',
        'staging_identity','accounting_identity','service_identity','generation_identity'})
    for key,selected in value.items():
        if key in ('generation','invocation_id'):
            boot._require(type(selected) is str and re.fullmatch('[0-9a-f]{'+('64' if key=='generation' else '32')+'}',selected))
        elif key=='mount_id':boot._require(type(selected) is int and 0<selected<2**64)
        elif key=='actors':
            boot._require(type(selected) is dict and set(selected)=={'launcher','supervisor','guardian','init','worker'})
            for actor in selected.values():
                boot._require(type(actor) is dict and set(actor)=={'pid','start'}
                              and all(type(x) is int and 0<x<2**64 for x in actor.values()) and actor['pid']<2**31)
        else:boot._require(type(selected) is list and len(selected)==2 and all(type(x) is int and 0<x<2**64 for x in selected))
    return value


def _decode(raw):
    boot._require(type(raw) is bytes and 0<len(raw)<=_MAXIMUM and raw.endswith(b'\n'))
    lines=raw.splitlines(keepends=True)
    header=json.loads(lines[0]);boot._require(_encoded(header)==lines[0] and type(header) is dict
        and set(header)=={'contract','nonce','uid','gid','operator_uid','operator_gid','unit','commit'}
        and header['contract']=='BUFFALO_IDENTITY_QUARANTINE_V1')
    from hosted_synthetic_identity import DedicatedIdentity,validate
    identity=DedicatedIdentity(*(header[key] for key in ('nonce','uid','gid','operator_uid','operator_gid')))
    validate(identity)
    boot._require(identity.unit==header['unit'] and re.fullmatch('[0-9a-f]{40}',header['commit']))
    stages=[]
    for index,line in enumerate(lines[1:]):
        value=json.loads(line)
        boot._require(type(value) is dict and set(value) in ({'sequence','stage'},{'sequence','stage','details'}) and _encoded(value)==line
                      and type(value['sequence']) is int and value['sequence']==index
                      and value['stage'] in _STAGES and 'RESOLVED' not in stages)
        stages.append(value['stage'])
        if 'details' in value:_details(value['details'])
    boot._require(stages and stages[0]=='RESERVED' and len(stages)<=64)
    return header,stages


def reserved_ids():
    """Malformed/foreign journals refuse allocation; never silently ignore one."""
    reservations=[]
    paths=list(ROOT.glob(PREFIX+'*.jsonl'));boot._require(len(paths)<=100)
    for path in paths:
        descriptor=ns._acquire(lambda:os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW))
        try:
            info=os.fstat(descriptor.number)
            boot._require(stat.S_ISREG(info.st_mode) and info.st_uid==os.geteuid()
                          and info.st_nlink==1 and stat.S_IMODE(info.st_mode)==0o600 and info.st_size<=_MAXIMUM)
            raw=os.pread(descriptor.number,_MAXIMUM+1,0)
            boot._require(ns._identity(path.lstat())==descriptor.identity and len(raw)==info.st_size)
            header,stages=_decode(raw)
            boot._require(path.name==PREFIX+header['nonce']+'.jsonl' and header['operator_uid']==os.geteuid())
            if stages[-1]!='RESOLVED':reservations.append((header['uid'],header['gid']))
        finally:descriptor.close()
    return frozenset(reservations)


class Quarantine:
    def __init__(self):raise boot.BootstrapRefusal('use_owned_quarantine_creation')

    @classmethod
    def create(cls,identity,commit):
        from hosted_synthetic_identity import validate
        validate(identity)
        boot._require(os.getresuid()==(identity.operator_uid,)*3 and os.getresgid()==(identity.operator_gid,)*3
                      and re.fullmatch('[0-9a-f]{40}',commit)
                      and all(identity.uid!=uid and identity.gid!=gid for uid,gid in reserved_ids()),
                      'quarantined_identity_cannot_be_reused')
        value=object.__new__(cls);value._owner=value;value.closed=False
        value.path=ROOT/(PREFIX+identity.nonce+'.jsonl')
        value.descriptor=ns._acquire(lambda:os.open(value.path,os.O_RDWR|os.O_APPEND|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC|os.O_NOFOLLOW,0o600))
        value.raw=_encoded({'contract':'BUFFALO_IDENTITY_QUARANTINE_V1','nonce':identity.nonce,'uid':identity.uid,
            'gid':identity.gid,'operator_uid':identity.operator_uid,'operator_gid':identity.operator_gid,
            'unit':identity.unit,'commit':commit})+_encoded({'sequence':0,'stage':'RESERVED'})
        value.sequence=1;value.identity=value.descriptor.identity
        try:
            boot._require(os.write(value.descriptor.number,value.raw)==len(value.raw))
            os.fsync(value.descriptor.number)
            # Persist directory entry before account/resource native mutations.
            parent=ns._acquire(lambda:os.open(ROOT,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW))
            try:os.fsync(parent.number)
            finally:parent.close()
            value._verify_descriptor(value.descriptor)
            value.descriptor.close();value.descriptor=None
            return value
        except BaseException:
            if value.descriptor is not None:value.descriptor.close()
            value.closed=True
            # Even partial journal bytes remain, refusing future reuse.
            raise

    def _open(self,flags):
        boot._require(self._owner is self and not self.closed)
        descriptor=ns._acquire(lambda:os.open(self.path,flags|os.O_CLOEXEC|os.O_NOFOLLOW))
        try:
            boot._require(descriptor.identity==self.identity)
            self._verify_descriptor(descriptor)
            return descriptor
        except BaseException:descriptor.close();raise

    def _verify_descriptor(self,descriptor):
        descriptor.verify();info=os.fstat(descriptor.number)
        boot._require(ns._identity(self.path.lstat())==descriptor.identity==self.identity
                      and stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode)==0o600
                      and info.st_nlink==1 and info.st_uid==os.geteuid()
                      and info.st_size==len(self.raw) and os.pread(descriptor.number,_MAXIMUM+1,0)==self.raw)
        _decode(self.raw)

    def verify(self):
        descriptor=self._open(os.O_RDONLY)
        descriptor.close()

    def _append(self,stage,details=None):
        descriptor=self._open(os.O_RDWR|os.O_APPEND)
        try:
            boot._require(self.sequence<64)
            value={'sequence':self.sequence,'stage':stage}
            if details is not None:value['details']=_details(details)
            line=_encoded(value)
            boot._require(len(self.raw)+len(line)<=_MAXIMUM and os.write(descriptor.number,line)==len(line))
            os.fsync(descriptor.number)
            self.raw+=line;self.sequence+=1;self._verify_descriptor(descriptor)
        finally:descriptor.close()

    def record(self,stage,details=None):
        boot._require(stage in _STAGES and stage not in ('RESERVED','RESOLVED'))
        self._append(stage,details)

    def resolve(self):
        """Caller already consumed external proof and removed the identity."""
        self._append('RESOLVED');self.closed=True

    def close(self):
        # Every operation closes its own FD; unresolved bytes remain reserved.
        self.closed=True
