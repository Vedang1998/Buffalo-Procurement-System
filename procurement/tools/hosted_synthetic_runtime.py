"""Hosted diagnostic CPython closure; distinct from all frozen Nix/browser pins.

Snapshot the actual root-owned interpreter/stdlib and recursively resolved ELF
dependencies without executing a resolver. Seal bytes using the existing bundle
primitive. Only a later sealed-root exec/consumer attestation establishes use.
No application/Node/Chromium runtime completeness is inferred from this policy.
"""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import sysconfig

import hosted_synthetic_namespace as ns


POLICY = 'BUFFALO_HOSTED_STDLIB_RUNTIME_V1'
APPLICATION_POLICY = 'BUFFALO_HOSTED_APPLICATION_RUNTIME_V1'
POSTGRES_POLICY = 'BUFFALO_HOSTED_POSTGRES_RUNTIME_V1'
POSTGRES_PHASE_POLICY = 'BUFFALO_HOSTED_POSTGRES_PHASE_RUNTIME_V1'
PROJECTED_ROOT = '/opt/buffalo-hosted'
PROJECTED_TOOLS = PROJECTED_ROOT + '/procurement/tools'
SENTINEL = '/buffalo-runtime/sentinel'
MAX_BYTES = 80 * 1024**2
MAX_ENTRIES = 3000
_TOKEN = object()
_IDENTITY_PATH = '/buffalo-runtime/identity.json'
_SKIP_DIRS = frozenset(('__pycache__', 'test', 'tests', 'site-packages', 'dist-packages', 'ensurepip', 'idlelib'))
_SKIP_FILES = frozenset(('sitecustomize.py', 'usercustomize.py'))
_ELF_FORBIDDEN_TAGS = frozenset((0x6FFFFEFC, 0x6FFFFEFB, 0x7FFFFFFF, 0x7FFFFFFD))


def _path(value):
    ns._require(type(value) is str and re.fullmatch(r'/[A-Za-z0-9_./+\-]+', value)
                and '..' not in Path(value).parts and str(Path(value)) == value)
    return value


def parse_elf(data, *, expected_rpath=None):
    """Strict ELF64 little-endian x86-64 loader metadata, never ldd/exec."""
    ns._require(type(data) is bytes and 64 <= len(data) <= MAX_BYTES
                and data[:7] == b'\x7fELF\x02\x01\x01'
                and struct.unpack_from('<HHI', data, 16) in ((2, 62, 1), (3, 62, 1)))
    phoff = struct.unpack_from('<Q', data, 32)[0]
    ehsize, phsize, count = struct.unpack_from('<HHH', data, 52)
    ns._require(ehsize == 64 and phsize == 56 and 0 < count <= 128
                and 64 <= phoff <= len(data) - phsize*count)
    loads=[]; dynamic=[]; interpreters=[]
    for index in range(count):
        kind, flags, offset, virtual, physical, size, memory, align = struct.unpack_from('<IIQQQQQQ', data, phoff+index*56)
        ns._require(offset <= len(data) and size <= len(data)-offset and (kind != 1 or size <= memory))
        if kind == 1:loads.append((virtual, offset, size))
        elif kind == 2:dynamic.append((offset,size))
        elif kind == 3:
            raw=data[offset:offset+size]
            ns._require(1 < size <= 4096 and raw[-1:] == b'\0' and b'\0' not in raw[:-1])
            try:interpreters.append(_path(raw[:-1].decode('ascii')))
            except UnicodeError:raise ns.NamespaceProofError('elf_interpreter_refused') from None
    ns._require(loads and len(dynamic) <= 1 and len(interpreters) <= 1)
    needed=[]; tags={}
    if dynamic:
        offset,size=dynamic[0]
        ns._require(size % 16 == 0 and 0 < size <= 65536)
        terminated=False
        for start in range(offset,offset+size,16):
            tag,value=struct.unpack_from('<qQ',data,start)
            if tag==0:terminated=True;break
            ns._require(tag not in _ELF_FORBIDDEN_TAGS)
            if tag==1:needed.append(value)
            elif tag in (5,10,14,15,29):
                ns._require(tag not in tags);tags[tag]=value
        ns._require(terminated and 29 not in tags and ((15 in tags)==(expected_rpath is not None)))
        ns._require((5 in tags)==(10 in tags))
        if needed or 14 in tags or 15 in tags:ns._require(5 in tags)
        if 5 in tags:
            address,length=tags[5],tags[10]
            matches=[off+address-virtual for virtual,off,size in loads
                     if virtual <= address and address-virtual <= size and length <= size-(address-virtual)]
            ns._require(len(matches)==1 and 0 < length <= 4*1024**2)
            strings=data[matches[0]:matches[0]+length]
            def name(index):
                ns._require(index < len(strings))
                end=strings.find(b'\0',index)
                ns._require(end > index and end-index <= 255)
                raw=strings[index:end]
                ns._require(re.fullmatch(rb'[A-Za-z0-9_+.-]+',raw) is not None and raw not in (b'.',b'..'))
                return raw.decode('ascii')
            if 15 in tags:
                index=tags[15];ns._require(index < len(strings))
                end=strings.find(b'\0',index)
                ns._require(end>index and strings[index:end]==expected_rpath.encode('ascii'))
            needed=[name(index) for index in needed]
            ns._require(len(needed)==len(set(needed)))
            soname=name(tags[14]) if 14 in tags else None
        else:soname=None
    else:soname=None
    return {'interpreter': interpreters[0] if interpreters else None,
            'needed': tuple(needed), 'soname': soname}


