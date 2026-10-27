"""Explicit credential-free real PID-namespace lifecycle regression.

Actual CLONE_NEWPID, PID 1, pidfds and wait states; simulated cgroup migration.
Never a containment/release result. Kernel unavailability is NOT_RUN (exit78).
No privileged command, account provisioning, service or credential is involved.
"""
import ctypes
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hosted_synthetic_namespace as ns

ENV = {'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8'}
CASES = ('success','missing_stop','missing_guardian_stop','missing_ack','wrong_ack','wrong_generation',
         'guardian_death','guardian_death_during_enroll','cancel','early_init_exit','init_exit_during_enroll')
READBACK_CASES=('readback_reexec','readback_fork_only')
GENERATION = '1'*64


def _guardian(args):
    case, gate_number, status_number, marker_number, deadline, supervisor = args
    for number in (gate_number,status_number,marker_number):os.set_inheritable(int(number),False)
    gate=ns._Descriptor.capture(int(gate_number));status=ns._Descriptor.capture(int(status_number))
    marker=int(marker_number);deadline=float(deadline);supervisor=int(supervisor)
    os.dup2(marker,1);os.close(marker);marker=1
    # Bind death/parentage before the deliberate no-exec namespace transition.
    # Defer dumpable0 until after self-mapping: this deliberate U0-mm negative
    # would otherwise lose access to its own map files before reaching readback.
    ns.backend._browser_prctl(1,int(signal.SIGKILL))
    assert os.getppid()==supervisor
    if case=='readback_fork_only':
        # Deliberate negative: preserve U0 mm ownership across U1 creation.
        # No credential/runtime payload runs. The final nondumpable readback
        # must refuse despite the same namespace-owner effective UID.
        uid,gid=os.geteuid(),os.getegid()
        assert uid>0 and gid>0 and os.getgroups()==[]
        os.unshare(os.CLONE_NEWUSER|os.CLONE_NEWNS|os.CLONE_NEWPID|os.CLONE_NEWNET)
        for name,raw in (('uid_map',f'0 {uid} 1\n'.encode()),('setgroups',b'deny\n'),('gid_map',f'0 {gid} 1\n'.encode())):
            fd=os.open('/proc/self/'+name,os.O_WRONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
            try:assert os.write(fd,raw)==len(raw)
            finally:os.close(fd)
    # unshare(1) has exec'd this interpreter in its own mapped user namespace.
    # G remains in S's PID namespace; G's next fork is the real namespace PID 1.
    ns.backend._block_browser_projection_signals()
    ns._hardening(supervisor)
    if case in READBACK_CASES:
        assert ns.backend._browser_libc().prctl(3,0,0,0,0)==0
        os.write(marker,b'D')
    os.setsid()
    parent=ns._pidfd(supervisor);own=ns._pidfd(os.getpid())
    inner_read,inner_write=ns._pipe();result_read,result_write=ns._pipe()
    original_init=ns._init
    def selected_init(*values):
        assert values[-1] is True and (os.getpid(),os.getppid())==(1,0)
        if case in READBACK_CASES:
            assert ns.backend._browser_libc().prctl(3,0,0,0,0)==0
            os.write(marker,b'd')
        os.write(marker,b'I')
        # A marker after the actual PROBE boundary substitutes only downstream
        # private-proc/projection work, not namespace creation or stop/wait.
        def reached_probe():
            os.write(marker,b'P')
            os._exit(0)
        ns._mount_proc=reached_probe
        if case=='early_init_exit':os.write(marker,b'F');os._exit(3)
        return original_init(*values)
    ns._init=selected_init
    try:
        child,init_fd=ns._start_init(own,parent,inner_read,inner_write,result_read,result_write,
                                    GENERATION,supervisor,deadline,os.geteuid(),os.getegid(),{},True)
        if case=='guardian_death':os.write(marker,b'F');os.kill(os.getpid(),signal.SIGKILL)
        # The lifecycle test does not claim a real delegated cgroup. Only these
        # path readbacks are simulated; process/ns/pidfd/stop checks stay real.
        original_read=ns._read
        def read(path,maximum=ns._LIMIT):
            if path=='/proc/self/cgroup':return b'0::/regression/supervisor\n'
            if path==f'/proc/{child}/cgroup':return ('0::/regression/buffalo-'+GENERATION+'\n').encode()
            return original_read(path,maximum)
        ns._read=read
        if case=='missing_stop':
            dispatch=signal.pidfd_send_signal
            def omit_stop(fd,sig):
                if sig==signal.SIGSTOP:os.write(marker,b'F');return
                return dispatch(fd,sig)
            signal.pidfd_send_signal=omit_stop
        if case=='missing_guardian_stop':
            kill=os.kill
            def omit_guardian_stop(pid,sig):
                if pid==os.getpid() and sig==signal.SIGSTOP:os.write(marker,b'F');return
                return kill(pid,sig)
            os.kill=omit_guardian_stop
        ns._guardian_enrollment(gate,status,child,init_fd,GENERATION,deadline)
        ns._send(inner_write,GENERATION,'PROBE',deadline)
        ns._wait(child,deadline)
        return 0
    except BaseException:
        # In particular, NEVER reap I after ENROLL_INIT and before ACK. On G
        # death S adopts it and retains that zombie/PID until migration ends.
        return 1


def _case(case):
    assert case in (*CASES,*READBACK_CASES)
    if case in READBACK_CASES:
        from hosted_synthetic_launcher import inspect_host_identity
        inspect_host_identity()
    def fds():
        result=set()
        for name in os.listdir('/proc/self/fd'):
            try:os.fstat(int(name))
            except OSError:continue
            result.add(int(name))
        return result
    original_fds=fds()
    previous_mask=ns.backend._block_browser_projection_signals() if case in READBACK_CASES else None
    ns.backend._browser_prctl(36,1)  # S is the sole adopter/reaper.
    deadline=time.monotonic()+3.0
    gr,gw=ns._pipe();sr,sw=ns._pipe()
    mr,mw=os.pipe2(os.O_CLOEXEC|os.O_NONBLOCK)
    descriptors=[gr,gw,sr,sw];owners=[];process=None;guardian_fd=None
    reaped=False;writes=0;enrolled=None;refused=False;cleanup=False;markers=b''
    fault_complete=False;cancellation_observed=False
    readback=None;fork_only_refusal=False
    command=_guardian_command(case,gr.number,sw.number,mw,deadline,os.getpid())
    try:
        process=subprocess.Popen(command,env=ENV,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,pass_fds=(gr.number,sw.number,mw))
        gr.close();sw.close();os.close(mw);mw=-1
        guardian=process.pid;guardian_fd=ns._pidfd(guardian)
        start=ns._process(guardian)['start']
        class SimulatedLease:
            path='/regression/buffalo-'+GENERATION
            def enroll_stopped_init(self,**values):
                nonlocal writes,enrolled,markers,fault_complete,readback,fork_only_refusal
                enrolled=values['pid']
                assert ns._process(guardian)['state']=='T'
                assert ns._process(enrolled)['state']=='T'
                assert ns._process(enrolled)['parent']==guardian
                assert ns.parse_status(ns._read(f'/proc/{enrolled}/status',16384))['nspid']==[enrolled,1]
                try:markers+=os.read(mr,16)
                except BlockingIOError:pass
                assert markers==(b'DdI' if case in READBACK_CASES else b'I')
                if case in READBACK_CASES:
                    from hosted_synthetic_cgroup import observe_stopped_init_identity
                    try:readback=observe_stopped_init_identity(**values)
                    except ns.NamespaceProofError as error:
                        if case=='readback_fork_only' and re.fullmatch(r'_namespace_outer_(user|mnt|pid|net):(1|13)',str(error)):
                            fork_only_refusal=True
                        else:raise AssertionError('unexpected namespace readback failure') from error
                        raise
                    assert case=='readback_reexec', 'fork-only mm unexpectedly readable'
                if case=='init_exit_during_enroll':
                    signal.pidfd_send_signal(values['pidfd'].number,signal.SIGKILL)
                    until=time.monotonic()+1
                    while not ns.backend._browser_pidfd_is_terminal(values['pidfd'].number):
                        assert time.monotonic()<until;time.sleep(.005)
                    assert ns._process(enrolled)['start']==values['start']
                    assert ns._process(enrolled)['parent']==guardian and ns._process(guardian)['state']=='T'
                    fault_complete=True
                    raise ns.NamespaceProofError('init_exited_before_migration')
                if case in ('guardian_death','guardian_death_during_enroll'):
                    signal.pidfd_send_signal(guardian_fd.number,signal.SIGKILL)
                    until=time.monotonic()+1
                    while ns._process(enrolled)['parent']!=os.getpid():
                        assert time.monotonic()<until;time.sleep(.005)
                    # Even an exited I remains our unreaped child, so no foreign
                    # process can acquire its PID while migration is in flight.
                    assert ns._process(enrolled)['start']==values['start']
                    if case=='guardian_death_during_enroll':
                        while not ns.backend._browser_pidfd_is_terminal(values['pidfd'].number):
                            assert time.monotonic()<until;time.sleep(.005)
                    fault_complete=True
                    raise ns.NamespaceProofError('guardian_died_before_migration')
                if case=='cancel':
                    os.kill(os.getpid(),signal.SIGINT)
                    raise AssertionError('SIGINT failed to interrupt')
                writes+=1  # explicitly simulated write, never a cgroup syscall
        original_send=ns._send
        def send(fd,generation,stage,limit,**fields):
            nonlocal fault_complete
            if stage=='ENROLLED':
                if case=='missing_ack':fault_complete=True;return
                if case=='wrong_ack':fields['start']+=1;fault_complete=True
                if case=='wrong_generation':generation='2'*64;fault_complete=True
            return original_send(fd,generation,stage,limit,**fields)
        ns._send=send
        try:
            ns._supervisor_enrollment(SimulatedLease(),gw,sr,guardian_fd,guardian,start,
                                      GENERATION,deadline,owners)
            code=process.wait(timeout=max(.1,deadline-time.monotonic()+1));reaped=True
            refused=code!=0
        except KeyboardInterrupt:
            cancellation_observed=True
            refused=True
        except (ns.NamespaceProofError,ns.backend.LocalStagingAcceptanceError,BrokenPipeError):
            refused=True
        finally:ns._send=original_send
        if case in ('success','readback_reexec'):assert not refused and writes==1
        else:assert refused
        if case=='readback_reexec':assert readback is not None and readback['execution_authority'] is False
        if case=='readback_fork_only':assert fork_only_refusal and writes==0 and readback is None
        if case in ('missing_ack','wrong_ack','wrong_generation','guardian_death_during_enroll','init_exit_during_enroll'):
            assert fault_complete
        if case in ('missing_ack','wrong_ack','wrong_generation'):
            assert writes==1 and reaped and process.returncode==1
        if case=='cancel':assert cancellation_observed
        if case in ('missing_stop','missing_guardian_stop','guardian_death','guardian_death_during_enroll','cancel','early_init_exit','init_exit_during_enroll'):
            assert writes==0
    finally:
        if process is not None:
            if not reaped:
                # G is our retained direct child, so its numerical group ID
                # cannot be reused before the following wait.
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                try:process.kill()
                except ProcessLookupError:pass
                process.wait(timeout=3)
            # G may have exited naturally on a wrong/missing ACK. Its init is
            # now S's child with PDEATHSIG; kill any retained I by exact pidfd.
            for item in owners:
                if not ns.backend._browser_pidfd_is_terminal(item.number):
                    signal.pidfd_send_signal(item.number,signal.SIGKILL)
            until=time.monotonic()+3
            while True:
                try:pid,_=os.waitpid(-process.pid,os.WNOHANG)
                except ChildProcessError:break
                if pid==0:
                    assert time.monotonic()<until;time.sleep(.005)
            assert all(ns.backend._browser_pidfd_is_terminal(item.number) for item in owners)
        for item in [*owners,*descriptors,*([guardian_fd] if guardian_fd is not None else [])]:item.close()
        if mw>=0:os.close(mw)
        markers+=os.read(mr,16);os.close(mr)
        expected=(b'DdIP' if case=='readback_reexec' else b'DdI' if case=='readback_fork_only' else b'IP' if case=='success' else
                  b'IF' if case in ('missing_stop','missing_guardian_stop','early_init_exit','guardian_death') else b'I')
        assert markers==expected
        # Some supported procfs configurations omit task/<tid>/children.
        # Unreaped direct children cannot disappear from this parent scan.
        entries=list(Path('/proc').iterdir());assert len(entries)<=65536
        for entry in entries:
            if not entry.name.isdecimal():continue
            try:observed=ns._process(int(entry.name))
            except FileNotFoundError:continue
            assert observed['parent']!=os.getpid()
        assert fds()==original_fds
        # Every owned process has exited and all our descriptors are closed.
        cleanup=True
        if previous_mask is not None:ns.backend._restore_browser_projection_signals(previous_mask)
    return dict(case=case,result='PASS',real_clone_newpid=True,cgroup_required=True,init_pid1_observed=True,
                simulated_cgroup_writes=writes,cleanup_complete=cleanup,
                private_proc_executed=False,provider_calls=0,execution_authority=False,
                **({'namespace_readback':readback,'fork_only_refusal':fork_only_refusal,
                    'guardian_and_init_dumpable_zero':True} if case in READBACK_CASES else {}))


def _guardian_command(case,gate,status,marker,deadline,supervisor):
    assert case in (*CASES,*READBACK_CASES)
    child=[sys.executable,'-I','-S','-B',__file__,'--guardian',case,
           str(gate),str(status),str(marker),str(deadline),str(supervisor)]
    if case=='readback_fork_only':return child
    return ['/usr/bin/unshare','--user','--map-root-user','--mount','--pid',
            *(['--net'] if case=='readback_reexec' else []),'--',*child]


def _readback_source():
    from hosted_synthetic_source import bind_hosted_source,_git,_oid,REPOSITORY,REF
    root=Path(__file__).resolve().parents[2]
    return bind_hosted_source(root,repository=REPOSITORY,event='push',ref=REF,
        sha=_oid(_git(root,'rev-parse','HEAD')))


def _case_result(case,result):
    assert type(result) is dict and result.get('case')==case and result.get('result')=='PASS'
    assert all(result.get(key) is True for key in ('real_clone_newpid','cgroup_required','init_pid1_observed','cleanup_complete'))
    assert result.get('private_proc_executed') is False and result.get('execution_authority') is False
    assert type(result.get('provider_calls')) is int and result['provider_calls']==0
    if case in READBACK_CASES:
        assert result.get('guardian_and_init_dumpable_zero') is True
        assert type(result.get('simulated_cgroup_writes')) is int
        if case=='readback_reexec':
            proof=result.get('namespace_readback')
            assert type(proof) is dict and proof.get('execution_authority') is False
            assert proof.get('scope')=='STOPPED_NAMESPACE_READBACK_ONLY'
            assert set(proof)=={'init','guardian','init_namespaces','guardian_namespaces',
                'supervisor_namespaces','supervisor_identity','scope','execution_authority'}
            for name in ('init','guardian'):
                record=proof[name]
                assert type(record) is dict and set(record)=={'pid','state','parent','group','session','start'}
                assert record['state']=='T' and all(type(record[key]) is int and record[key]>1
                    for key in ('pid','parent','group','session'))
                assert type(record['start']) is int and record['start']>0
            init,guardian=proof['init'],proof['guardian']
            assert init['parent']==guardian['pid'] and len({init['pid'],guardian['pid'],guardian['parent']})==3
            assert init['group']==init['session']==guardian['group']==guardian['session']==guardian['pid']
            for name in ('init_namespaces','guardian_namespaces','supervisor_namespaces'):
                record=proof[name]
                assert type(record) is dict and set(record)=={'user','mnt','pid','net'}
                assert all(type(value) is list and len(value)==2 and all(type(x) is int and x>0 for x in value)
                    for value in record.values())
            i,g,s=(proof[name] for name in ('init_namespaces','guardian_namespaces','supervisor_namespaces'))
            assert i['user']==g['user']!=s['user'] and i['net']==g['net']!=s['net']
            assert i['mnt']!=g['mnt'] and i['pid']!=g['pid']==s['pid']
            host=proof['supervisor_identity']
            assert type(host) is dict and set(host)=={'uids','gids','groups','capabilities','no_new_privs'}
            for name in ('uids','gids'):
                assert type(host[name]) is list and len(host[name])==4
                assert all(type(x) is int and x>0 for x in host[name]) and len(set(host[name]))==1
            assert host['groups']==[] and type(host['no_new_privs']) is int and host['no_new_privs']==1
            assert type(host['capabilities']) is dict and set(host['capabilities'])=={'CapInh','CapPrm','CapEff','CapBnd','CapAmb'}
            assert all(type(x) is int and x==0 for x in host['capabilities'].values())
            assert result.get('fork_only_refusal') is False and result['simulated_cgroup_writes']==1
        else:
            assert result.get('namespace_readback') is None and result.get('fork_only_refusal') is True
            assert result['simulated_cgroup_writes']==0


def main(args=None):
    args=sys.argv[1:] if args is None else args
    if len(args)==7 and args[0]=='--guardian':return _guardian(args[1:])
    readback=args==['--run-nondumpable-readback']
    if (args!=['--run-pid1-regressions'] and not readback) or os.geteuid()==0:return 78
    source=None
    if readback:
        from hosted_synthetic_launcher import inspect_host_identity,LauncherRefusal
        from hosted_synthetic_source import HostedSourceError
        try:inspect_host_identity();source=_readback_source()
        except (LauncherRefusal,HostedSourceError,OSError):
            print(json.dumps({'real_pid1_regression':'NOT_RUN','scope':'NON_DUMPABLE_READBACK_ONLY',
                'reason':'ZERO_CAPABILITY_OR_BOUND_SOURCE_PREREQUISITE_UNAVAILABLE','execution_authority':False}))
            return 78
    # Single bounded, unprivileged capability check; no retry or workaround.
    try:
        probe=subprocess.run(['/usr/bin/unshare','--user','--map-root-user','--mount','--pid',*(['--net'] if readback else []),'--fork',
                              '/usr/bin/true'],env=ENV,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,timeout=5)
    except (OSError,subprocess.TimeoutExpired):
        print(json.dumps({'real_pid1_regression':'FAIL','reason':'CAPABILITY_PROBE_INCOMPLETE',
            'cleanup':'NOT_PROVEN','execution_authority':False}))
        return 1
    if probe.returncode:
        print(json.dumps({'real_pid1_regression':'NOT_RUN','reason':'UNPRIVILEGED_NAMESPACE_CAPABILITY_UNAVAILABLE',
                          'probe_returncode':probe.returncode,'execution_authority':False}))
        return 78
    results=[]
    for case in (READBACK_CASES if readback else CASES):
        # Fresh S per case isolates subreaper/signal/process state.
        command=[sys.executable,'-I','-S','-B','-c',
                 'import sys,json;sys.path.insert(0,sys.argv[1]);import hosted_synthetic_pid1_regression as r;print(json.dumps(r._case(sys.argv[2]),sort_keys=True))',
                 str(Path(__file__).resolve().parent),case]
        try:run=subprocess.run(command,env=ENV,capture_output=True,timeout=10)
        except (OSError,subprocess.TimeoutExpired):
            print(json.dumps({'real_pid1_regression':'FAIL','case':case,'reason':'CASE_INCOMPLETE',
                'completed_cases':results,'cleanup':'NOT_PROVEN','execution_authority':False,
                **({'source_commit':source.commit,'source_tree':source.tree} if readback else {})}))
            return 1
        if run.returncode:
            print(json.dumps({'real_pid1_regression':'FAIL','case':case,'returncode':run.returncode,
                              'completed_cases':results,'execution_authority':False}))
            return 1
        try:result=json.loads(run.stdout);_case_result(case,result)
        except (ValueError,AssertionError):
            print(json.dumps({'real_pid1_regression':'FAIL','case':case,'reason':'INVALID_CASE_RESULT',
                'completed_cases':results,'execution_authority':False}))
            return 1
        results.append(result)
    if readback:
        try:assert _readback_source()==source
        except (HostedSourceError,OSError,AssertionError):
            print(json.dumps({'real_pid1_regression':'FAIL','reason':'SOURCE_CHANGED',
                'completed_cases':results,'execution_authority':False}))
            return 1
    print(json.dumps({'real_pid1_regression':'PASS','scope':('ZERO_HOST_CAPABILITY_READBACK_COMPONENT' if readback else 'REAL_PID_NAMESPACE_SIMULATED_CGROUP_ONLY'),
                      'cases':results,'execution_authority':False,
                      **({'source_commit':source.commit,'source_tree':source.tree,'cgroup_migration':'NOT_RUN',
                          'runtime_closure':'NOT_PROVEN'} if readback else {})},sort_keys=True))
    return 0


if __name__=='__main__':raise SystemExit(main())
