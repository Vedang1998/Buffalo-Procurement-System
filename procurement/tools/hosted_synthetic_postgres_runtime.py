"""Fixed native PG16 dependency capture into the existing sealed-root format.

This is separate from the diagnostic/application and accepted Nix/browser
closures. Capture seals bytes; it does not prove native execution, a writable
PG phase, identity, network isolation, enrollment or credential release. There
is no CLI, subprocess, credential provider or privileged activation here.
"""
from __future__ import annotations

import hashlib
import copy
import os
from pathlib import Path
import re
import resource
import time
import tracemalloc

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
        image=runtime._native_elf(path,raw)
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


def capture_postgres_phase(generation,root,binding):
    """Fixed CPython supervisor + native PG closure, never a payload grant.

    A PG-only root cannot reexec its Python guardian. V3 composes the existing
    authenticated inputs under the approved phase-only160MiB cap. Individual
    captures remain80MiB; close their owned memfds before sealing the union.
    """
    result=None
    @source._resource_operation
    def guarded():
        nonlocal result
        _source_identity(root,binding)
        diagnostic=None;postgres=None;failure=None;prepared=None
        try:
            diagnostic=runtime.capture_runtime(generation,root,binding)
            postgres=capture_postgres(generation,root,binding)
            prepared=_prepare_phase(diagnostic,postgres)
        except BaseException as error:failure=error
        errors=[]
        for value in (postgres,diagnostic):
            if value is None:continue
            try:value.close()
            except BaseException as error:errors.append(error)
        if errors:raise ns.NamespaceCleanupError('postgres_phase_capture_cleanup_refused') from (failure or errors[0])
        if failure is not None:raise failure
        # Prepared immutable bytes retain both authenticated inputs. Only this
        # owning path closes their memfds before allocating the composed image.
        ns._require(prepared is not None)
        combined=None
        try:
            combined=_seal_phase(*prepared)
            del prepared
            _source_identity(root,binding)
        except BaseException as error:
            if combined is not None:
                try:combined.close()
                except BaseException:raise ns.NamespaceCleanupError('postgres_phase_capture_cleanup_refused') from error
            raise
        result=combined
        return result
    try:return guarded()
    except BaseException as error:
        if result is not None:
            try:result.close()
            except BaseException:raise ns.NamespaceCleanupError('postgres_phase_cancel_cleanup_refused') from error
        raise


def _phase_identity(diagnostic,postgres):
    diagnostic.verify();postgres.verify()
    ns._require(diagnostic.policy==runtime.POLICY and postgres.policy==runtime.POSTGRES_POLICY
        and (diagnostic.generation,diagnostic.source_commit,diagnostic.source_tree)==
            (postgres.generation,postgres.source_commit,postgres.source_tree)
        and not postgres.source_files and postgres.executable==BIN+'postgres'
        and postgres.postgres['executables']==list(EXECUTABLES))
    native=dict(diagnostic.native_files)
    for path,digest in postgres.native_files.items():
        ns._require(path not in diagnostic.source_files and (path not in native or native[path]==digest))
        native[path]=digest
    metadata={'scope':'POSTGRES_SUPERVISOR_CAPTURE_ONLY','execution':'NOT_PROVEN','credential_release':'WITHHELD',
        'supervisor_executable':diagnostic.executable,'postgres_executables':list(EXECUTABLES),
        'diagnostic_bundle_sha256':diagnostic.bundle.sha256,'postgres_bundle_sha256':postgres.bundle.sha256,
        'native_capture':copy.deepcopy(postgres.postgres)}
    identity=runtime._identity_bytes(diagnostic.generation,diagnostic.executable,diagnostic.source_files,native,
        diagnostic.source_commit,diagnostic.source_tree,policy=runtime.POSTGRES_PHASE_POLICY,postgres=metadata)
    return native,metadata,identity


def _compose_phase(diagnostic,postgres):
    """Borrowed-input component helper; never closes the caller's captures."""
    return _seal_phase(*_prepare_phase(diagnostic,postgres))