def _metadata(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid,
            info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def read_native(path):
    """Flatten a bounded, root-owned alias to immutable bytes, preserving name."""
    requested=Path(_path(str(path)))
    # Validate both alias parents and the canonical chain. A writable ancestor
    # can replace a root-owned final inode and is never accepted as trust.
    for selected in (requested,*requested.parents):
        info=selected.lstat()
        ns._require(info.st_uid==0 and (stat.S_ISLNK(info.st_mode) or not info.st_mode & 0o022))
    resolved=requested.resolve(strict=True)
    for selected in (resolved,*resolved.parents):
        info=selected.lstat()
        ns._require(info.st_uid==0 and not info.st_mode & 0o022 and not stat.S_ISLNK(info.st_mode))
    before=resolved.lstat()
    ns._require(stat.S_ISREG(before.st_mode) and not before.st_mode & 0o6000
                and 0 <= before.st_size <= MAX_BYTES)
    fd=ns._acquire(lambda:os.open(resolved,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW))
    try:
        ns._require(_metadata(os.fstat(fd.number))==_metadata(before))
        try:os.getxattr(fd.number,'security.capability')
        except OSError as error:
            import errno
            ns._require(error.errno==errno.ENODATA)
        else:raise ns.NamespaceProofError('runtime_file_capability_refused')
        data=os.pread(fd.number,before.st_size+1,0)
        ns._require(len(data)==before.st_size and requested.resolve(strict=True)==resolved
                    and _metadata(before)==_metadata(os.fstat(fd.number))==_metadata(resolved.lstat()))
        return data, 0o555 if before.st_mode & 0o111 else 0o444
    finally:fd.close()


def _library_roots():
    values=(sysconfig.get_config_var('LIBDIR'),'/lib/x86_64-linux-gnu','/usr/lib/x86_64-linux-gnu',
            '/lib64','/usr/lib64','/lib','/usr/lib')
    roots=[]
    for value in values:
        if value and Path(value).is_dir():
            selected=Path(_path(value)).resolve(strict=True)
            if selected not in roots:roots.append(selected)
    return tuple(roots)


def _resolve_library(name, roots):
    candidates=[]
    for root in roots:
        path=root/name
        if os.path.lexists(path):
            data,mode=read_native(path)
            candidates.append((str(path),data,mode))
    ns._require(candidates and len({hashlib.sha256(data).digest() for _,data,_ in candidates})==1)
    return candidates[0]


def _selected_stdlib(root):
    files=[];seen=set();total=0
    ns._require(root.is_absolute() and root.name=='python3.13' and not root.is_symlink())
    for parent, directories, names in os.walk(root,followlinks=False):
        info=Path(parent).lstat()
        ns._require(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode & 0o022)
        directories[:]=sorted(name for name in directories if name not in _SKIP_DIRS and not name.startswith('config-'))
        ns._require(all(not (Path(parent)/name).is_symlink() for name in directories))
        for name in sorted(names):
            if name in _SKIP_FILES or name.endswith(('.pyc','.pyo')) or name.startswith(('_test','_ctypes_test','_xxtest','xxlimited','xxsubtype')):continue
            path=Path(parent)/name
            data,mode=read_native(path)
            ns._require(str(path) not in seen);seen.add(str(path))
            files.append((str(path),data,mode));total+=len(data)
            ns._require(len(files)<=MAX_ENTRIES and total<=MAX_BYTES)
    return files


@dataclass
class RuntimeBundle:
    bundle: object
    generation: str
    executable: str
    source_files: dict
    native_files: dict
    source_commit: str
    source_tree: str
    policy: str = POLICY
    application: dict | None = None
    postgres: dict | None = None

    def verify(self):
        ns._require(getattr(self,'_owner',None) is self and getattr(self,'_token',None) is _TOKEN
                    and self.policy in (POLICY,APPLICATION_POLICY,POSTGRES_POLICY,POSTGRES_PHASE_POLICY)
                    and ((self.policy==POLICY and self.application is None)
                         or (self.policy==APPLICATION_POLICY and type(self.application) is dict)
                         or (self.policy in (POSTGRES_POLICY,POSTGRES_PHASE_POLICY) and self.application is None))
                    and ((self.policy in (POSTGRES_POLICY,POSTGRES_PHASE_POLICY) and type(self.postgres) is dict)
                         or (self.policy not in (POSTGRES_POLICY,POSTGRES_PHASE_POLICY) and self.postgres is None))
                    and re.fullmatch('[0-9a-f]{64}',self.generation)
                    and re.fullmatch('[0-9a-f]{40}',self.source_commit)
                    and re.fullmatch('[0-9a-f]{40}',self.source_tree))
        ns.backend._validate_pinned_browser_runtime_bundle(self.bundle)
        ns._require(self.bundle.regular_bytes<=MAX_BYTES and self.bundle.entries<=MAX_ENTRIES)

    def close(self):
        self.verify()
        ns._require(ns.backend._close_browser_runtime_bundle_descriptor(self.bundle.descriptor,self.bundle.target,
                    (self.bundle.device,self.bundle.inode)))
        self._token=None

    def packet(self):
        self.verify()
        # The inventory lives inside the sealed bytes, not the bounded control
        # protocol. Bind that descriptor and reconstruct its canonical identity.
        return {'generation':self.generation,'policy':self.policy,
                'source_commit':self.source_commit,'source_tree':self.source_tree,
                'bundle':{name:getattr(self.bundle,name) for name in ('descriptor','target','device','inode','size',
                    'sha256','manifest_sha256','entries','regular_bytes','execution_authority')}}


def receive_runtime(packet):
    """Only from the exact sealed resume channel, never an ambient CLI JSON."""
    return _receive_runtime(packet,(POLICY,APPLICATION_POLICY))


def _receive_runtime(packet, policies):
    ns._require(type(packet) is dict and set(packet)=={'generation','policy','bundle','source_commit','source_tree'}
                and packet['policy'] in policies and type(packet['bundle']) is dict
                and set(packet['bundle'])=={'descriptor','target','device','inode','size','sha256','manifest_sha256',
                                          'entries','regular_bytes','execution_authority'})
    bundle=ns.backend._PinnedBrowserRuntimeBundle(**packet['bundle'])
    ns._require(type(bundle.descriptor) is int and bundle.descriptor>2)
    # Verify ownership before mutating CLOEXEC: a stale number may have been
    # reused for an unrelated descriptor which must remain entirely untouched.
    info=os.fstat(bundle.descriptor)
    ns._require(stat.S_ISREG(info.st_mode) and info.st_nlink==0
                and (info.st_dev,info.st_ino,info.st_size)==(bundle.device,bundle.inode,bundle.size)
                and (info.st_uid,info.st_gid)==(os.geteuid(),os.getegid())
                and stat.S_IMODE(info.st_mode)==ns.backend._BROWSER_RUNTIME_BUNDLE_MODE
                and os.readlink(f'/proc/self/fd/{bundle.descriptor}')==bundle.target
                and ns.backend._BROWSER_RUNTIME_BUNDLE_MEMFD_TARGET.fullmatch(bundle.target)
                and fcntl.fcntl(bundle.descriptor,fcntl.F_GETFL)&(os.O_ACCMODE|os.O_NONBLOCK)==os.O_RDONLY
                and fcntl.fcntl(bundle.descriptor,fcntl.F_GET_SEALS)==ns.backend._BROWSER_RUNTIME_BUNDLE_SEALS)
    # Ownership transfers only after the complete kernel descriptor identity
    # above matches. Foreign/mismatched descriptors remain untouched.
    try:
        return _adopt_runtime(packet,bundle)
    except BaseException as error:
        try:
            ns._require(ns.backend._close_browser_runtime_bundle_descriptor(bundle.descriptor,bundle.target,
                (bundle.device,bundle.inode)))
        except BaseException:
            raise ns.NamespaceCleanupError('runtime_resume_cleanup_refused') from error
        raise


def _adopt_runtime(packet,bundle):
    os.set_inheritable(bundle.descriptor,False)
    bundle._owner_token=ns.backend._BROWSER_HANDLE_TOKEN
    ns.backend._validate_pinned_browser_runtime_bundle(bundle)
    ns._require(bundle.regular_bytes<=MAX_BYTES and bundle.entries<=MAX_ENTRIES)
    raw=ns.backend._read_browser_runtime_bundle_descriptor(bundle.descriptor,bundle.size)
    entries,manifest,digest,size=ns.backend._parse_browser_runtime_bundle(raw)
    ns._require((manifest,digest,size)==(bundle.manifest_sha256,bundle.sha256,bundle.regular_bytes))
    identities=[entry.content for entry in entries if entry.path==_IDENTITY_PATH and entry.kind=='F']
    ns._require(len(identities)==1 and len(identities[0])<=512*1024)
    identity=json.loads(identities[0])
    ns._require(type(identity) is dict and set(identity)==({'policy','generation','executable','source_files','native_files','source_commit','source_tree'}
                    | ({'application'} if packet['policy']==APPLICATION_POLICY else
                       {'postgres'} if packet['policy'] in (POSTGRES_POLICY,POSTGRES_PHASE_POLICY) else set()))
                and all(identity[key]==packet[key] for key in ('policy','generation','source_commit','source_tree'))
                and json.dumps(identity,sort_keys=True,separators=(',',':'),allow_nan=False).encode('ascii')==identities[0])
    result=RuntimeBundle(bundle,packet['generation'],identity['executable'],identity['source_files'],identity['native_files'],
                         packet['source_commit'],packet['source_tree'],packet['policy'],identity.get('application'),identity.get('postgres'))
    result._owner=result;result._token=_TOKEN
    _entries(result)
    return result


def _identity_bytes(generation,executable,source,native,commit,tree, *, policy=POLICY,application=None,postgres=None):
    value={'policy':policy,'generation':generation,'executable':executable,
        'source_files':source,'native_files':native,'source_commit':commit,'source_tree':tree}
    if policy==APPLICATION_POLICY:
        ns._require(type(application) is dict and postgres is None);value['application']=application
    elif policy in (POSTGRES_POLICY,POSTGRES_PHASE_POLICY):
        ns._require(type(postgres) is dict and application is None);value['postgres']=postgres
    else:ns._require(policy==POLICY and application is None and postgres is None)
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode('ascii')


def capture_runtime(generation, source_root, binding):
    """Bound diagnostic closure, not arbitrary caller-selected filesystem input."""
    from hosted_synthetic_source import HostedSourceBinding, SOURCE_PATHS
    ns._require(sys.version_info[:2]==(3,13) and os.uname().machine=='x86_64'
                and type(generation) is str and re.fullmatch('[0-9a-f]{64}',generation)
                and type(binding) is HostedSourceBinding)
    root=Path(source_root).resolve(strict=True)
    executable=str(Path('/proc/self/exe').resolve(strict=True))
    stdlib=Path(sysconfig.get_path('stdlib')).resolve(strict=True)
    pending=[(executable,*read_native(executable)),*_selected_stdlib(stdlib)]
    native={};contents={};roots=_library_roots();total=0
    while pending:
        path,data,mode=pending.pop()
        if path in contents:
            ns._require(contents[path]==(data,mode));continue
        contents[path]=(data,mode);total+=len(data)
        ns._require(total<=MAX_BYTES and len(contents)<=MAX_ENTRIES)
        native[path]=hashlib.sha256(data).hexdigest()
        if data.startswith(b'\x7fELF'):
            elf=parse_elf(data)
            if path==executable:ns._require(elf['interpreter'] is not None)
            if elf['interpreter']:
                interpreter=elf['interpreter'];pending.append((interpreter,*read_native(interpreter)))
            pending.extend(_resolve_library(name,roots) for name in elf['needed'])
    entries=[ns.backend._BrowserRuntimeBundleEntry(path=path,kind='F',mode=mode,user_id=0,group_id=0,
               provenance='native-runtime:'+native[path],content=data) for path,(data,mode) in contents.items()]
    expected=dict(binding.files);source={}
    for relative in SOURCE_PATHS:
        if not relative.endswith('.py'):continue
        path=root/relative
        fd=ns._acquire(lambda:os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW))
        try:
            before=os.fstat(fd.number)
            ns._require(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and not before.st_mode & 0o6022
                        and 0 < before.st_size <= 2*1024**2)
            data=os.pread(fd.number,before.st_size+1,0)
            digest=hashlib.sha256(data).hexdigest()
            ns._require(len(data)==before.st_size and digest==expected.get(relative)
                        and _metadata(before)==_metadata(os.fstat(fd.number))==_metadata(path.lstat()))
        finally:fd.close()
        projected=PROJECTED_ROOT+'/'+relative
        source[projected]=digest
        entries.append(ns.backend._BrowserRuntimeBundleEntry(path=projected,kind='F',mode=0o444,
            user_id=0,group_id=0,provenance='audit-source:'+digest,content=data))
    from hosted_synthetic_projection import sentinel_bytes
    sentinel=sentinel_bytes()
    entries.append(ns.backend._BrowserRuntimeBundleEntry(path=SENTINEL,kind='F',mode=0o555,
        user_id=0,group_id=0,provenance='audit-source:'+hashlib.sha256(sentinel).hexdigest(),content=sentinel))
    identity=_identity_bytes(generation,executable,source,native,binding.commit,binding.tree)
    entries.append(ns.backend._BrowserRuntimeBundleEntry(path=_IDENTITY_PATH,kind='F',mode=0o444,
        user_id=0,group_id=0,provenance='audit-source:'+hashlib.sha256(identity).hexdigest(),content=identity))
    entries=ns.backend._complete_browser_runtime_bundle_parents(tuple(entries))
    ns._require(len(entries)<=MAX_ENTRIES and sum(len(e.content) for e in entries)<=MAX_BYTES)
    bundle=ns.backend._seal_browser_runtime_bundle(entries)
    try:
        result=RuntimeBundle(bundle,generation,executable,source,native,binding.commit,binding.tree)
        result._owner=result;result._token=_TOKEN
        result.verify();return result
    except BaseException as error:
        try:ns._require(ns.backend._close_browser_runtime_bundle_descriptor(bundle.descriptor,bundle.target,(bundle.device,bundle.inode)))
        except BaseException:raise ns.NamespaceCleanupError('runtime_capture_cleanup_refused') from error
        raise


