"""Dormant fixed native importer and ownership-bound cleanup.

No CLI or installation authority. The real transport constructor refuses until a
separate reviewed activation change. Tests exercise this state machine with owned
synthetic transports. No repository Python is ever a privileged command.
"""
import hashlib
from functools import wraps
from contextlib import contextmanager
import math
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import time

import hosted_synthetic_native_inputs as native
import hosted_synthetic_bootstrap as boot

_TOKEN=object()
CLEANUP_SECONDS=5


class WorkExpired(native.ns.NamespaceProofError):
    """No ownership ambiguity: no new work was performed by this refusal."""


def _work_deadline(deadline):
    if not (type(deadline) in (int,float) and math.isfinite(deadline) and time.monotonic()<deadline):
        raise WorkExpired('native_install_work_expired')

def _sticky(method):
    @wraps(method)
    def guarded(self,*args,**kwargs):
        try:return method(self,*args,**kwargs)
        except BaseException as error:
            if getattr(self,'_owner',None) is self and (not isinstance(error,WorkExpired) or self.pending):self.uncertain=True
            raise
    return guarded


_PROGRAMS=frozenset(('mkdir','dd','sha256sum','chmod','stat','find','unlink','rmdir','rm'))


def _path(root,relative=''):
    native.require(re.fullmatch('/run/buffalo-native-[0-9a-f]{32}',root))
    native.require(type(relative) is str and (not relative or (not relative.startswith('/')
        and '..' not in Path(relative).parts and re.fullmatch(r'[A-Za-z0-9_./+\-]+',relative))))
    return root+('/'+relative if relative else '')


def _command(program,*args,remaining=None):
    native.require(program in _PROGRAMS and len(args)<=70
        and all(type(a) is str and '\0' not in a for a in args)
        and sum(len(a.encode('utf-8'))+1 for a in args)<=32768,'native_command_arguments_refused')
    # Native timeout plus kill grace must fit the same outer cleanup deadline.
    if remaining is None:kill='2s';work='15s'
    else:
        native.require(type(remaining) in (int,float) and math.isfinite(remaining) and remaining>.05,
            'native_install_cleanup_timeout')
        kill=f'{min(.2,remaining/4):.6f}s';work=f'{remaining/2:.6f}s'
    return ('/usr/bin/sudo','-n','--','/usr/bin/env','-i','PATH=/usr/bin:/bin','LC_ALL=C.UTF-8',
        '/usr/bin/timeout','--signal=TERM','--kill-after='+kill,work,'/usr/bin/'+program,*args)


def _source_binding(binding,deadline):
    from hosted_synthetic_source import HostedSourceBinding,bind_hosted_source,REPOSITORY,REF
    native.require(type(binding) is HostedSourceBinding,'native_install_source_required')
    native.require(bind_hosted_source(Path(__file__).resolve().parents[2],repository=REPOSITORY,event='push',ref=REF,
        sha=binding.commit,operation_deadline=deadline)==binding,'native_install_source_changed')


def inspect_import_tools(deadline):
    """Read-only platform-tool observation, not publisher artifact attestation."""
    import hosted_synthetic_runtime as runtime
    native.require(time.monotonic()<deadline)
    wrappers=boot.inspect_native_prerequisites();programs={}
    for name in sorted(_PROGRAMS):
        native.require(time.monotonic()<deadline,'native_install_tool_deadline')
        path='/usr/bin/'+name;data,mode=runtime.read_native(path)
        native.require(mode==0o555,'native_install_tool_not_executable')
        programs[path]=hashlib.sha256(data).hexdigest()
    return {'wrappers':wrappers,'programs':programs,'assurance':'PLATFORM_OWNERSHIP_NOT_PUBLISHER_ATTESTATION'}