def _prepare_phase(diagnostic,postgres):
    """Retain authenticated immutable union bytes without creating a new memfd."""
    native,metadata,identity=_phase_identity(diagnostic,postgres)
    entries={};total=0
    for value in (diagnostic,postgres):
        for entry in runtime._entries(value):
            if entry.kind!='F' or entry.path==runtime._IDENTITY_PATH:continue
            if entry.path in entries:
                ns._require(entries[entry.path]==entry)
                continue
            total+=len(entry.content)
            ns._require(total<=runtime.POSTGRES_PHASE_MAX_BYTES and len(entries)<runtime.MAX_ENTRIES)
            entries[entry.path]=entry
    entries[runtime._IDENTITY_PATH]=ns.backend._BrowserRuntimeBundleEntry(path=runtime._IDENTITY_PATH,kind='F',
        mode=0o444,user_id=0,group_id=0,provenance='audit-source:'+hashlib.sha256(identity).hexdigest(),content=identity)
    complete=ns.backend._complete_browser_runtime_bundle_parents(tuple(entries.values()))
    ns._require(len(complete)<=runtime.MAX_ENTRIES and sum(len(e.content) for e in complete)<=runtime.POSTGRES_PHASE_MAX_BYTES)
    fields=(diagnostic.generation,diagnostic.executable,dict(diagnostic.source_files),
            native,diagnostic.source_commit,diagnostic.source_tree,runtime.POSTGRES_PHASE_POLICY,None,metadata)
    return complete,fields


def _seal_phase(complete,fields):
    bundle=ns.backend._seal_browser_runtime_bundle(complete)
    try:
        result=runtime.RuntimeBundle(bundle,*fields)
        result._owner=result;result._token=runtime._TOKEN
        _phase_inventory(result)
        return result
    except BaseException as error:
        try:ns._require(ns.backend._close_browser_runtime_bundle_descriptor(bundle.descriptor,bundle.target,(bundle.device,bundle.inode)))
        except BaseException:raise ns.NamespaceCleanupError('postgres_phase_seal_cleanup_refused') from error
        raise


def measure_phase_fit(diagnostic,postgres):
    """Borrow existing captures and count the exact final union before sealing.

    Retain only content identities/sizes, not another copy of file contents.
    Include the regenerated identity and precisely the same parent directories
    as composition. A capacity observation grants no execution authority.
    """
    _,_,identity=_phase_identity(diagnostic,postgres)
    inventory={}
    for value in (diagnostic,postgres):
        for entry in runtime._entries(value):
            if entry.kind!='F' or entry.path==runtime._IDENTITY_PATH:continue
            signature=(entry.mode,entry.user_id,entry.group_id,entry.provenance,
                       hashlib.sha256(entry.content).hexdigest(),len(entry.content))
            if entry.path in inventory:ns._require(inventory[entry.path]==signature)
            inventory[entry.path]=signature
    paths=set(inventory);paths.add(runtime._IDENTITY_PATH)
    for path in tuple(paths):
        parts=path.split('/')[1:]
        paths.update('/'+'/'.join(parts[:index]) for index in range(1,len(parts)))
    total=sum(value[-1] for value in inventory.values())+len(identity)
    return {'policy':runtime.POSTGRES_PHASE_POLICY,'commit':diagnostic.source_commit,'tree':diagnostic.source_tree,
        'generation':diagnostic.generation,'diagnostic_regular_bytes':diagnostic.bundle.regular_bytes,
        'postgres_regular_bytes':postgres.bundle.regular_bytes,'composed_regular_bytes':total,
        'composed_entries':len(paths),'maximum_regular_bytes':runtime.POSTGRES_PHASE_MAX_BYTES,'maximum_entries':runtime.MAX_ENTRIES,'capacity_policy':runtime._phase_capacity_identity(),
        'diagnostic_bundle_sha256':diagnostic.bundle.sha256,'postgres_bundle_sha256':postgres.bundle.sha256,
        'input_memfd_allocated_bytes':sum(os.fstat(v.bundle.descriptor).st_blocks*512 for v in (diagnostic,postgres)),
        'input_memfd_logical_bytes':sum(v.bundle.size for v in (diagnostic,postgres)),
        'capacity':'FIT' if total<=runtime.POSTGRES_PHASE_MAX_BYTES and len(paths)<=runtime.MAX_ENTRIES else 'REFUSED',
        'account_mutations':0,'native_execution':'NOT_RUN','service_memory_fit':'NOT_PROVEN',
        'credential_release':'WITHHELD','execution_authority':False}