def _entries(runtime):
    runtime.verify()
    raw=ns.backend._read_browser_runtime_bundle_descriptor(runtime.bundle.descriptor,runtime.bundle.size)
    entries,manifest,digest,size=ns.backend._parse_browser_runtime_bundle(raw)
    ns._require((manifest,digest,size)==(runtime.bundle.manifest_sha256,runtime.bundle.sha256,runtime.bundle.regular_bytes))
    regular={entry.path:hashlib.sha256(entry.content).hexdigest() for entry in entries if entry.kind=='F'}
    identity=_identity_bytes(runtime.generation,runtime.executable,runtime.source_files,runtime.native_files,
                             runtime.source_commit,runtime.source_tree,policy=runtime.policy,application=runtime.application,postgres=runtime.postgres)
    expected=dict(runtime.native_files,**runtime.source_files)
    from hosted_synthetic_projection import sentinel_bytes
    ns._require(_IDENTITY_PATH not in expected and SENTINEL not in expected)
    expected[_IDENTITY_PATH]=hashlib.sha256(identity).hexdigest()
    expected[SENTINEL]=hashlib.sha256(sentinel_bytes()).hexdigest()
    ns._require(not set(runtime.native_files).intersection(runtime.source_files)
                and regular==expected
                and all(entry.kind in ('D','F') and entry.user_id==entry.group_id==0
                        and entry.mode in (0o444,0o555) for entry in entries)
                and runtime.executable in runtime.native_files)
    paths={entry.path for entry in entries}
    ns._require('/proc' not in paths and '/tmp' not in paths and '/sys' not in paths)
    policy_hash=hashlib.sha256(runtime.policy.encode()).hexdigest()
    return (*entries,*(ns.backend._BrowserRuntimeBundleEntry(path=path,kind='D',mode=0o555,
        user_id=0,group_id=0,provenance='bundle-parent:'+policy_hash,content=b'') for path in ('/proc','/tmp')))