class _NativeOps:
    def __init__(self,deadline):
        from hosted_synthetic_native_route import require_installation_scope
        require_installation_scope();_work_deadline(deadline)
        self.deadline=deadline;self.cleanup_deadline=None

    def begin_cleanup(self,deadline):
        native.require(getattr(self,'cleanup_deadline',None) is None and time.monotonic()<deadline)
        self.cleanup_deadline=deadline

    def run(self,program,*args):
        cleanup=getattr(self,'cleanup_deadline',None)
        if cleanup is not None:
            native.require(program in ('stat','find','unlink','rmdir','rm'),'native_cleanup_work_refused')
        code,raw=boot._collect_native_client(_command(program,*args,remaining=None if cleanup is None else cleanup-time.monotonic()),cwd='/',operation_deadline=self.deadline if cleanup is None else cleanup,**({} if cleanup is None else {'cleanup_deadline':cleanup}))
        native.require(code==0,'native_install_command_refused');return raw

    def stream(self,path,row,chunks):
        """Fixed dd stdin, bounded buffering; no privileged Stage path reopen."""
        argv=_command('dd','bs=65536','iflag=count_bytes,fullblock','count='+str(row['bytes']),
            'of='+path,'oflag=excl,nofollow','status=none')
        native.require(getattr(self,'cleanup_deadline',None) is None,'native_cleanup_work_refused')
        process=None;pending=memoryview(b'');sent=0;iterator=iter(chunks);eof=False;errors=bytearray()
        failure=None
        try:
            _work_deadline(self.deadline)
            process=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                env=boot._ENVIRONMENT,close_fds=True,bufsize=0)
            with selectors.DefaultSelector() as selected:
                for pipe in (process.stdin,process.stdout,process.stderr):os.set_blocking(pipe.fileno(),False)
                selected.register(process.stdin,selectors.EVENT_WRITE)
                selected.register(process.stdout,selectors.EVENT_READ);selected.register(process.stderr,selectors.EVENT_READ)
                while selected.get_map():
                    remaining=self.deadline-time.monotonic();native.require(remaining>0,'native_import_timeout')
                    ready=selected.select(remaining);native.require(ready,'native_import_timeout')
                    for key,_ in ready:
                        pipe=key.fileobj
                        if pipe is process.stdin:
                            if not pending and not eof:
                                try:raw=next(iterator)
                                except StopIteration:eof=True
                                else:
                                    native.require(type(raw) is bytes and 0<len(raw)<=65536,'native_import_chunk_bound')
                                    native.require(sent+len(raw)<=row['bytes'],'native_import_extra_bytes');pending=memoryview(raw)
                            if pending:
                                written=os.write(pipe.fileno(),pending);native.require(written>0)
                                pending=pending[written:];sent+=written
                            elif eof:selected.unregister(pipe);pipe.close()
                        else:
                            raw=os.read(pipe.fileno(),min(4096,4097-len(errors)))
                            if not raw:selected.unregister(pipe)
                            else:errors.extend(raw);native.require(len(errors)<=4096,'native_import_output_bound')
            remaining=self.deadline-time.monotonic();native.require(remaining>0)
            native.require(process.wait(timeout=remaining)==0 and sent==row['bytes'] and eof,'native_import_terminal_refused')
        except BaseException as error:failure=error
        finally:
            if process is not None:
                try:
                    if process.poll() is None:
                        process.kill();process.wait(timeout=5)
                        # Direct client only; pending mutation remains uncertain.
                except BaseException as cleanup:
                    if failure is None:failure=cleanup
                    else:failure.add_note('native_stream_client_reap_unverified')
                finally:
                    for pipe in (process.stdin,process.stdout,process.stderr):
                        try:
                            if pipe is not None and not pipe.closed:pipe.close()
                        except BaseException as cleanup:
                            if failure is None:failure=cleanup
                            else:failure.add_note('native_stream_pipe_close_unverified')
        if failure is not None:raise failure

    def observe(self,path):
        raw=self.run('stat','--printf=%d %i %f %u %g %h %s\n','--',path)
        native.require(re.fullmatch(rb'[0-9]+ [0-9]+ [0-9a-f]+ 0 0 [0-9]+ [0-9]+\n',raw),'native_install_stat_refused')
        a=raw.split();return (int(a[0]),int(a[1]),int(a[2],16),int(a[3]),int(a[4]),int(a[5]),int(a[6]))

    def observe_many(self,paths, *, follow=False):
        native.require(type(paths) is list and 0<len(paths)<=3000)
        result=[]
        for offset in range(0,len(paths),64):
            selected=paths[offset:offset+64]
            raw=self.run('stat',*(('-L',) if follow else ()), '--printf=%d %i %f %u %g %h %s\\n','--',*selected)
            lines=raw.splitlines();native.require(len(lines)==len(selected))
            for line in lines:
                native.require(re.fullmatch(rb'[0-9]+ [0-9]+ [0-9a-f]+ [0-9]+ [0-9]+ [0-9]+ [0-9]+',line))
                values=line.split();result.append(tuple(int(v,16 if i==2 else 10) for i,v in enumerate(values)))
        return result

    def read_maps(self,pid):
        native.require(type(pid) is int and pid>1)
        return self.run('dd','if=/proc/'+str(pid)+'/maps','bs=65537','count=1','status=none')

    def inventory(self,root):
        raw=self.run('find','-P',root,'-xdev','-printf',r'%P\0')
        native.require(raw.endswith(b'\0') and len(raw)<=65536,'native_install_inventory_bound')
        names=raw[:-1].decode('ascii').split('\0');native.require(len(names)==len(set(names))<=3001)
        return set(names)-{''}

    def absent(self,path):
        # Parent /run is searchable. A vanished top path is not consumer drain.
        if re.fullmatch('/run/buffalo-native-[0-9a-f]{32}',path):
            try:os.lstat(path)
            except FileNotFoundError:return True
            return False
        parent=str(Path(path).parent)
        raw=self.run('find','-P',parent,'-maxdepth','1','-printf',r'%f\0')
        native.require(raw.endswith(b'\0') and len(raw)<=65536,'native_install_absence_unverified')
        return Path(path).name.encode() not in raw[:-1].split(b'\0')


