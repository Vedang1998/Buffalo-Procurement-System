"""Fixed native-input successor contract; installation and activation stay dormant.

Publisher-verified inventory is partial. No supplied manifest, Boolean, version
string or successful extraction grants complete-closure or execution authority.
Only native tools appear in proposed privileged commands; this module never runs
as root, runs package installers, changes host permissions or activates a unit.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import errno
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import time

import hosted_synthetic_namespace as ns

POLICY='BUFFALO_NATIVE_INPUT_VIEW_V1'
LOADER_POLICY='BUFFALO_FIXED_PYTHON_3135_RUNPATH_V1'
PYTHON='/opt/hostedtoolcache/Python/3.13.5/x64/bin/python3.13'
RUNPATH='/opt/hostedtoolcache/Python/3.13.5/x64/lib'
FILES=('hosted_native_inputs.json','hosted_native_runpath.json')
DIGESTS=('840a10c4228e055680567ec5e902bd9a00af7588bb31bf23bf493ffec22e8609',
         'ac4fb80664ff012a4625cee81f8627e27dba045f35a5fabfb84cfbfb069d31c1')
ARTIFACTS={
 'c12a003f80a8cb0df9fa15aee369ab5c9512976edff9405dded164e2c6922e1b':129234019,
 'd60ae76fb862cd4cca7bfdcdcdcc271c5e1d75f6b7915305ce2d962735a4b666':15610426,
 '4796f72e1a7270ad8c17b1ecb025cd453f692b42c10aaf89f6b476da61761eac':1301302,
 '3f96dd36bcd7841172c1ead4965da20a193bb9ccf9a93a3f5a3ac7f56369dbb1':151262,
}
_TOKEN=object()
_ACTIVE_VIEW=ContextVar('buffalo_native_input_view',default=None)
_ACTIVE_FILES=ContextVar('buffalo_native_input_files',default=None)


def require(value,code='native_input_contract_refused'):
    if not value:raise ns.NamespaceProofError(code)


def _canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode('ascii')


def _digest(value):return hashlib.sha256(_canonical(value)).hexdigest()


def _pinned(index):
    data=Path(__file__).with_name(FILES[index]).read_bytes()
    require(len(data)<=2*1024**2 and hashlib.sha256(data).hexdigest()==DIGESTS[index],
            'native_input_manifest_identity_refused')
    return json.loads(data)


def inventory():
    value=_pinned(0);rows=value['rows']
    require(type(rows) is list and len(rows)==value['known_file_count']<=3000)
    paths=set();total=0
    for row in rows:
        path=row['path'];require(type(path) is str and path.startswith('/')
            and '..' not in Path(path).parts and str(Path(path))==path
            and path not in paths and row['artifact_sha256'] in ARTIFACTS
            and type(row['bytes']) is int and 0<=row['bytes']<=83886080
            and re.fullmatch('[0-9a-f]{64}',row['sha256']) and row['mode'] in ('0444','0555'))
        paths.add(path);total+=row['bytes']
    require(total==value['known_regular_bytes'] and total<=134217728)
    return value


def require_complete_inventory():
    value=inventory()
    missing=[key for key in value if key.startswith(('required_unresolved_','unresolved_native_')) and value[key]]
    require(not missing and value['complete_runtime_manifest'] is True
            and value['exact_transitive_package_versions']=='AUTHENTICATED_AND_TARGET_MATCHED'
            and value['status']=='COMPLETE_VERIFIED','native_input_inventory_incomplete')
    # A future complete pin must also enumerate the recursively resolved graph.
    rows={row['path']:row for row in value['rows']}
    require(type(value.get('resolved_dependencies')) is dict,'native_dependency_graph_missing')
    for path,row in rows.items():
        if not row.get('elf'):continue
        needed=row['elf']['needed'];resolved=value['resolved_dependencies'].get(path)
        require(type(resolved) is dict and set(resolved)==set(needed))
        for name,target in resolved.items():
            require(target in rows and rows[target].get('elf',{}).get('soname')==name,
                    'native_dependency_resolution_refused')
        interp=row['elf'].get('interpreter')
        require(interp is None or interp in rows,'native_loader_missing')
    require(value.get('provenance_requirements_satisfied') is True,'native_provenance_incomplete')
    return value


@dataclass(frozen=True)
class ElfPermission:
    path:str
    sha256:str
    runpath:str
    _token:object


def elf_permission(path,data):
    """Parsing permission only, not materialization/execution/provenance closure."""
    require(type(data) is bytes)
    digest=hashlib.sha256(data).hexdigest()
    rows=[row for row in _pinned(1)['objects'] if row['path']==path]
    require(len(rows)==1 and rows[0]['sha256']==digest and rows[0]['bytes']==len(data)
            and rows[0]['runpath']==RUNPATH and rows[0]['rpath'] is None,
            'native_elf_object_not_selected')
    return ElfPermission(path,digest,RUNPATH,_TOKEN)


def require_elf_permission(permission,data):
    require(type(permission) is ElfPermission and permission._token is _TOKEN
            and permission==elf_permission(permission.path,data),'native_runpath_permission_refused')
    return RUNPATH


def parse_selected_elf(path,data):
    import hosted_synthetic_runtime as runtime
    permission=elf_permission(path,data)
    return runtime.parse_elf(data,native_permission=permission)


def _metadata(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid,info.st_nlink,
            info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def verify_artifact_fd(fd,digest):
    """Authenticated expected digest precedes any output; never execute payload."""
    require(type(fd) is int and fd>2 and digest in ARTIFACTS)
    before=os.fstat(fd)
    require(stat.S_ISREG(before.st_mode) and before.st_size==ARTIFACTS[digest]
            and not before.st_mode&0o6000,'native_artifact_shape_refused')
    h=hashlib.sha256();offset=0
    while offset<before.st_size:
        raw=os.pread(fd,min(1024**2,before.st_size-offset),offset)
        require(bool(raw),'native_artifact_truncated');h.update(raw);offset+=len(raw)
    require(h.hexdigest()==digest and _metadata(before)==_metadata(os.fstat(fd)),
            'native_artifact_digest_refused')
    return _metadata(before)


def _member(name):
    require(type(name) is str and not name.startswith('/') and '\\' not in name
            and '\0' not in name and '..' not in PurePosixPath(name).parts,'native_archive_path_refused')
    return name.removeprefix('./')


def _new_file(root_fd,relative,data,mode,owned):
    """Openat/O_NOFOLLOW below a newly owned root; no existing-object repair."""
    parts=PurePosixPath(relative).parts
    require(parts and all(re.fullmatch(r'[A-Za-z0-9_.+\-]+',p) and p not in ('.','..') for p in parts))
    parent=os.dup(root_fd)
    try:
        for index,part in enumerate(parts[:-1]):
            path='/'.join(parts[:index+1])
            try:
                os.mkdir(part,0o700,dir_fd=parent)
                info=os.stat(part,dir_fd=parent,follow_symlinks=False)
                owned.append(('D',path,(info.st_dev,info.st_ino)))
            except FileExistsError:require(any(k=='D' and p==path for k,p,_ in owned),'native_stage_foreign_directory')
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
            info=os.fstat(child)
            require(any(k=='D' and p==path and identity==(info.st_dev,info.st_ino) for k,p,identity in owned),'native_stage_directory_replaced')
            os.close(parent);parent=child
        fd=os.open(parts[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=parent)
        try:
            info=os.fstat(fd);owned.append(('F',relative,(info.st_dev,info.st_ino)))
            for offset in range(0,len(data),1024**2):
                chunk=memoryview(data)[offset:offset+1024**2]
                while chunk:
                    written=os.write(fd,chunk);require(written>0);chunk=chunk[written:]
            os.fchmod(fd,mode)
        finally:os.close(fd)
    finally:os.close(parent)



def _stage_operation(function):
    @wraps(function)
    def operation(self,*args,**kwargs):
        try:return function(self,*args,**kwargs)
        except BaseException as original:
            if not self.closed:
                try:self.close()
                except BaseException as cleanup:
                    raise ns.NamespaceCleanupError('native_materialization_cleanup_uncertain') from original
            raise
    return operation


class Stage:
    """Unprivileged test/materialization lease; not a trusted root installation."""
    def __init__(self,path):
        require(os.geteuid()>0 and os.getegid()>0,'native_python_root_refused')
        self.path=Path(path);self.owned=[];self._owner=self;self.closed=False
        self.path.mkdir(mode=0o700)
        self.fd=os.open(self.path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        info=os.fstat(self.fd);self.identity=(info.st_dev,info.st_ino)
        require(info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o700)

    def verify(self):
        require(self._owner is self and not self.closed)
        info=os.fstat(self.fd);named=self.path.lstat()
        require((info.st_dev,info.st_ino)==self.identity==(named.st_dev,named.st_ino)
                and stat.S_ISDIR(named.st_mode) and named.st_uid==os.geteuid()
                and stat.S_IMODE(named.st_mode)==0o700,'native_stage_identity_changed')

    @_stage_operation
    def materialize_tar(self,stream,rows):
        """Decoded tar data from an already authenticated artifact transport.

        Caller binds outer .deb/archive digest before this helper. Every selected
        file is also digest-bound; no tarfile extraction or metadata restoration.
        This helper alone cannot authorize installation of its output.
        """
        self.verify();expected={_member(row['artifact_member']):row for row in rows}
        require(len(expected)==len(rows)<=3000)
        for row in rows:
            require(type(row['bytes']) is int and 0<=row['bytes']<=83886080
                and row['mode'] in ('0444','0555') and re.fullmatch('[0-9a-f]{64}',row['sha256'])
                and type(row['path']) is str and row['path'].startswith('/')
                and '..' not in Path(row['path']).parts,'native_stage_row_refused')
        found=set();seen=set();total=0
        with tarfile.open(fileobj=stream,mode='r|') as archive:
            for index,entry in enumerate(archive):
                require(index<50000,'native_archive_entry_bound')
                name=_member(entry.name)
                require(name not in seen,'native_archive_duplicate');seen.add(name)
                if name not in expected:continue
                row=expected[name]
                require(entry.isfile() and not entry.issym() and not entry.islnk()
                        and not entry.mode&0o7000 and not entry.pax_headers
                        and entry.size==row['bytes']<=83886080,'native_archive_member_refused')
                total+=entry.size;require(total<=134217728,'native_stage_capacity_refused')
                content=archive.extractfile(entry).read(entry.size+1)
                require(len(content)==entry.size and hashlib.sha256(content).hexdigest()==row['sha256'],
                        'native_archive_content_refused')
                _new_file(self.fd,row['path'].lstrip('/'),content,int(row['mode'],8),self.owned)
                found.add(name)
        require(found==set(expected),'native_archive_member_missing');self.verify()
        return {'members':len(found),'regular_bytes':total,'execution_authority':False}

    @_stage_operation
    def materialize_python_artifact(self,descriptor):
        """The fixed authenticated archive only, unprivileged and never executed."""
        digest='c12a003f80a8cb0df9fa15aee369ab5c9512976edff9405dded164e2c6922e1b'
        before=verify_artifact_fd(descriptor,digest)
        rows=[row for row in inventory()['rows'] if row['artifact_sha256']==digest]
        require(len(rows)==646,'native_python_population_refused')
        # Independent offset, held inode; recheck complete outer digest after
        # reading. No path reopening, publisher setup.sh or file metadata import.
        import io
        class HeldArtifact(io.RawIOBase):
            def __init__(self):self.position=0
            def readable(self):return True
            def readinto(self,buffer):
                raw=os.pread(descriptor,len(buffer),self.position)
                buffer[:len(raw)]=raw;self.position+=len(raw);return len(raw)
        with io.BufferedReader(HeldArtifact(),buffer_size=65536) as stream:
            with gzip.GzipFile(fileobj=stream,mode='rb') as decoded:
                result=self.materialize_tar(decoded,rows)
        require(verify_artifact_fd(descriptor,digest)==before,'native_artifact_changed_during_materialization')
        return dict(result,artifact_sha256=digest,publisher_verification='PINNED_PUBLISHER_DIGEST_NOT_BUILD_ATTESTATION')

    @contextmanager
    def verified_stream(self,row):
        """Native importer stdin from one exact held, hash-verified stage file.

        Root must never reopen this operator-owned pathname. Its newly written
        destination is separately hashed against the publisher-bound manifest.
        """
        self.verify();require(row in inventory()['rows'],'native_stream_unselected')
        relative=row['path'].lstrip('/');matches=[x for x in self.owned if x[0]=='F' and x[1]==relative]
        require(len(matches)==1,'native_stream_missing')
        path=self.path/relative
        descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            before=os.fstat(descriptor)
            require((before.st_dev,before.st_ino)==matches[0][2] and before.st_uid==os.geteuid()
                and stat.S_ISREG(before.st_mode) and stat.S_IMODE(before.st_mode)==int(row['mode'],8)
                and before.st_size==row['bytes'],'native_stream_identity_refused')
            complete=[False]
            def checked_chunks():
                complete[0]=False
                h=hashlib.sha256();offset=0
                while offset<before.st_size:
                    raw=os.pread(descriptor,min(65536,before.st_size-offset),offset)
                    require(bool(raw));h.update(raw);offset+=len(raw);yield raw
                require(h.hexdigest()==row['sha256'] and _metadata(os.fstat(descriptor))==_metadata(before)
                    and _metadata(path.lstat())==_metadata(before),'native_stream_content_changed')
                complete[0]=True
            # Verify before the proposed native write and after its last byte.
            for _ in checked_chunks():pass
            complete[0]=False
            yield checked_chunks()
            require(complete[0],'native_stream_not_fully_consumed')
            require(_metadata(os.fstat(descriptor))==_metadata(before),'native_stream_content_changed')
        finally:os.close(descriptor)

    def close(self):
        require(self._owner is self and not self.closed)
        failure=None
        try:
            self.verify()
            # Refuse unknown descendants before removing anything. Own exact
            # files only; directories are retained writable until final native copy.
            actual=set()
            for parent,dirs,files in os.walk(self.path,followlinks=False):
                for name in dirs+files:
                    p=Path(parent)/name;require(not p.is_symlink(),'native_stage_foreign_link')
                    actual.add(str(p.relative_to(self.path)))
            require(actual=={p for _,p,_ in self.owned},'native_stage_foreign_resource')
            for kind,name,identity in self.owned:
                info=(self.path/name).lstat()
                require((info.st_dev,info.st_ino)==identity and info.st_uid==os.geteuid()
                    and (stat.S_ISREG(info.st_mode) if kind=='F' else stat.S_ISDIR(info.st_mode)),
                    'native_stage_resource_replaced')
            for kind,name,identity in reversed(self.owned):
                p=self.path/name;info=p.lstat()
                require((info.st_dev,info.st_ino)==identity and info.st_uid==os.geteuid(),'native_stage_owner_changed')
                if kind=='F':
                    require((info.st_dev,info.st_ino)==identity and stat.S_ISREG(info.st_mode),'native_stage_file_replaced')
                    p.unlink()
                else:require(stat.S_ISDIR(info.st_mode));p.rmdir()
            self.path.rmdir()
        except BaseException as error:failure=error
        finally:
            try:
                info=os.fstat(self.fd)
                require((info.st_dev,info.st_ino)==self.identity,'native_stage_descriptor_reused')
                os.close(self.fd)
            except BaseException as error:
                if failure is None:failure=error
            self.closed=True
        if failure is not None:raise ns.NamespaceCleanupError('native_stage_cleanup_uncertain') from failure


def native_install_plan(nonce,stage):
    """Fixed native-only proposed effects. No invocation, sudo or shell here.

    Root never reads an operator-controlled source pathname. The unprivileged
    controller must verify/read each Stage file and stream it to the fixed native
    dd stdin. New output stays under0700 root until publisher digest checks pass.
    A plan is not installation permission and cannot be activated from this API.
    """
    value=require_complete_inventory()
    require(type(nonce) is str and re.fullmatch('[0-9a-f]{32}',nonce))
    require(type(stage) is Stage);stage.verify()
    destination='/run/buffalo-native-'+nonce
    require(not os.path.lexists(destination),'native_destination_collision')
    directories={destination+'/view'}
    for row in value['rows']:
        path=Path(destination+'/view'+row['path'])
        directories.update(str(p) for p in path.parents if str(p).startswith(destination+'/'))
    commands=[{'argv':('/usr/bin/mkdir','--mode=0700','--',destination),'stdin':'DEVNULL'}]
    for path in sorted(directories,key=lambda p:(p.count('/'),p)):
        commands.append({'argv':('/usr/bin/mkdir','--mode=0700','--',path),'stdin':'DEVNULL'})
    for row in value['rows']:
        target=destination+'/view'+row['path']
        commands.append({'argv':('/usr/bin/dd','bs=1048576','of='+target,'oflag=excl,nofollow','status=none'),
            'stdin':'VERIFIED_UNPRIVILEGED_STAGE_STREAM','source_path':row['path'],
            'maximum_input_bytes':row['bytes'],'sha256':row['sha256']})
        commands.append({'argv':('/usr/bin/sha256sum','--',target),'stdin':'DEVNULL','expected_sha256':row['sha256']})
        commands.append({'argv':('/usr/bin/chmod',row['mode'],'--',target),'stdin':'DEVNULL'})
    for path in sorted(directories,key=lambda p:(-p.count('/'),p)):
        commands.append({'argv':('/usr/bin/chmod','0555','--',path),'stdin':'DEVNULL'})
    commands.append({'argv':('/usr/bin/chmod','0555','--',destination),'stdin':'DEVNULL'})
    return {'policy':POLICY,'manifest_sha256':DIGESTS[0],'destination':destination,
        'commands':tuple(commands),'expected_files':tuple((r['path'],r['sha256']) for r in value['rows']),
        'installation_authority':False,'execution_authority':False,
        'activation':'DISABLED_PENDING_SINGLE_INSTALLATION_DECISION_AND_REVIEW',
        'cleanup':'EXACT_RECORDED_OWNERSHIP_AND_NO_UNIT_PROCESS_MOUNT_HANDLE_REQUIRED; no recursive rm'}


@dataclass(frozen=True)
class ViewRequest:
    nonce:str
    commit:str
    tree:str
    manifest:str
    root:str
    root_identity:tuple
    interpreter_sha256:str
    deadline:float
    _token:object


def view_request(binding,root, *, deadline):
    value=require_complete_inventory()
    require(type(deadline) in (int,float) and time.monotonic()<deadline)
    root=Path(root);require(re.fullmatch('/run/buffalo-native-[0-9a-f]{32}',str(root)))
    info=root.lstat();require(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022)
    rows={r['path']:r for r in value['rows']};require(PYTHON in rows)
    return ViewRequest(os.urandom(32).hex(),binding.commit,binding.tree,DIGESTS[0],str(root),
        (info.st_dev,info.st_ino),rows[PYTHON]['sha256'],deadline,_TOKEN)


def _verify_request(request,binding):
    require(type(request) is ViewRequest and request._token is _TOKEN
        and request.manifest==DIGESTS[0] and (request.commit,request.tree)==(binding.commit,binding.tree)
        and type(request.nonce) is str and re.fullmatch('[0-9a-f]{64}',request.nonce)
        and re.fullmatch('/run/buffalo-native-[0-9a-f]{32}',request.root)
        and type(request.deadline) in (int,float) and time.monotonic()<request.deadline,'native_view_request_refused')
    info=os.lstat(request.root)
    require((info.st_dev,info.st_ino)==request.root_identity and info.st_uid==0
        and stat.S_ISDIR(info.st_mode) and not info.st_mode&0o022,'native_view_root_changed')


def view_properties(request,binding):
    _verify_request(request,binding);require_complete_inventory()
    return ('PrivateMounts=yes','PrivateNetwork=yes','NoNewPrivileges=yes','MemoryMax=536870912','TasksMax=64',
        'BindReadOnlyPaths='+request.root+'/view/opt:/opt',
        'BindReadOnlyPaths='+request.root+'/view/usr/share:/usr/share')


@contextmanager
def protected_view(request,binding):
    """Same manifest/view requirement for the measurement child and service.

    Only callable after native view setup. Original-host ownership/IPC scanning
    remains outside this context. This never sets up mounts or starts a child.
    """
    _verify_request(request,binding);value=require_complete_inventory()
    require(os.geteuid()>0 and os.getegid()>0 and not os.getgroups(),'native_view_credentials_refused')
    from hosted_synthetic_bootstrap import launcher,_ENVIRONMENT
    require(dict(os.environ)==_ENVIRONMENT,'native_view_environment_override_refused')
    launcher.parse_postdrop_status(Path('/proc/self/status').read_bytes(),uid=os.geteuid(),gid=os.getegid())
    import hosted_synthetic_runtime as runtime
    require(str(Path('/proc/self/exe').resolve(strict=True))==PYTHON,'native_view_interpreter_refused')
    for base in ('/opt','/usr/share'):
        require(os.statvfs(base).f_flag&os.ST_RDONLY,'native_view_not_readonly')
    for row in value['rows']:
        data,mode=runtime.read_native(row['path'])
        require(len(data)==row['bytes'] and hashlib.sha256(data).hexdigest()==row['sha256']
            and mode==int(row['mode'],8),'native_view_input_changed')
    from hosted_synthetic_source import bind_hosted_source,REPOSITORY,REF
    require(bind_hosted_source(Path(__file__).resolve().parents[2],repository=REPOSITORY,event='push',ref=REF,sha=binding.commit)==binding,
            'native_view_source_changed')
    token=_ACTIVE_VIEW.set(request);files_token=_ACTIVE_FILES.set({r['path']:r for r in value['rows']})
    try:yield request
    finally:
        _ACTIVE_FILES.reset(files_token);_ACTIVE_VIEW.reset(token)


def active_view():return _ACTIVE_VIEW.get()


def require_selected_file(path,data,mode):
    rows=_ACTIVE_FILES.get();request=active_view()
    require(request is not None and rows is not None and time.monotonic()<request.deadline)
    row=rows.get(path)
    require(row is not None and len(data)==row['bytes'] and mode==int(row['mode'],8)
            and hashlib.sha256(data).hexdigest()==row['sha256'],'native_unlisted_input_or_fallback_refused')



def bind_measurement_result(request,binding,receipt):
    """No exit-code/JSON/Boolean substitution for actual code-minted readiness."""
    _verify_request(request,binding)
    from hosted_synthetic_postgres_runtime import PhaseReadiness
    require(type(receipt) is PhaseReadiness and active_view() is request,'native_measurement_view_refused')
    report=receipt.verify(Path(__file__).resolve().parents[2],binding)
    require(receipt.native.get(PYTHON)==request.interpreter_sha256,'native_measurement_interpreter_changed')
    return {'policy':POLICY,'nonce':request.nonce,'commit':request.commit,'tree':request.tree,
        'manifest_sha256':request.manifest,'view_identity':list(request.root_identity),
        'interpreter_sha256':request.interpreter_sha256,'memory_readiness':report,
        'execution_authority':False,'service_memory_proof':'NOT_PROVEN'}


def activate_installation(*args,**kwargs):
    raise ns.NamespaceProofError('native_installation_not_authorized')