def _verify_materialized(root, entries):
    root.verify()
    expected={entry.path:entry for entry in entries}
    actual=set(ns.backend._browser_projection_inventory(Path(f'/proc/self/fd/{root.number}')))
    ns._require(actual==set(expected)|{'/'} and ns.backend._browser_fstatfs_magic(root.number)==0x01021994)
    for path,entry in expected.items():
        info=os.stat(path[1:],dir_fd=root.number,follow_symlinks=False)
        ns._require(info.st_dev==root.identity[0] and info.st_uid==info.st_gid==0
                    and stat.S_IMODE(info.st_mode)==entry.mode)
        if entry.kind=='D':ns._require(stat.S_ISDIR(info.st_mode))
        else:
            ns._require(entry.kind=='F' and stat.S_ISREG(info.st_mode) and info.st_nlink==1)
            descriptor=ns._acquire(lambda:os.open(path[1:],os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW,dir_fd=root.number))
            try:
                ns._require(os.pread(descriptor.number,len(entry.content)+1,0)==entry.content)
            finally:descriptor.close()
    flags=os.fstatvfs(root.number).f_flag
    ns._require(flags & os.ST_RDONLY and flags & os.ST_NOSUID and flags & os.ST_NODEV
                and not flags & os.ST_NOEXEC)
    try:fd=os.open('write-must-refuse',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC,0o600,dir_fd=root.number)
    except OSError as error:ns._require(error.errno==errno.EROFS)
    else:os.close(fd);raise ns.NamespaceProofError('runtime_root_writable')