class InstallationLease:
    def __init__(self):raise native.ns.NamespaceProofError('native_installation_must_be_owned')

    @classmethod
    def _create(cls,nonce,*,deadline,binding):
        native.require(os.geteuid()>0 and time.monotonic()<deadline)
        _source_binding(binding,deadline)
        root=_path('/run/buffalo-native-'+nonce)
        native.require(not os.path.lexists(root),'native_destination_collision')
        native.require_native_capacity()
        # This remains separate from static capacity and memory readiness.
        native.require_complete_inventory()
        self=object.__new__(cls);self._owner=self;self._token=_TOKEN;self.root=root;self.deadline=deadline
        self.binding=binding;self.manifest=native.INSTALLATION_MANIFEST_SHA256;self.rows=native.execution_inventory()['rows']
        self.ops=_NativeOps(deadline);self.owned={};self.closed=False;self.uncertain=False
        self.pending=False;self.cleanup_deadline=None;self.installed=False;self.consumers=[];self.view_requests={};self.active_stage=None;self.total=0
        try:
            self._mkdir('');self._mkdir('view')
            self._ensure_directories(list(self.rows.values()))
            return self
        except BaseException as error:
            if not isinstance(error,WorkExpired) or self.pending:self.uncertain=True
            error.native_installation_lease=self  # Retain exact ownership; never delete ambiguous objects.
            raise

    def _state(self):
        native.require(self._owner is self and self._token is _TOKEN and not self.closed
            and not self.uncertain and not self.pending
            and self.manifest==native.INSTALLATION_MANIFEST_SHA256,'native_install_lease_refused')

    @_sticky
    def verify(self, *, all_objects=False):
        self._state()
        native.require(self.cleanup_deadline is None,'native_cleanup_work_refused')
        _work_deadline(self.deadline)
        self._objects(all_objects)

    def _objects(self,all_objects):
        if all_objects:
            names=list(self.owned);observed=self.ops.observe_many([_path(self.root,n) for n in names])
            native.require(observed==[self.owned[n] for n in names],'native_install_owned_identity_changed')
        else:native.require(self.ops.observe(self.root)==self.owned[''],'native_install_owned_identity_changed')

    def _mkdir(self,name):
        _work_deadline(self.deadline)
        if name in self.owned:return
        self.pending=True
        try:
            self.ops.run('mkdir','--mode=0700','--',_path(self.root,name))
            info=self.ops.observe(_path(self.root,name))
            native.require(stat.S_ISDIR(info[2]) and stat.S_IMODE(info[2])==0o700 and info[3:5]==(0,0))
            # Directory link counts change as owned children are added; refresh
            # only known same-inode directory metadata after each command.
            self.owned[name]=info;self._refresh_directories(name);self.pending=False
        except BaseException:self.uncertain=True;raise

    def _refresh_directories(self,changed):
        self._refresh_parent_batch([changed])

    def _check_named(self,names):
        ordered=sorted(set(names))
        native.require(ordered and all(n in self.owned for n in ordered))
        native.require(self.ops.observe_many([_path(self.root,n) for n in ordered])==
            [self.owned[n] for n in ordered],'native_install_owned_identity_changed')

    def _ensure_directories(self,rows):
        missing={str(p) for r in rows for p in Path('view'+r['path']).parents
            if str(p)!='.' and str(p) not in self.owned}
        for depth in sorted({n.count('/') for n in missing}):
            level=sorted(n for n in missing if n.count('/')==depth)
            for offset in range(0,len(level),64):
                names=level[offset:offset+64];_work_deadline(self.deadline)
                parents={str(Path(n).parent) for n in names}|{''}
                self._check_named(parents);self.pending=True
                # No -p: every parent is already owned, every destination new.
                self.ops.run('mkdir','--mode=0700','--',*[_path(self.root,n) for n in names])
                observed=self.ops.observe_many([_path(self.root,n) for n in names])
                native.require(len(observed)==len(names))
                for name,info in zip(names,observed):
                    native.require(name not in self.owned and stat.S_ISDIR(info[2])
                        and stat.S_IMODE(info[2])==0o700 and info[3:5]==(0,0),'native_directory_batch_refused')
                    self.owned[name]=info
                self._refresh_parent_batch(names);self.pending=False

    @_sticky
    def import_stage(self,stage,rows):
        self.verify();native.require(type(stage) is native.Stage and self.active_stage is None and not self.installed)
        native.require(rows and all(self.rows.get(r['path'])==r for r in rows),'native_install_unlisted_file')
        native.require(len({r['artifact_sha256'] for r in rows})==1,'native_install_artifact_group_refused')
        self.active_stage=stage
        try:
            self._ensure_directories(rows)
            for offset in range(0,len(rows),64):
                batch=rows[offset:offset+64];_work_deadline(self.deadline)
                names=['view'+r['path'] for r in batch];paths=[_path(self.root,n) for n in names]
                native.require(len(set(names))==len(names) and all(n not in self.owned for n in names))
                ancestors={''}|{str(p) for n in names for p in Path(n).parents if str(p)!='.'}
                self._check_named(ancestors);self.pending=True
                # Parent tree is root-owned0700, inaccessible to unprivileged
                # actors. Every dd remains exclusive/no-follow. No batch is
                # acknowledged until every output and inode has matched.
                for row,path in zip(batch,paths):
                    _work_deadline(self.deadline)
                    with stage.verified_stream(row) as chunks:self.ops.stream(path,row,chunks)
                before=self.ops.observe_many(paths)
                native.require(len(before)==len(batch))
                for row,info in zip(batch,before):
                    native.require(stat.S_ISREG(info[2]) and info[3:]==(0,0,1,row['bytes'])
                        and stat.S_IMODE(info[2])&0o7000==0,'native_install_file_identity_refused')
                raw=self.ops.run('sha256sum','--',*paths)
                expected=''.join(r['sha256']+'  '+path+'\n' for r,path in zip(batch,paths)).encode()
                native.require(raw==expected,'native_install_hash_refused')
                for mode in sorted({r['mode'] for r in batch}):
                    self.ops.run('chmod',mode,'--',*[path for r,path in zip(batch,paths) if r['mode']==mode])
                observed=self.ops.observe_many(paths)
                native.require(len(observed)==len(batch))
                for name,row,old,info in zip(names,batch,before,observed):
                    native.require(info[:2]==old[:2] and stat.S_ISREG(info[2])
                        and stat.S_IMODE(info[2])==int(row['mode'],8) and info[3:]==(0,0,1,row['bytes']),
                        'native_install_file_identity_refused')
                for name,row,info in zip(names,batch,observed):
                    self.owned[name]=info;self.total+=row['bytes']
                self._refresh_parent_batch(names);self.pending=False
            stage.close();self.active_stage=None
        except BaseException as error:
            if not isinstance(error,WorkExpired) or self.pending:self.uncertain=True
            try:
                if not stage.closed:stage.close()
            except BaseException:
                self.uncertain=True;error.add_note('native_stage_cleanup_unverified')
            else:self.active_stage=None
            raise

    @_sticky
    def finish(self):
        self.verify(all_objects=True);_source_binding(self.binding,self.deadline);native.require(self.active_stage is None)
        native.require({n[4:] for n,v in self.owned.items() if stat.S_ISREG(v[2])}==set(self.rows)
            and self.total==sum(r['bytes'] for r in self.rows.values()),'native_install_population_refused')
        directories=sorted(n for n in self.owned if n and stat.S_ISDIR(self.owned[n][2]))
        for offset in range(0,len(directories),64):
            names=directories[offset:offset+64];_work_deadline(self.deadline)
            paths=[_path(self.root,n) for n in names];self._check_named(names)
            self.pending=True;self.ops.run('chmod','0555','--',*paths)
            observed=self.ops.observe_many(paths);native.require(len(observed)==len(names))
            for name,new in zip(names,observed):
                old=self.owned[name]
                native.require(new[:2]==old[:2] and stat.S_ISDIR(new[2])
                    and stat.S_IMODE(new[2])==0o555 and new[3:]==old[3:],'native_directory_mode_batch_refused')
                self.owned[name]=new
            self.pending=False
        # Keep the backing top0700. Private mounts expose selected read-only
        # objects; unrelated unprivileged processes cannot traverse this lease.
        self.verify();native.require(stat.S_IMODE(self.owned[''][2])==0o700)
        native.require(self.ops.inventory(self.root)==set(self.owned)-{''},'native_install_unknown_descendant')
        self.installed=True

    @_sticky
    def close(self):
        if self._owner is self and self.closed and self._token is None:return
        self._state()
        # One cleanup window, independent of expired work, never renewable.
        if self.cleanup_deadline is None:
            self.cleanup_deadline=time.monotonic()+CLEANUP_SECONDS
            self.ops.begin_cleanup(self.cleanup_deadline)
        self._cleanup_time();self._objects(True)
        native.require(self.active_stage is None,'native_install_stage_still_live')
        from hosted_synthetic_native_measurement import require_consumer_drain,NativeConsumer
        native.require(all(type(c) is NativeConsumer for c in self.consumers)
            and set(self.view_requests)=={c.request.nonce for c in self.consumers},'native_view_without_consumer_refused')
        for consumer in self.consumers:require_consumer_drain(consumer,self)
        native.require(self.ops.inventory(self.root)==set(self.owned)-{''},'native_install_unknown_descendant')
        files=sorted(n for n,v in self.owned.items() if stat.S_ISREG(v[2]))
        # Exact nonrecursive batches; never globs, -f, -r or unknown paths.
        for offset in range(0,len(files),64):
            names=files[offset:offset+64];self._cleanup_time()
            paths=[_path(self.root,n) for n in names]
            native.require(self.ops.observe_many(paths)==[self.owned[n] for n in names],'native_install_owned_identity_changed')
            self.pending=True;self.ops.run('rm','--',*paths)
            remaining=self.ops.inventory(self.root)
            native.require(remaining==(set(self.owned)-{''}-set(names)),'native_install_removal_unverified')
            for name in names:del self.owned[name]
            self._refresh_parent_batch(names);self.pending=False
        directories=sorted((n for n in self.owned if n),key=lambda n:(-n.count('/'),n))
        # A depth group never contains its own ancestor.
        for depth in sorted({n.count('/') for n in directories},reverse=True):
            group=[n for n in directories if n.count('/')==depth]
            for offset in range(0,len(group),64):
                names=group[offset:offset+64];self._cleanup_time();paths=[_path(self.root,n) for n in names]
                native.require(self.ops.observe_many(paths)==[self.owned[n] for n in names],'native_install_owned_identity_changed')
                self.pending=True;self.ops.run('rmdir','--',*paths)
                native.require(self.ops.inventory(self.root)==set(self.owned)-{''}-set(names),'native_install_removal_unverified')
                for name in names:del self.owned[name]
                self._refresh_parent_batch(names);self.pending=False
        self._cleanup_time();native.require(self.ops.observe(self.root)==self.owned[''])
        self.pending=True;self.ops.run('rmdir','--',self.root)
        native.require(self.ops.absent(self.root),'native_install_removal_unverified')
        self.pending=False;self.closed=True;self._token=None

    def _cleanup_time(self):
        native.require(self.cleanup_deadline is not None and time.monotonic()<self.cleanup_deadline,'native_install_cleanup_timeout')

    def _refresh_parent_batch(self,names):
        # Only immediate parents change size/link count. Higher ancestors must
        # retain their exact snapshots; refreshing them would hide drift.
        parents={str(Path(name).parent) if str(Path(name).parent)!='.' else '' for name in names if name}&set(self.owned)
        ordered=sorted(parents)
        if not ordered:return
        current=self.ops.observe_many([_path(self.root,n) for n in ordered])
        for name,new in zip(ordered,current):
            native.require(new[:5]==self.owned[name][:5],'native_install_parent_replaced');self.owned[name]=new


def activate_installation(*args,**kwargs):
    from hosted_synthetic_native_route import _installation_decision_gate,installation_measurement_entry
    _installation_decision_gate()
    return installation_measurement_entry(*args,**kwargs)