class PhaseCapacityRefusal(ns.NamespaceProofError):
    def __init__(self,evidence):
        super().__init__('postgres_phase_capacity_refused_before_account')
        self.evidence=evidence


@source._resource_operation
def capture_phase_fit(generation,root,binding):
    """Read-only memfd capture; no account, namespace, PG exec or service.

    Service execution must recapture/revalidate; these descriptors do not cross
    systemd's native launch chain. Close every capture before returning/refusing.
    """
    _source_identity(root,binding)
    diagnostic=postgres=None;failure=None;report=None;errors=[]
    try:
        diagnostic=runtime.capture_runtime(generation,root,binding)
        postgres=capture_postgres(generation,root,binding)
        report=measure_phase_fit(diagnostic,postgres)
        _source_identity(root,binding)
    except BaseException as error:failure=error
    for value in (postgres,diagnostic):
        if value is not None:
            try:value.close()
            except BaseException as error:errors.append(error)
    if errors:raise ns.NamespaceCleanupError('postgres_fit_capture_cleanup_refused') from (failure or errors[0])
    if failure is not None:raise failure
    ns._require(report is not None)
    return report


MEMORY_READINESS_POLICY='BUFFALO_PG_PREFLIGHT_MEMORY_V1'
SERVICE_MEMORY_MAX=536870912
_READINESS_TOKEN=object()


def _ready_require(condition,code='postgres_readiness_refused'):
    if not condition:raise ns.NamespaceProofError(code)


def _validate_memory_result(report,binding):
    try:
        m=report['memory'];values=[m[k] for k in ('process_peak_rss_bytes','child_peak_rss_bytes',
            'maximum_owned_memfd_allocation_bytes','conservative_sum_bytes','service_limit_bytes')]
        _ready_require(all(type(v) is int and 0<=v<2**63 for v in values))
        _ready_require(m['policy']==MEMORY_READINESS_POLICY and values[4]==SERVICE_MEMORY_MAX
            and values[3]==sum(values[:3]) and values[3]<=values[4]
            and m['result']=='WITHIN_COMPONENT_ENVELOPE'
            and (report['commit'],report['tree'])==(binding.commit,binding.tree)
            and report['capacity']=='FIT' and report['maximum_regular_bytes']==runtime.POSTGRES_PHASE_MAX_BYTES
            and type(report['composed_regular_bytes']) is int and 0<report['composed_regular_bytes']<=runtime.POSTGRES_PHASE_MAX_BYTES
            and type(report['composed_entries']) is int and 0<report['composed_entries']<=runtime.MAX_ENTRIES
            and report['capacity_policy']==runtime._phase_capacity_identity()
            and report['owned_descriptors_closed'] is True and report['execution_authority'] is False
            and report['adoption']=='VERIFIED_SAME_PROCESS_NOT_SERVICE'
            and all(type(report[k]) is str and re.fullmatch('[0-9a-f]{64}',report[k])
                for k in ('generation','diagnostic_bundle_sha256','postgres_bundle_sha256','bundle_sha256',
                          'manifest_sha256','native_inventory_sha256')))
    except (KeyError,TypeError,ValueError,OverflowError):
        raise ns.NamespaceProofError('postgres_readiness_evidence_malformed') from None


def _fd_inventory():
    result=[]
    for name in os.listdir('/proc/self/fd'):
        try:info=os.fstat(int(name))
        except OSError as error:
            if error.errno==__import__('errno').EBADF:continue
            raise
        result.append((int(name),info.st_dev,info.st_ino,info.st_mode))
    return tuple(sorted(result))


