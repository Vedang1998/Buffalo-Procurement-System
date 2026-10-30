"""Dormant fixed native importer and ownership-bound cleanup.

No CLI or installation authority. The real transport constructor refuses until a
separate reviewed activation change. Tests exercise this state machine with owned
synthetic transports. No repository Python is ever a privileged command.
"""
import hashlib
from functools import wraps
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
def _sticky(method):
    @wraps(method)
    def guarded(self,*args,**kwargs):
        try:return method(self,*args,**kwargs)
        except BaseException:
            if getattr(self,'_owner',None) is self:self.uncertain=True
            raise
    return guarded


_PROGRAMS=frozenset(('mkdir','dd','sha256sum','chmod','stat','find','unlink','rmdir'))


def _path(root,relative=''):
    native.require(re.fullmatch('/run/buffalo-native-[0-9a-f]{32}',root))
    native.require(type(relative) is str and (not relative or (not relative.startswith('/')
        and '..' not in Path(relative).parts and re.fullmatch(r'[A-Za-z0-9_./+\-]+',relative))))
    return root+('/'+relative if relative else '')


def _command(program,*args):
    native.require(program in _PROGRAMS)
    return ('/usr/bin/sudo','-n','--','/usr/bin/env','-i','PATH=/usr/bin:/bin','LC_ALL=C.UTF-8',
        '/usr/bin/timeout','--signal=TERM','--kill-after=2s','15s','/usr/bin/'+program,*args)


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
        # Source-reviewed activation is a separate gate, not a Boolean/secret.
        raise native.ns.NamespaceProofError('native_installation_not_authorized')

    def run(self,program,*args):
        code,raw=boot._collect_native_client(_command(program,*args),cwd='/',operation_deadline=self.deadline)
        native.require(code==0,'native_install_command_refused');return raw

    def stream(self,path,row,chunks):
        """Fixed dd stdin, bounded buffering; no privileged Stage path reopen."""
        argv=_command('dd','bs=65536','iflag=count_bytes,fullblock','count='+str(row['bytes']),
            'of='+path,'oflag=excl,nofollow','status=none')
        process=None;pending=memoryview(b'');sent=0;iterator=iter(chunks);eof=False;errors=bytearray()
        try:
            native.require(time.monotonic()<self.deadline)
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
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill();process.wait(timeout=5)
                    # This reaps the direct client only. The lease remains
                    # uncertain; it never claims root descendants drained.
                for pipe in (process.stdin,process.stdout,process.stderr):
                    if pipe is not None and not pipe.closed:pipe.close()

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
        self.binding=binding;self.manifest=native.PROPOSED_INVENTORY_SHA256;self.rows=native.execution_inventory()['rows']
        self.ops=_NativeOps(deadline);self.owned={};self.closed=False;self.uncertain=False
        self.pending=False;self.installed=False;self.consumers=[];self.view_requests={};self.active_stage=None;self.total=0
        try:self._mkdir('');self._mkdir('view');return self
        except BaseException as error:
            self.uncertain=True
            error.native_installation_lease=self  # Retain exact ownership; never delete ambiguous objects.
            raise

    @_sticky
    def verify(self, *, all_objects=False):
        native.require(self._owner is self and self._token is _TOKEN and not self.closed
            and not self.uncertain and not self.pending and time.monotonic()<self.deadline
            and self.manifest==native.PROPOSED_INVENTORY_SHA256,'native_install_lease_refused')
        if all_objects:
            names=list(self.owned);observed=self.ops.observe_many([_path(self.root,n) for n in names])
            native.require(observed==[self.owned[n] for n in names],'native_install_owned_identity_changed')
        else:native.require(self.ops.observe(self.root)==self.owned[''],'native_install_owned_identity_changed')

    def _mkdir(self,name):
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
        names={str(p) if str(p)!='.' else '' for p in Path(changed).parents}
        for name in names:
            if name not in self.owned:continue
            old=self.owned[name]
            if not stat.S_ISDIR(old[2]):continue
            new=self.ops.observe(_path(self.root,name))
            native.require(new[:5]==old[:5],'native_install_parent_replaced');self.owned[name]=new

    @_sticky
    def import_stage(self,stage,rows):
        self.verify();native.require(type(stage) is native.Stage and self.active_stage is None and not self.installed)
        native.require(rows and all(self.rows.get(r['path'])==r for r in rows),'native_install_unlisted_file')
        native.require(len({r['artifact_sha256'] for r in rows})==1,'native_install_artifact_group_refused')
        self.active_stage=stage
        try:
            for row in rows:
                name='view'+row['path'];native.require(name not in self.owned)
                for parent in reversed(Path(name).parents):
                    if str(parent)!='.':self._mkdir(str(parent))
                self.verify();self.pending=True
                with stage.verified_stream(row) as chunks:self.ops.stream(_path(self.root,name),row,chunks)
                raw=self.ops.run('sha256sum','--',_path(self.root,name))
                native.require(raw==(row['sha256']+'  '+_path(self.root,name)+'\n').encode(),'native_install_hash_refused')
                self.ops.run('chmod',row['mode'],'--',_path(self.root,name))
                info=self.ops.observe(_path(self.root,name))
                native.require(stat.S_ISREG(info[2]) and stat.S_IMODE(info[2])==int(row['mode'],8)
                    and info[3:]==(0,0,1,row['bytes']),'native_install_file_identity_refused')
                self.owned[name]=info;self.total+=row['bytes'];self.pending=False
                self._refresh_directories(name)
            stage.close();self.active_stage=None
        except BaseException:
            self.uncertain=True
            if not stage.closed:
                try:stage.close()
                except BaseException as cleanup:raise native.ns.NamespaceCleanupError('native_stage_cleanup_unverified') from cleanup
            raise

    @_sticky
    def finish(self):
        self.verify(all_objects=True);_source_binding(self.binding,self.deadline);native.require(self.active_stage is None)
        native.require({n[4:] for n,v in self.owned.items() if stat.S_ISREG(v[2])}==set(self.rows)
            and self.total==sum(r['bytes'] for r in self.rows.values()),'native_install_population_refused')
        for name in sorted(self.owned,key=lambda n:-n.count('/')):
            if not name or not stat.S_ISDIR(self.owned[name][2]):continue
            self.pending=True;self.ops.run('chmod','0555','--',_path(self.root,name))
            new=self.ops.observe(_path(self.root,name));old=self.owned[name]
            native.require(new[:2]==old[:2] and stat.S_IMODE(new[2])==0o555 and new[3:5]==(0,0))
            self.owned[name]=new;self.pending=False
        # Keep the backing top0700. Private mounts expose selected read-only
        # objects; unrelated unprivileged processes cannot traverse this lease.
        self.verify();native.require(stat.S_IMODE(self.owned[''][2])==0o700)
        native.require(self.ops.inventory(self.root)==set(self.owned)-{''},'native_install_unknown_descendant')
        self.installed=True

    @_sticky
    def close(self):
        self.verify(all_objects=True);native.require(self.active_stage is None,'native_install_stage_still_live')
        # Consumers register before launch. Their own kernel/source-bound
        # observers must be terminal and close every handle, never a Boolean.
        from hosted_synthetic_native_measurement import require_consumer_drain
        from hosted_synthetic_native_measurement import NativeConsumer
        native.require(all(type(c) is NativeConsumer for c in self.consumers)
            and set(self.view_requests)=={c.request.nonce for c in self.consumers},
            'native_view_without_consumer_refused')
        for consumer in self.consumers:require_consumer_drain(consumer,self)
        native.require(self.ops.inventory(self.root)==set(self.owned)-{''},'native_install_unknown_descendant')
        for name in sorted(self.owned,key=lambda n:(-n.count('/'),n),reverse=False):
            if not name:continue
            info=self.owned[name];native.require(self.ops.observe(_path(self.root,name))==info)
            self.pending=True
            if stat.S_ISREG(info[2]):self.ops.run('unlink','--',_path(self.root,name))
            else:self.ops.run('rmdir','--',_path(self.root,name))
            native.require(self.ops.absent(_path(self.root,name)),'native_install_removal_unverified')
            del self.owned[name];self._refresh_directories(name);self.pending=False
        self.pending=True;self.ops.run('rmdir','--',self.root)
        native.require(self.ops.absent(self.root),'native_install_removal_unverified')
        self.pending=False;self.closed=True;self._token=None


def activate_installation(*args,**kwargs):
    raise native.ns.NamespaceProofError('native_installation_not_authorized')
