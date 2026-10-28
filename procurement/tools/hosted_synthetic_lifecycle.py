"""Dormant fixed credential-free lifecycle cases for the reviewed native path.

No CLI, environment activation, arbitrary callback or caller-supplied PASS.
Each invocation owns one account/service/generation. Negative assertion success
is separate from cleanup/resource acceptance; quarantine never means drained.
"""
import json
import os
from pathlib import Path
import signal
import time

import hosted_synthetic_bootstrap as boot
import hosted_synthetic_namespace as ns
import hosted_synthetic_observer as observation
import hosted_synthetic_identity as identity
import hosted_synthetic_source as source

CASES=frozenset(('normal','timeout','cancellation','manager_kill','lost_ack','partial_setup'))
_TOKEN=object()


class PartialSetupFault(boot.BootstrapRefusal):pass


class LifecycleCase:
    def __init__(self,case,binding,selected):
        boot._require(type(case) is str and case in CASES and type(binding) is source.HostedSourceBinding)
        identity.validate(selected)
        boot._require(os.getresuid()==(selected.operator_uid,)*3 and os.getresgid()==(selected.operator_gid,)*3
                      and signal.getsignal(signal.SIGINT)==signal.default_int_handler
                      and signal.SIGINT not in signal.sigpending())
        self.case=case;self.binding=binding;self.identity=selected;self._owner=self;self._token=_TOKEN
        self.operator=ns._process(os.getpid());self.lease=None;self.observer=None
        self.events=[];self.actors={};self.cancel_sent=False;self.kill_sent=False
        self.child_report=None;self.child_evidence=None;self.partial=False;self.finished=False;self.native=None

    def verify(self):
        boot._require(self._owner is self and self._token is _TOKEN and not self.finished
                      and ns._process(os.getpid())['start']==self.operator['start']
                      and os.getpid()==self.operator['pid']
                      and os.getresuid()==(self.identity.operator_uid,)*3
                      and os.getresgid()==(self.identity.operator_gid,)*3)

    def account_created(self,lease):
        self.verify()
        boot._require(self.lease is None and type(lease) is identity.AccountLease and lease._owner is lease
                      and lease.identity==self.identity and lease.source_commit==self.binding.commit)
        self.lease=lease

    def provisioned(self,index):
        self.verify();boot._require(self.lease is not None and type(index) is int and 0<=index<8)
        self.events.append('provision_ack_'+str(index))
        if self.case=='partial_setup' and index==3:
            # Account creation, lock and no-sudo policy were acknowledged. No
            # source directory or service exists yet; retain identity on fault.
            self.partial=True
            raise PartialSetupFault('lifecycle_partial_setup_injected')

    def native_observed(self,value):
        self.verify();boot._require(self.native is None and type(value) is dict)
        self.native=value

    def observer_created(self,observer):
        self.verify()
        boot._require(self.observer is None and type(observer) is observation.ExternalObserver
                      and observer.account is self.lease and observer.binding==self.binding)
        self.observer=observer

    def _live_service(self):
        self.verify();observer=self.observer
        boot._require(observer is not None and observer.generation is not None and len(observer.actors)==5)
        observer.verify();observer.service.verify();observer.generation_dir.verify()
        current=observation._manager(observer.unit,service=True)
        boot._require(current['InvocationID']==observer.service_invocation
                      and current['ControlGroup']==observation.service_path(observer.unit)
                      and current['ActiveState'] in ('activating','active'))
        for role,descriptor in observer.actors.items():
            descriptor.verify()
            _,pid,_,_=ns.backend._read_browser_pidfd_metadata(descriptor.number)
            selected=ns._process(pid)
            boot._require(not ns.backend._browser_pidfd_is_terminal(descriptor.number)
                          and selected['start']==self.actors[role]['start'] and pid==self.actors[role]['pid'])
            expected=observation.service_path(observer.unit)+('/buffalo-'+observer.generation if role in ('init','worker') else '/supervisor')
            boot._require(ns.backend.parse_browser_cgroup_path(ns._read(f'/proc/{pid}/cgroup',4096))==expected)
        return current

    def _signal_service(self,value):
        boot._require(value in ('SIGSTOP','SIGKILL'))
        self._live_service()
        command=observation._native(('/usr/bin/systemctl','--system','--no-ask-password','kill',
            '--kill-whom=all','--signal='+value,self.identity.unit))
        code,_=boot._collect_native_client(command,cwd='/')
        boot._require(code==0,'lifecycle_native_signal_unacknowledged')
        self.events.append('native_'+value+'_acknowledged')
        if value=='SIGSTOP':
            deadline=time.monotonic()+2
            while True:
                self._live_service()
                if all(ns._process(item['pid'])['state']=='T' for item in self.actors.values()):break
                boot._require(time.monotonic()<deadline,'lifecycle_stop_unobserved')
                time.sleep(.01)
            self.events.append('all_bound_actors_stopped')
        else:self.kill_sent=True

    def before_ack(self,observer,frame):
        self.verify();boot._require(observer is self.observer and not self.actors)
        self.actors={role:dict(value) for role,value in frame['actors'].items()}
        self._live_service();self.events.append('generation_bound_before_fault')
        if self.case=='manager_kill':self._signal_service('SIGSTOP')
        if self.case=='cancellation':
            boot._require(signal.SIGINT in signal.pthread_sigmask(signal.SIG_BLOCK,set())
                          and signal.SIGINT not in signal.sigpending())
            os.kill(os.getpid(),signal.SIGINT)
            boot._require(signal.SIGINT in signal.sigpending())
            self.cancel_sent=True;self.events.append('owned_operator_SIGINT_pending')
        # Timeout leaves its pipe open until the existing namespace deadline;
        # lost_ack closes it immediately. Neither claims an ACK was sent.
        return 'HOLD' if self.case=='timeout' else ('DROP' if self.case=='lost_ack' else 'SEND')

    def after_ack(self,observer):
        self.verify();boot._require(observer is self.observer and observer.work_acknowledged)
        self.events.append('observer_ack_sent')
        if self.case=='manager_kill':self._signal_service('SIGKILL')

    def report_received(self,report):
        self.verify()
        boot._require(report['commit']==self.binding.commit and report['tree']==self.binding.tree
                      and report['provider_calls']==0 and report['credential_release']=='WITHHELD'
                      and report['execution_authority'] is False)
        self.child_evidence=boot.snapshot_diagnostic(report,self.binding)
        self.child_report=report

    def _quarantined(self):
        lease=self.lease
        boot._require(lease is not None and lease.uncertain and not lease.closed)
        if lease.drain_proof is not None:
            # External resource drainage can succeed before account deletion
            # fails. It does not release an uncertain numeric identity.
            observation.require_drain_proof(lease.drain_proof,lease)
        journal=lease.journal
        descriptor=ns._acquire(lambda:os.open(journal.path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW))
        try:journal._verify_descriptor(descriptor)
        finally:descriptor.close()
        from hosted_synthetic_quarantine import _decode,reserved_ids
        header,stages=_decode(journal.raw)
        boot._require(header['commit']==self.binding.commit and header['nonce']==self.identity.nonce
                      and 'RESOLVED' not in stages
                      and (self.identity.uid,self.identity.gid) in reserved_ids())
        return stages

    def finish(self,result,error):
        self.verify();lease=self.lease;observer=self.observer
        drained=False;quarantine=False;expected=False
        if lease is not None and lease.closed and not lease.uncertain:
            observation.require_drain_proof(lease.drain_proof,lease)
            drained=True
        if self.case=='normal':expected=error is None and result is not None and drained
        elif self.case=='cancellation':expected=isinstance(error,KeyboardInterrupt) and self.cancel_sent and drained
        elif self.case=='manager_kill':
            expected=isinstance(error,boot.BootstrapRefusal) and self.kill_sent and drained and lease.drain_proof.operation_succeeded is False
        elif self.case in ('timeout','lost_ack'):
            if lease is not None and lease.uncertain:
                self._quarantined();quarantine=True
            namespace=(self.child_report or {}).get('namespace_attempt') or {}
            code='external_observer_timeout' if self.case=='timeout' else 'external_observer_ack_refused'
            expected=(isinstance(error,boot.BootstrapRefusal) and (drained or quarantine)
                      and observer is not None and observer.closed and not observer.work_acknowledged
                      and observer.descriptors_closed_successfully is True
                      and namespace.get('external_observer_failure')==code)
        elif self.case=='partial_setup':
            if lease is not None and lease.uncertain:
                stages=self._quarantined();quarantine=True
                expected=(isinstance(error,boot.BootstrapCleanupError) and self.partial and observer is None
                          and stages[-1]=='provision_ack_3' and not lease.service_attempted
                          and not identity.identity_processes(self.identity)
                          and not os.path.lexists(self.identity.root)
                          and not os.path.lexists('/sys/fs/cgroup'+observation.service_path(self.identity.unit)))
        self.finished=True
        return {'contract':'BUFFALO_HOSTED_LIFECYCLE_CASE_V1','case':self.case,
            'commit':self.binding.commit,'tree':self.binding.tree,'nonce':self.identity.nonce,
            'generation':observer.generation if observer else None,'actors':self.actors,'events':self.events,
            'assertion':'PASS' if expected else 'FAIL','operation':'SUCCEEDED' if error is None else 'FAILED',
            'cleanup':'DRAINED_IDENTITY_REMOVED' if drained else ('QUARANTINED' if quarantine else 'UNVERIFIED'),
            'resource_acceptance':'AGGREGATE_OBSERVED' if drained else 'UNVERIFIED',
            'cleanup_scope':lease.drain_proof.scope if drained else 'UNVERIFIED',
            'acknowledged_generation_lifetime':drained and lease.drain_proof.scope=='ACKNOWLEDGED_GENERATION',
            'child_resources':((self.child_report or {}).get('namespace_attempt') or {}).get('resource_cleanup'),
            'native_prerequisites':self.native,'account_native_programs':lease.platform if lease else None,
            'aggregate':lease.drain_proof.aggregate if drained else None,
            'observer_descriptors_closed':getattr(observer,'descriptors_closed_successfully',False) if observer else None,
            'failure_class':type(error).__name__ if error else None,
            'diagnostic_evidence':(json.loads(self.child_evidence.encoded)
                if self.child_evidence is not None else boot.diagnostic_failure_evidence(error,binding=self.binding)),
            'privileged_live_acceptance':'INCOMPLETE','credential_release':'WITHHELD','provider_calls':0,
            'application_browser_execution':'NOT_RUN','execution_authority':False}