def enter_runtime(runtime):
    """Irreversible transition in the disposable mapped guardian only.

    Reuses detached-tmpfs/seal/move primitives. The old root is detached only in
    this private mount namespace. Any post-pivot failure requires this owned
    process to exit; its parent retains process/namespace cleanup ownership.
    """
    runtime.verify();ns._require(runtime.policy in (POLICY,APPLICATION_POLICY));ns._require_mapped_identity()
    return _enter_projection(runtime)


def _enter_projection(runtime):
    """Kernel projection primitive; host entry identity is checked by caller.

    Direct component tests may exercise this in a disposable mapped namespace,
    without claiming the outer launcher or supplementary-group gates passed.
    """
    runtime.verify()
    ns._require(os.geteuid()==os.getegid()==0)
    for kind in ('uid','gid'):
        mapping=ns._read('/proc/self/'+kind+'_map',128).split()
        ns._require(len(mapping)==3 and mapping[0]==b'0' and mapping[2]==b'1' and ns._integer(mapping[1])>0)
    ns.backend._browser_mount(None,b'/',None,(1<<18)|16384,None)
    owners=[];root=None;attached=False;pivoted=False;body_error=None;result=None
    try:
        old=ns._acquire(lambda:os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW));owners.append(old)
        old_mount=ns._mount_id(old)
        target=ns._acquire(lambda:os.open('/tmp',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW));owners.append(target)
        # This clones the already available G proc mount; it grants no new
        # proc-view authority. I later replaces it with its own private proc.
        proc=ns._acquire(lambda:ns.backend._browser_syscall(428,ctypes.c_int(-100),
            ctypes.c_char_p(b'/proc'),ctypes.c_uint(1|os.O_CLOEXEC)));owners.append(proc)
        numbers=ns.backend._open_detached_browser_projection_mount()
        try:
            for number in numbers:owners.append(ns._Descriptor.capture(number))
        except BaseException:
            for number in numbers:
                if not any(item.number==number for item in owners):os.close(number)
            raise
        filesystem,mount,root=owners[-3:]
        entries=_entries(runtime)
        ns.backend._materialize_browser_projection_entries(Path(f'/proc/self/fd/{root.number}'),root.number,entries)
        os.fchmod(root.number,0o555)
        ns.backend._seal_detached_browser_projection_mount(mount.number)
        _verify_materialized(root,entries)
        destination=ns._acquire(lambda:os.open('proc',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW,dir_fd=root.number))
        owners.append(destination)
        ns.backend._attach_detached_browser_projection_mount(proc.number,destination.number)
        target.verify();ns._require(target.identity==ns._identity(os.stat('/tmp')))
        ns.backend._attach_detached_browser_projection_mount(mount.number,target.number);attached=True
        ns._require(root.identity==ns._identity(os.stat('/tmp')))
        os.fchdir(root.number)
        # Linux's documented pivot_root(".", ".") stacks old/new roots. Use
        # the retained original mount identity instead of guessing which side
        # of that stack a cwd/path lookup selects.
        ns.backend._browser_syscall(155,ctypes.c_char_p(b'.'),ctypes.c_char_p(b'.'));pivoted=True
        old.verify();root.verify()
        ns._require(ns._identity(os.stat(f'/proc/self/fd/{old.number}'))==old.identity)
        named_old=ns._acquire(lambda:os.open(f'/proc/self/fd/{old.number}',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC))
        try:ns._require(named_old.identity==old.identity and ns._mount_id(named_old)==ns._mount_id(old)==old_mount)
        finally:named_old.close()
        ns.backend._browser_unmount(f'/proc/self/fd/{old.number}'.encode(),2)
        os.chdir('/')
        ns._require(ns._identity(os.stat('/'))==ns._identity(os.stat('.'))==root.identity)
        from hosted_synthetic_projection import _mount_rows
        ns._require(old_mount not in {number for number,parent in _mount_rows()})
        result={'policy':runtime.policy,'generation':runtime.generation,'bundle_sha256':runtime.bundle.sha256,
                'manifest_sha256':runtime.bundle.manifest_sha256,'root_identity':list(root.identity),
                'root_mount_id':ns._mount_id(root),'old_root_detached':True,'regular_bytes':runtime.bundle.regular_bytes,
                'consumer_exec_verified':False,'execution_authority':False}
    except BaseException as error:body_error=error
    errors=[]
    if attached and not pivoted and root is not None:
        try:
            root.verify();ns._require(ns._identity(os.stat('/tmp'))==root.identity)
            ns.backend._browser_unmount(b'/tmp',2)
        except BaseException as error:errors.append(error)
    for descriptor in reversed(owners):
        try:descriptor.close()
        except BaseException as error:errors.append(error)
    if errors:raise ns.NamespaceCleanupError('runtime_projection_cleanup_refused') from (body_error or errors[0])
    if body_error is not None:raise body_error
    ns._require(result is not None)
    return result


