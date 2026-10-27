"""Fixed native PG16 dependency capture into the existing sealed-root format.

This is separate from the diagnostic/application and accepted Nix/browser
closures. Capture seals bytes; it does not prove native execution, a writable
PG phase, identity, network isolation, enrollment or credential release. There
is no CLI, subprocess, credential provider or privileged activation here.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re

import hosted_synthetic_namespace as ns
import hosted_synthetic_runtime as runtime
import hosted_synthetic_source as source

BIN = '/usr/lib/postgresql/16/bin/'
LIB = '/usr/lib/postgresql/16/lib/'
SHARE = '/usr/share/postgresql/16/'
ZONE = '/usr/share/zoneinfo/'
EXECUTABLES = tuple(BIN + name for name in ('postgres','initdb','pg_dump','pg_restore','pg_controldata')) + ('/bin/sh','/usr/bin/locale')
_DATA = ('postgres.bki','pg_hba.conf.sample','pg_ident.conf.sample','postgresql.conf.sample',
         'snowball_create.sql','information_schema.sql','sql_features.txt',
         'system_constraints.sql','system_functions.sql','system_views.sql','timezonesets/Default')
_LOCALE = ('LC_ADDRESS','LC_COLLATE','LC_CTYPE','LC_IDENTIFICATION','LC_MEASUREMENT',
           'LC_MESSAGES/SYS_LC_MESSAGES','LC_MONETARY','LC_NAME','LC_NUMERIC',
           'LC_PAPER','LC_TELEPHONE','LC_TIME')
_CONTROLS = {
    'pgcrypto': {'comment': "'cryptographic functions'", 'default_version': "'1.3'",
        'module_pathname': "'$libdir/pgcrypto'", 'relocatable': 'true', 'trusted': 'true'},
    'plpgsql': {'comment': "'PL/pgSQL procedural language'", 'default_version': "'1.0'",
        'module_pathname': "'$libdir/plpgsql'", 'relocatable': 'false', 'schema': 'pg_catalog',
        'superuser': 'true', 'trusted': 'true'},
}
# Bound native resolver policy; never import the host's users, NSS, resolver,
# OpenSSL initialization, passfiles, service files or certificate configuration.
_FIXED = {
    '/etc/passwd': b'buffalo-pg:x:1000:1000:Disposable synthetic PostgreSQL:/nonexistent:/usr/sbin/nologin\n',
    '/etc/group': b'buffalo-pg:x:1000:\n',
    '/etc/nsswitch.conf': b'passwd: files\ngroup: files\nhosts: files\n',
    '/etc/hosts': b'127.0.0.1 localhost\n::1 localhost\n',
    '/buffalo-runtime/openssl.cnf': b'openssl_conf = buffalo\n[buffalo]\nproviders = providers\n[providers]\ndefault = default\n[default]\nactivate = 1\n',
}


def _ascii(raw, bound):
    ns._require(type(raw) is bytes and 0 < len(raw) <= bound and b'\0' not in raw)
    try:return raw.decode('ascii')
    except UnicodeError:raise ns.NamespaceProofError('postgres_data_encoding_refused') from None


def module_references(raw):
    """Version-bound data scanner, not an SQL parser or arbitrary path resolver."""
    _ascii(raw,2*1024**2)
    matches=tuple(re.finditer(rb"'\$libdir/([a-z][a-z0-9_]{0,79})'",raw))
    ns._require(len(matches)<=1024 and raw.count(b'$libdir')==len(matches))
    return tuple(sorted({m[1].decode('ascii') for m in matches}))


def stopword_references(raw):
    _ascii(raw,128*1024)
    matches=tuple(re.finditer(rb'(?i)\bStopWords[ \t]*=[ \t]*([a-z][a-z0-9_]{0,79})[ \t]*(?=[,)])',raw))
    ns._require(len(matches)<=128 and len(re.findall(rb'(?i)\bStopWords\b',raw))==len(matches))
    return tuple(sorted({m[1].decode('ascii').lower() for m in matches}))


def timezone_references(raw):
    text=_ascii(raw,128*1024);names=set();zones={'UTC','America/New_York'}
    lines=text.splitlines();ns._require(len(lines)<=4096)
    for line in lines:
        ns._require(len(line)<=512)
        fields=line.split('#',1)[0].split()
        if not fields:continue
        ns._require(len(fields) in (2,3) and re.fullmatch('[A-Z][A-Z0-9]{0,15}',fields[0])
                    and fields[0] not in names)
        names.add(fields[0]);ns._require(len(names)<=512)
        if re.fullmatch('-?(0|[1-9][0-9]{0,5})',fields[1]):
            ns._require(abs(int(fields[1]))<=24*60*60 and (len(fields)==2 or fields[2]=='D'))
        else:
            zone=fields[1]
            ns._require(len(fields)==2 and len(zone)<=120 and
                re.fullmatch(r'[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+)*',zone))
            zones.add(zone)
    ns._require(names and len(zones)<=128)
    return tuple(sorted(zones))


def extension_control(name,raw):
    ns._require(name in _CONTROLS)
    actual={}
    for line in _ascii(raw,8192).splitlines():
        line=line.strip()
        if not line or line.startswith('#'):continue
        matched=re.fullmatch('([a-z_]+)[ \t]*=[ \t]*(.+)',line)
        ns._require(matched is not None and matched[1] not in actual)
        actual[matched[1]]=matched[2]
    ns._require(actual==_CONTROLS[name])
    return actual['default_version'][1:-1]


def _source_identity(root,binding):
    ns._require(type(binding) is source.HostedSourceBinding)
    current=source.bind_hosted_source(Path(root),repository=source.REPOSITORY,
        event='push',ref=source.REF,sha=binding.commit)
    ns._require(current==binding)


def capture_postgres(generation,root,binding):
    """Capture only the fixed root-trusted platform paths; no PATH fallback.

    The caller supplies an already bound detached checkout. Repeat the complete
    source binding before/after capture. Cancellation owns any unreturned memfd.
    The existing 80 MiB/3000 entry limits are unchanged.
    """
    result=None
    @source._resource_operation
    def guarded():
        nonlocal result
        result=_capture(generation,root,binding)
        return result
    try:return guarded()
    except BaseException as error:
        if result is not None:
            try:result.close()
            except BaseException:
                raise ns.NamespaceCleanupError('postgres_capture_cleanup_refused') from error
        raise


def _capture(generation,root,binding):
    ns._require(type(generation) is str and re.fullmatch('[0-9a-f]{64}',generation)
                and os.uname().machine=='x86_64')
    _source_identity(root,binding)
    contents={};native={};total=0
    def add(path,data,mode):
        nonlocal total
        runtime._path(path)
        ns._require(type(data) is bytes and mode in (0o444,0o555))
        if path in contents:
            ns._require(contents[path]==(data,mode));return False
        total+=len(data)
        ns._require(total<=runtime.MAX_BYTES and len(contents)<runtime.MAX_ENTRIES)
        contents[path]=(data,mode);native[path]=hashlib.sha256(data).hexdigest()
        return True
    def data_file(path):
        data,mode=runtime.read_native(path)
        ns._require(not data.startswith(b'\x7fELF'))
        add(path,data,0o444);return data
    data={name:data_file(SHARE+name) for name in _DATA}
    ns._require(data['postgres.bki'].splitlines()[0]==b'# PostgreSQL 16')
    modules=set()
    for name,raw in data.items():
        if name=='postgres.bki' or name.endswith('.sql'):modules.update(module_references(raw))
    ns._require(modules and 'dict_snowball' in modules and len(modules)<=64)
    stops=stopword_references(data['snowball_create.sql'])
    ns._require(stops)
    for name in stops:data_file(SHARE+'tsearch_data/'+name+'.stop')
    for name in _CONTROLS:
        version=extension_control(name,data_file(SHARE+'extension/'+name+'.control'))
        raw=data_file(SHARE+'extension/'+name+'--'+version+'.sql')
        # MODULE_PATHNAME resolves through the exact validated control file.
        ns._require(b'MODULE_PATHNAME' in raw)
        modules.add(name);modules.update(module_references(raw))
    ns._require(len(modules)<=64)
    zones=timezone_references(data['timezonesets/Default'])
    for name in zones:ns._require(data_file(ZONE+name).startswith(b'TZif'))
    for name in _LOCALE:data_file('/usr/lib/locale/C.utf8/'+name)
    for path,raw in _FIXED.items():add(path,raw,0o444)
    roots=runtime._library_roots()
    pending=[('path',path) for path in (*EXECUTABLES,*(LIB+name+'.so' for name in sorted(modules)))]
    # glibc may implement files NSS internally; include its native compatibility
    # DSO explicitly so a supported loader's dlopen cannot escape the inventory.
    pending.append(('library','libnss_files.so.2'));resolved_libraries=set()
    while pending:
        kind,name=pending.pop()
        if kind=='path':
            if name in contents:continue
            path=name;raw,mode=runtime.read_native(path)
        else:
            if name in resolved_libraries:continue
            path,raw,mode=runtime._resolve_library(name,roots);resolved_libraries.add(name)
        ns._require(raw.startswith(b'\x7fELF'))
        image=runtime.parse_elf(raw)
        if path in EXECUTABLES:ns._require(image['interpreter'] is not None and mode==0o555)
        if not add(path,raw,mode):continue
        if image['interpreter']:
            pending.append(('path',image['interpreter']))
        pending.extend(('library',name) for name in image['needed'])
    _source_identity(root,binding)
    metadata={'executables':list(EXECUTABLES),'modules':sorted(modules),'stopwords':list(stops),'timezones':list(zones),
        'source_files':dict(binding.files),'locale_files':list(_LOCALE),
        'generated_configuration':{path:hashlib.sha256(raw).hexdigest() for path,raw in _FIXED.items()},
        'scope':'NATIVE_POSTGRES_CAPTURE_ONLY',
        'execution':'NOT_PROVEN','credential_release':'WITHHELD',
        'required_phase_controls':['NONZERO_NAMESPACE_UID_GID_1000','EMPTY_GROUPS_ZERO_CAPS_NNP',
            'WORKER_CONTROL_EXCLUSION','OWNED_WRITABLE_DATA_SOCKET_TMP_AND_SHM','CONTROLLED_DEV_NULL',
            'SEALED_SERVER_CONFIG_AND_AUTO_CONFIG_EXCLUSION','LIBC_C_UTF8','JIT_OFF_SSL_OFF',
            'EMPTY_PRELOAD_LIBRARIES','FIXED_OPENSSL_CONF','PRIVATE_NETWORK','STOPPED_ENROLLMENT',
            'MATCHED_PROJECTED_PG16_VERSIONS','PROJECTED_EXEC_AND_LIVE_NATIVE_MAPPING_PROOF','EXTERNAL_LIFECYCLE_AND_RESOURCE_PROOF']}
    entries=[ns.backend._BrowserRuntimeBundleEntry(path=path,kind='F',mode=mode,user_id=0,group_id=0,
        provenance=('audit-source:' if path in _FIXED else 'native-runtime:')+native[path],content=raw)
        for path,(raw,mode) in contents.items()]
    from hosted_synthetic_projection import sentinel_bytes
    sentinel=sentinel_bytes()
    identity=runtime._identity_bytes(generation,BIN+'postgres',{},native,binding.commit,binding.tree,
        policy=runtime.POSTGRES_POLICY,postgres=metadata)
    for path,raw,mode in ((runtime.SENTINEL,sentinel,0o555),(runtime._IDENTITY_PATH,identity,0o444)):
        entries.append(ns.backend._BrowserRuntimeBundleEntry(path=path,kind='F',mode=mode,user_id=0,group_id=0,
            provenance='audit-source:'+hashlib.sha256(raw).hexdigest(),content=raw))
    complete=ns.backend._complete_browser_runtime_bundle_parents(tuple(entries))
    ns._require(len(complete)<=runtime.MAX_ENTRIES and sum(len(e.content) for e in complete)<=runtime.MAX_BYTES)
    bundle=ns.backend._seal_browser_runtime_bundle(complete)
    try:
        result=runtime.RuntimeBundle(bundle,generation,BIN+'postgres',{},native,binding.commit,binding.tree,
            runtime.POSTGRES_POLICY,None,metadata)
        result._owner=result;result._token=runtime._TOKEN
        runtime._entries(result);return result
    except BaseException as error:
        try:
            ns._require(ns.backend._close_browser_runtime_bundle_descriptor(bundle.descriptor,bundle.target,
                (bundle.device,bundle.inode)))
        except BaseException:
            raise ns.NamespaceCleanupError('postgres_capture_cleanup_refused') from error
        raise


def receive_postgres(packet):
    """Reserved phase resume path; never accepted by the diagnostic dispatcher."""
    return runtime._receive_runtime(packet,(runtime.POSTGRES_POLICY,))