def memory_envelope(input_backing,phase_backing):
    """Unchanged conservative formula; never label this measured cgroup usage.

    RSS includes resident Python/native allocations (tracemalloc is a subset).
    memfd page cache can overlap RSS if mapped; descriptor aliases share backing.
    Independent maxima deliberately overcount possible overlap. Kernel charges,
    unowned processes and later tmpfs projection remain outside this observation.
    """
    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
    children=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss*1024
    _ready_require(all(type(v) is int and 0<=v<2**63 for v in (rss,children,input_backing,phase_backing)))
    backing=max(input_backing,phase_backing);total=rss+children+backing
    return {'policy':MEMORY_READINESS_POLICY,'process_peak_rss_bytes':rss,
        'child_peak_rss_bytes':children,'maximum_owned_memfd_allocation_bytes':backing,
        'conservative_sum_bytes':total,'service_limit_bytes':SERVICE_MEMORY_MAX,
        'result':'WITHIN_COMPONENT_ENVELOPE' if total<=SERVICE_MEMORY_MAX else 'REFUSED_COMPONENT_ENVELOPE',
        'scope':'PREFLIGHT_COMPONENT_NOT_SERVICE_ENFORCEMENT_OR_CGROUP_USAGE'}


def _memory_sample(stage):
    current,peak=tracemalloc.get_traced_memory()
    return {'stage':stage,'traced_current_bytes':current,'traced_peak_bytes':peak,
        'process_cumulative_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024}


class PhaseMemoryRefusal(ns.NamespaceProofError):
    def __init__(self,evidence):
        super().__init__('postgres_phase_memory_refused_before_account')
        self.evidence=evidence


def _readiness_digest(receipt):
    try:
        payload={'root':receipt.root,'commit':receipt.binding.commit,'tree':receipt.binding.tree,
            'source_files':receipt.binding.files,'deadline':receipt.deadline,
            'interpreter':receipt.interpreter,'native_files':receipt.native,'report':receipt.report}
        return hashlib.sha256(__import__('json').dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    except (AttributeError,TypeError,ValueError,OverflowError):
        raise ns.NamespaceProofError('postgres_readiness_evidence_malformed') from None


class PhaseReadiness:
    """Owner-minted result of this invocation; not a deserialized PASS/report."""
    def __init__(self):raise ns.NamespaceProofError('readiness_must_be_measured')

    def verify(self,root,binding):
        _ready_require(getattr(self,'_token',None) is _READINESS_TOKEN and self._owner is self
            and self.root==str(Path(root).resolve(strict=True)) and self.binding==binding
            and time.monotonic()<self.deadline,'postgres_readiness_binding_refused')
        with source.source_deadline(self.deadline):_source_identity(root,binding)
        _ready_require(self.interpreter==str(Path('/proc/self/exe').resolve(strict=True)),
                    'postgres_readiness_interpreter_refused')
        _ready_require(_readiness_digest(self)==self.digest,'postgres_readiness_report_changed')
        report=self.report
        _validate_memory_result(report,binding)
        _ready_require(report['capacity']=='FIT' and report['memory']['result']=='WITHIN_COMPONENT_ENVELOPE'
            and report['memory']['policy']==MEMORY_READINESS_POLICY and report['owned_descriptors_closed'] is True
            and (report['commit'],report['tree'])==(binding.commit,binding.tree)
            and report['interpreter']==self.interpreter
            and report['native_inventory_sha256']==hashlib.sha256(__import__('json').dumps(self.native,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            'postgres_readiness_result_refused')
        # Reuse never substitutes a stale native/source identity. These reads
        # are serial (at most one file), without another capture/clone/union.
        for path,digest in self.native.items():
            _ready_require(time.monotonic()<self.deadline,'postgres_readiness_deadline')
            # PG's historical native_files map also includes fixed generated
            # configuration. Re-derive those source-owned bytes; never read the
            # host passwd/group/hosts/OpenSSL files as measurement evidence.
            if path in _FIXED:data=_FIXED[path]
            else:data,_=runtime.read_native(path)
            _ready_require(hashlib.sha256(data).hexdigest()==digest,'postgres_readiness_native_changed')
        return self.report


def _fresh_generation():return hashlib.sha256(os.urandom(32)).hexdigest()


@source._resource_operation
def require_phase_fit(root,binding, *, deadline=None, generation=None):
    """Actual pre-account capacity AND memory decision; no supplied result accepted.

    One capture per input, ownership-bound closure before return on every path.
    The same canonical composition/receiver is exercised. Later service limits
    and measurements remain mandatory and are never inferred from this receipt.
    """
    started=time.monotonic()
    deadline=started+60 if deadline is None else min(deadline,started+60)
    _ready_require(type(deadline) in (int,float) and started<deadline
                and not tracemalloc.is_tracing(),'postgres_readiness_precondition_refused')
    with source.source_deadline(deadline):_source_identity(root,binding)
    before_fds=_fd_inventory()
    generation=_fresh_generation() if generation is None else generation
    _ready_require(type(generation) is str and re.fullmatch('[0-9a-f]{64}',generation),'postgres_readiness_generation_refused')
    diagnostic=postgres=phase=received=None;descriptor=None;prepared=None
    failure=None;report=None;stages=[];native=None;interpreter=None
    tracemalloc.start()
    try:
        with source.source_deadline(deadline):diagnostic=runtime.capture_runtime(generation,root,binding)
        stages.append(_memory_sample('DIAGNOSTIC_CAPTURE'))
        with source.source_deadline(deadline):postgres=capture_postgres(generation,root,binding)
        stages.append(_memory_sample('POSTGRES_CAPTURE'))
        report=measure_phase_fit(diagnostic,postgres)
        _ready_require(report['generation']==generation,'postgres_readiness_generation_refused')
        if report['capacity']!='FIT':raise PhaseCapacityRefusal(report)
        _ready_require(time.monotonic()<deadline,'postgres_readiness_deadline')
        prepared=_prepare_phase(diagnostic,postgres)
        stages.append(_memory_sample('COMPOSITION_PREPARED'))
        postgres.close();postgres=None;diagnostic.close();diagnostic=None
        phase=_seal_phase(*prepared);prepared=None
        stages.append(_memory_sample('SEALED_SERIALIZATION'))
        phase_backing=os.fstat(phase.bundle.descriptor).st_blocks*512
        packet=phase.packet();descriptor=os.dup(phase.bundle.descriptor)
        packet['bundle']['descriptor']=descriptor
        received=receive_postgres_phase(packet);descriptor=None
        _ready_require(received.bundle.sha256==phase.bundle.sha256)
        native=dict(phase.native_files);interpreter=phase.executable
        stages.append(_memory_sample('DESCRIPTOR_ADOPTION'))
        report.update(memory=memory_envelope(report['input_memfd_allocated_bytes'],phase_backing),
            bundle_sha256=phase.bundle.sha256,manifest_sha256=phase.bundle.manifest_sha256,
            native_inventory_sha256=hashlib.sha256(__import__('json').dumps(native,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            interpreter=interpreter,adoption='VERIFIED_SAME_PROCESS_NOT_SERVICE',
            service_memory_enforcement='NOT_PROVEN',memory_stages=stages)
        with source.source_deadline(deadline):_source_identity(root,binding)
        _ready_require(time.monotonic()<deadline,'postgres_readiness_deadline')
    except BaseException as error:failure=error
    errors=[];prepared=None
    if descriptor is not None:
        try:os.fstat(descriptor)
        except OSError as error:
            if error.errno!=__import__('errno').EBADF:errors.append(error)
        else:
            try:_ready_require(ns.backend._close_browser_runtime_bundle_descriptor(descriptor,phase.bundle.target,(phase.bundle.device,phase.bundle.inode)))
            except BaseException as error:errors.append(error)
    for value in (received,phase,postgres,diagnostic):
        if value is not None:
            try:value.close()
            except BaseException as error:errors.append(error)
    try:stages.append(_memory_sample('OWNED_CLEANUP'))
    finally:tracemalloc.stop()
    if _fd_inventory()!=before_fds:errors.append(ns.NamespaceCleanupError('postgres_readiness_fd_inventory_changed'))
    if errors:raise ns.NamespaceCleanupError('postgres_readiness_cleanup_refused') from (failure or errors[0])
    if failure is not None:raise failure
    _ready_require(report is not None)
    report.update(owned_descriptors_closed=True,elapsed_seconds=time.monotonic()-started)
    if report['memory']['result']!='WITHIN_COMPONENT_ENVELOPE':raise PhaseMemoryRefusal(report)
    _validate_memory_result(report,binding)
    receipt=object.__new__(PhaseReadiness);receipt._owner=receipt;receipt._token=_READINESS_TOKEN
    receipt.root=str(Path(root).resolve(strict=True));receipt.binding=binding;receipt.deadline=deadline
    receipt.interpreter=interpreter;receipt.native=native;receipt.report=report
    receipt.digest=_readiness_digest(receipt)
    return receipt


def _phase_inventory(value):
    value.verify()
    ns._require(value.policy==runtime.POSTGRES_PHASE_POLICY)
    metadata=value.postgres
    ns._require(set(metadata)=={'scope','execution','credential_release','supervisor_executable','postgres_executables',
        'diagnostic_bundle_sha256','postgres_bundle_sha256','native_capture'}
        and metadata['scope']=='POSTGRES_SUPERVISOR_CAPTURE_ONLY' and metadata['execution']=='NOT_PROVEN'
        and metadata['credential_release']=='WITHHELD' and metadata['supervisor_executable']==value.executable
        and metadata['postgres_executables']==list(EXECUTABLES)
        and type(metadata['native_capture']) is dict and metadata['native_capture']['executables']==list(EXECUTABLES)
        and metadata['native_capture']['locale_files']==list(_LOCALE)
        and all(type(metadata[name]) is str and re.fullmatch('[0-9a-f]{64}',metadata[name])
            for name in ('diagnostic_bundle_sha256','postgres_bundle_sha256')))
    entries={e.path:e for e in runtime._entries(value)}
    ns._require(all('/usr/lib/locale/C.utf8/'+name in value.native_files
        and entries['/usr/lib/locale/C.utf8/'+name].mode==0o444 for name in _LOCALE))
    ns._require(value.executable not in EXECUTABLES)
    expected_source={runtime.PROJECTED_ROOT+'/'+p:digest for p,digest in metadata['native_capture']['source_files'].items()
                     if p.endswith('.py') or p in ('procurement/tools/hosted_native_inputs.json','procurement/tools/hosted_native_runpath.json','procurement/tools/hosted_native_proposed_inventory.json')}
    ns._require(expected_source==value.source_files)
    for path in (value.executable,*EXECUTABLES):
        ns._require(path in value.native_files and entries[path].mode==0o555
            and runtime._native_elf(path,entries[path].content)['interpreter'] in value.native_files)
    return entries


def receive_postgres_phase(packet):
    value=runtime._receive_runtime(packet,(runtime.POSTGRES_PHASE_POLICY,))
    try:_phase_inventory(value);return value
    except BaseException as error:
        try:value.close()
        except BaseException:raise ns.NamespaceCleanupError('postgres_phase_resume_cleanup_refused') from error
        raise


def enter_postgres_phase(value):
    """For the already bound private-mount U1 guardian before I/W exist.

    Caller must retain the parent handshake and original/current namespace
    proof. These local entry checks alone do not prove exclusive mount use.
    No activation or standalone launcher authority is supplied here.
    """
    _phase_inventory(value);ns._require_mapped_identity()
    from hosted_synthetic_cgroup import _signal_boundary
    _signal_boundary()
    ns._require(os.getpid()>1 and ns._read(f'/proc/self/task/{os.getpid()}/children',4096)==b'')
    return runtime._enter_projection(value)


def verify_postgres_phase_consumer(value,root_observation):
    """Actual projected CPython/maps proof, not PostgreSQL execution proof."""
    _phase_inventory(value)
    return runtime._verify_consumer(value,root_observation,(runtime.POSTGRES_PHASE_POLICY,))


def verify_postgres_phase_report(value,proof):
    _phase_inventory(value)
    return runtime._verify_report_binding(value,proof,(runtime.POSTGRES_PHASE_POLICY,))