def projected_images(runtime):
    """Open the new image/loader; the old /proc/self/exe is not its identity."""
    _entries(runtime)
    executable=ns._acquire(lambda:os.open(runtime.executable,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW))
    try:
        data=os.pread(executable.number,MAX_BYTES+1,0)
        ns._require(hashlib.sha256(data).hexdigest()==runtime.native_files[runtime.executable])
        interpreter=parse_elf(data)['interpreter']
        ns._require(interpreter in runtime.native_files)
        loader=ns._acquire(lambda:os.open(interpreter,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW))
        try:
            ns._require(hashlib.sha256(os.pread(loader.number,MAX_BYTES+1,0)).hexdigest()==runtime.native_files[interpreter])
            return executable,dict(executable=list(executable.identity),loader=list(loader.identity))
        finally:loader.close()
    except BaseException:
        executable.close()
        raise


def verify_consumer(runtime, root_observation):
    """Must run after projected exec; old mapped host ELF inodes refuse."""
    return _verify_consumer(runtime,root_observation,(POLICY,APPLICATION_POLICY))


def _consumer_scope(policy):
    return {POLICY:'HOSTED_DIAGNOSTIC_RUNTIME',APPLICATION_POLICY:'HOSTED_APPLICATION_IMPORT_COMPONENT',
            POSTGRES_PHASE_POLICY:'HOSTED_POSTGRES_SUPERVISOR_COMPONENT'}[policy]


