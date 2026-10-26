"""Explicit credential-free real PID-namespace lifecycle regression.

Actual CLONE_NEWPID, PID 1, pidfds and wait states; simulated cgroup migration.
Never a containment/release result. Kernel unavailability is NOT_RUN (exit78).
No privileged command, account provisioning, service or credential is involved.
"""
import ctypes
import json
import os
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
GENERATION = '1'*64


def _guardian(args):
    case, gate_number, status_number, marker_number, deadline, supervisor = args
    for number in (gate_number,status_number,marker_number):os.set_inheritable(int(number),False)
    gate=ns._Descriptor.capture(int(gate_number));status=ns._Descriptor.capture(int(status_number))
    marker=int(marker_number);deadline=float(deadline);supervisor=int(supervisor)
    os.dup2(marker,1);os.close(marker);marker=1
    # unshare(1) has exec'd this interpreter in its own mapped user namespace.
    # G remains in S's PID namespace; G's next fork is the real namespace PID 1.
    ns.backend._block_browser_projection_signals()
    ns._hardening(supervisor)
    os.setsid()
    parent=ns._pidfd(supervisor);own=ns._pidfd(os.getpid())
    inner_read,inner_write=ns._pipe();result_read,result_write=ns._pipe()
    original_init=ns._init
    def selected_init(*values):
        assert values[-1] is True and (os.getpid(),os.getppid())==(1,0)
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
    assert case in CASES
    def fds():
        result=set()
        for name in os.listdir('/proc/self/fd'):
            try:os.fstat(int(name))
            except OSError:continue
            result.add(int(name))
        return result
    original_fds=fds()
    ns.backend._browser_prctl(36,1)  # S is the sole adopter/reaper.
    deadline=time.monotonic()+3.0
    gr,gw=ns._pipe();sr,sw=ns._pipe()
    mr,mw=os.pipe2(os.O_CLOEXEC|os.O_NONBLOCK)
    descriptors=[gr,gw,sr,sw];owners=[];process=None;guardian_fd=None
    reaped=False;writes=0;enrolled=None;refused=False;cleanup=False;markers=b''
    fault_complete=False;cancellation_observed=False
    command=['/usr/bin/unshare','--user','--map-root-user','--mount','--pid','--',
             sys.executable,'-I','-S','-B',__file__,'--guardian',case,str(gr.number),str(sw.number),
             str(mw),str(deadline),str(os.getpid())]
    try:
        process=subprocess.Popen(command,env=ENV,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,pass_fds=(gr.number,sw.number,mw))
        gr.close();sw.close();os.close(mw);mw=-1
        guardian=process.pid;guardian_fd=ns._pidfd(guardian)
        start=ns._process(guardian)['start']
        class SimulatedLease:
            path='/regression/buffalo-'+GENERATION
            def enroll_stopped_init(self,**values):
                nonlocal writes,enrolled,markers,fault_complete
                enrolled=values['pid']
                assert ns._process(guardian)['state']=='T'
                assert ns._process(enrolled)['state']=='T'
                assert ns._process(enrolled)['parent']==guardian
                assert ns.parse_status(ns._read(f'/proc/{enrolled}/status',16384))['nspid']==[enrolled,1]
                try:markers+=os.read(mr,16)
                except BlockingIOError:pass
                assert markers==b'I'
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
        if case=='success':assert not refused and writes==1
        else:assert refused
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
        expected=(b'IP' if case=='success' else
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
    return dict(case=case,result='PASS',real_clone_newpid=True,cgroup_required=True,init_pid1_observed=True,
                simulated_cgroup_writes=writes,cleanup_complete=cleanup,
                private_proc_executed=False,provider_calls=0,execution_authority=False)


def main(args=None):
    args=sys.argv[1:] if args is None else args
    if len(args)==7 and args[0]=='--guardian':return _guardian(args[1:])
    if args!=['--run-pid1-regressions'] or os.geteuid()==0:return 78
    # Single bounded, unprivileged capability check; no retry or workaround.
    probe=subprocess.run(['/usr/bin/unshare','--user','--map-root-user','--mount','--pid','--fork',
                          '/usr/bin/true'],env=ENV,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,timeout=5)
    if probe.returncode:
        print(json.dumps({'real_pid1_regression':'NOT_RUN','reason':'UNPRIVILEGED_NAMESPACE_CAPABILITY_UNAVAILABLE',
                          'probe_returncode':probe.returncode,'execution_authority':False}))
        return 78
    results=[]
    for case in CASES:
        # Fresh S per case isolates subreaper/signal/process state.
        command=[sys.executable,'-I','-S','-B','-c',
                 'import sys,json;sys.path.insert(0,sys.argv[1]);import hosted_synthetic_pid1_regression as r;print(json.dumps(r._case(sys.argv[2]),sort_keys=True))',
                 str(Path(__file__).resolve().parent),case]
        run=subprocess.run(command,env=ENV,capture_output=True,timeout=10)
        if run.returncode:
            print(json.dumps({'real_pid1_regression':'FAIL','case':case,'returncode':run.returncode,
                              'completed_cases':results,'execution_authority':False}))
            return 1
        result=json.loads(run.stdout);results.append(result)
    print(json.dumps({'real_pid1_regression':'PASS','scope':'REAL_PID_NAMESPACE_SIMULATED_CGROUP_ONLY',
                      'cases':results,'execution_authority':False},sort_keys=True))
    return 0


if __name__=='__main__':raise SystemExit(main())