def require_case(value,selected,commit):
    boot._require(type(value) is LifecycleCase and value.identity==selected and value.binding.commit==commit)
    value.verify()


def run_lifecycle_case(case,selected,*,root,python,repository,event,ref,commit,diagnostic="retained"):
    """Reviewed wiring only; not reachable from bootstrap.main or workflow."""
    from hosted_synthetic_capability import _require_clean_supervisor
    _require_clean_supervisor()
    boot._require(type(case) is str and case in CASES and diagnostic in ("retained","postgres-version"))
    binding=source.bind_hosted_source(Path(root),repository=repository,event=event,ref=ref,sha=commit)
    fault=LifecycleCase(case,binding,selected)
    result=None;error=None
    try:
        result=identity.run_provisioned_candidate(selected,source=root,python=python,
            repository=repository,event=event,ref=ref,commit=commit,lifecycle=fault,
            **({"diagnostic":diagnostic} if diagnostic!="retained" else {}))
    except BaseException as failure:error=failure
    try:report=fault.finish(result,error)
    except BaseException as evidence_error:
        if error is not None:
            evidence_error.add_note('lifecycle_original_operation_failure_retained')
            raise evidence_error from error
        raise
    if isinstance(error,(KeyboardInterrupt,SystemExit)) and not (case=='cancellation' and fault.cancel_sent):raise error
    if diagnostic=="postgres-version":report["diagnostic"]="FIXED_POSTGRES_VERSION"
    return report