def _mapped_file_kind(policy,path,data,digest,permissions,mode):
    ns._require(hashlib.sha256(data).hexdigest()==digest)
    if data.startswith(b'\x7fELF'):return 'ELF'
    # PG adds exact libc locale files absent from the diagnostic capture.
    # glibc may map them during supervisor startup; they are data, not ELF.
    from hosted_synthetic_postgres_runtime import _LOCALE
    ns._require(policy==POSTGRES_PHASE_POLICY
        and path in {'/usr/lib/locale/C.utf8/'+name for name in _LOCALE}
        and permissions==b'r--p' and mode==0o444)
    return 'LOCALE'


def _verify_consumer(runtime,root_observation,policies):
    ns._require(runtime.policy in policies)
    _entries(runtime)
    ns._require(type(root_observation) is dict and set(root_observation)=={
                    'policy','generation','bundle_sha256','manifest_sha256','root_identity','root_mount_id',
                    'old_root_detached','regular_bytes','consumer_exec_verified','execution_authority'}
                and root_observation['policy']==runtime.policy
                and root_observation['old_root_detached'] is True
                and root_observation['consumer_exec_verified'] is False
                and root_observation['execution_authority'] is False
                and type(root_observation['regular_bytes']) is int
                and root_observation['regular_bytes']==runtime.bundle.regular_bytes
                and type(root_observation['root_identity']) is list and len(root_observation['root_identity'])==2
                and all(type(value) is int and value>0 for value in root_observation['root_identity'])
                and type(root_observation['root_mount_id']) is int and root_observation['root_mount_id']>0
                and root_observation['generation']==runtime.generation
                and root_observation['bundle_sha256']==runtime.bundle.sha256
                and root_observation['manifest_sha256']==runtime.bundle.manifest_sha256
                and list(ns._identity(os.stat('/')))==root_observation['root_identity']
                and ns._identity(os.stat('/'))==ns._identity(os.stat('.'))
                and str(Path('/proc/self/exe').resolve())==runtime.executable
                and ns._identity(os.stat('/proc/self/exe'))==ns._identity(os.stat(runtime.executable)))
    root=ns._acquire(lambda:os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW))
    try:
        flags=os.fstatvfs(root.number).f_flag
        ns._require(ns._mount_id(root)==root_observation['root_mount_id']
                    and flags & os.ST_RDONLY and flags & os.ST_NOSUID and flags & os.ST_NODEV
                    and not flags & os.ST_NOEXEC)
    finally:root.close()
    allowed=dict(runtime.native_files);mapped=set();locale_mapped=set()
    for line in ns._read('/proc/self/maps').splitlines():
        fields=line.split(maxsplit=5)
        ns._require(len(fields) in (5,6))
        if len(fields)==5:continue
        path=fields[5].decode('ascii')
        if path.startswith('['):
            ns._require(path in ('[heap]','[stack]','[vvar]','[vvar_vclock]','[vdso]','[vsyscall]'));continue
        ns._require(path in allowed)
        info=os.stat(path,follow_symlinks=False)
        ns._require(stat.S_ISREG(info.st_mode) and info.st_dev==root_observation['root_identity'][0]
                    and fields[4]==str(info.st_ino).encode()
                    and fields[3]==f'{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}'.encode())
        if path in locale_mapped:
            ns._require(fields[1]==b'r--p' and stat.S_IMODE(info.st_mode)==0o444)
        elif path not in mapped:
            data=ns._read(path,MAX_BYTES)
            kind=_mapped_file_kind(runtime.policy,path,data,allowed[path],fields[1],stat.S_IMODE(info.st_mode))
            (mapped if kind=='ELF' else locale_mapped).add(path)
    ns._require(runtime.executable in mapped)
    for path,digest in runtime.source_files.items():
        ns._require(hashlib.sha256(ns._read(path,2*1024**2)).hexdigest()==digest)
    return {'policy':runtime.policy,'generation':runtime.generation,'bundle_sha256':runtime.bundle.sha256,
            'manifest_sha256':runtime.bundle.manifest_sha256,'source_commit':runtime.source_commit,'source_tree':runtime.source_tree,
            'root_identity':root_observation['root_identity'],'root_mount_id':root_observation['root_mount_id'],
            'regular_bytes':runtime.bundle.regular_bytes,'old_root_detached':True,
            'consumer_exec_verified':True,'mapped_elf_files':len(mapped),'execution_authority':False,
            'scope':_consumer_scope(runtime.policy),'application_browser_closure':'NOT_PROVEN',
            **({'native_postgres_execution':'NOT_PROVEN','mapped_postgres_locale_files':len(locale_mapped)}
                if runtime.policy==POSTGRES_PHASE_POLICY else {})}


def verify_report_binding(runtime, proof):
    """S retains its exact sealed bundle while comparing G's consumer proof."""
    return _verify_report_binding(runtime,proof,(POLICY,APPLICATION_POLICY))


def _verify_report_binding(runtime,proof,policies):
    runtime.verify()
    ns._require(runtime.policy in policies)
    ns._require(type(proof) is dict and set(proof)=={
        'policy','generation','bundle_sha256','manifest_sha256','source_commit','source_tree',
        'root_identity','root_mount_id','regular_bytes','old_root_detached','consumer_exec_verified',
        'mapped_elf_files','execution_authority','scope','application_browser_closure'}
        | ({'native_postgres_execution','mapped_postgres_locale_files'} if runtime.policy==POSTGRES_PHASE_POLICY else set()))
    expected={'policy':runtime.policy,'generation':runtime.generation,'bundle_sha256':runtime.bundle.sha256,
        'manifest_sha256':runtime.bundle.manifest_sha256,'source_commit':runtime.source_commit,'source_tree':runtime.source_tree,
        'regular_bytes':runtime.bundle.regular_bytes,'scope':_consumer_scope(runtime.policy),'application_browser_closure':'NOT_PROVEN',
        **({'native_postgres_execution':'NOT_PROVEN'} if runtime.policy==POSTGRES_PHASE_POLICY else {})}
    ns._require(all(proof[key]==value and type(proof[key]) is type(value) for key,value in expected.items())
                and proof['execution_authority'] is False and proof['old_root_detached'] is True
                and proof['consumer_exec_verified'] is True
                and type(proof['mapped_elf_files']) is int and 1<=proof['mapped_elf_files']<=MAX_ENTRIES
                and type(proof['root_identity']) is list and len(proof['root_identity'])==2
                and all(type(value) is int and value>0 for value in proof['root_identity'])
                and type(proof['root_mount_id']) is int and proof['root_mount_id']>0)
    if runtime.policy==POSTGRES_PHASE_POLICY:
        from hosted_synthetic_postgres_runtime import _LOCALE
        ns._require(type(proof['mapped_postgres_locale_files']) is int
            and 0<=proof['mapped_postgres_locale_files']<=len(_LOCALE))
