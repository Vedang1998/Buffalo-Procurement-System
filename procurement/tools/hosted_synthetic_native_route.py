"""Exact dormant installation -> measurement -> account/service -> drain route.

No CLI, workflow, environment switch or caller approval flag activates this.
Native installation still requires the single external decision and delta review.
All Python and archive parsing run as the unprivileged operator; only the fixed
importer's native commands receive privilege. No downloads/install scripts here.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import datetime
import fcntl
import gzip
import hashlib
import io
import lzma
import re
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import tempfile
import time
import uuid

import hosted_synthetic_native_inputs as native
import hosted_synthetic_native_installation as install
import hosted_synthetic_native_measurement as measurement
import hosted_synthetic_identity as identity
import hosted_synthetic_bootstrap as boot

_SCOPE=ContextVar('buffalo_fixed_native_installation_scope',default=None)
_TOKEN=object()
_EVIDENCE=Path(__file__).resolve().parents[2]/'docs/review/2026-10-27-runtime-control-lifecycle'


def _installation_decision_gate():
    raise native.ns.NamespaceProofError('native_installation_not_authorized')


def require_installation_scope():
    native.require(_SCOPE.get() is _TOKEN,'native_installation_not_authorized')


def _check(deadline):install._work_deadline(deadline)


def _artifact_fd(path,digest,size,deadline):
    """Fixed digest, held no-follow file; mutable transport never becomes trust."""
    _check(deadline);fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
    try:
        _verify_fd(fd,digest,size,deadline);return fd
    except BaseException:os.close(fd);raise


def _verify_fd(fd,digest,size,deadline):
    before=os.fstat(fd)
    native.require(stat.S_ISREG(before.st_mode) and before.st_size==size and not before.st_mode&0o6000,
        'native_artifact_shape_refused')
    h=hashlib.sha256();offset=0
    while offset<size:
        _check(deadline);raw=os.pread(fd,min(65536,size-offset),offset)
        native.require(raw,'native_artifact_truncated');h.update(raw);offset+=len(raw)
    native.require(h.hexdigest()==digest and native._metadata(before)==native._metadata(os.fstat(fd)),
        'native_artifact_digest_refused')
    return native._metadata(before)


@contextmanager
def _sealed_metadata(raw):
    """Protected public trust material; immutable held bytes, not writable host keyring."""
    fd=os.memfd_create('buffalo-public-metadata',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING);reader=None
    try:
        view=memoryview(raw)
        while view:
            count=os.write(fd,view);native.require(count>0);view=view[count:]
        os.fchmod(fd,0o444)
        seals=fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL
        fcntl.fcntl(fd,fcntl.F_ADD_SEALS,seals)
        reader=os.open('/proc/self/fd/'+str(fd),os.O_RDONLY|os.O_CLOEXEC)
        native.require(fcntl.fcntl(reader,fcntl.F_GET_SEALS)==seals and
            fcntl.fcntl(reader,fcntl.F_GETFL)&os.O_ACCMODE==os.O_RDONLY)
        os.close(fd);fd=None;yield reader
    finally:
        if reader is not None:os.close(reader)
        if fd is not None:os.close(fd)


def _release_index_binding(raw,index):
    """Only called after signature verification; parse the signed SHA256 section."""
    native.require(type(raw) is bytes and len(raw)<=2*1024**2)
    target=(index['component']+'/binary-amd64/Packages.xz').encode()
    active=False;sections=0;found=[]
    for line in raw.splitlines():
        if line==b'SHA256:':active=True;sections+=1;continue
        if active and not line.startswith(b' '):active=False
        if active:
            fields=line.split();native.require(len(fields)==3,'native_release_index_schema_refused')
            if fields[2]==target:found.append(fields)
    native.require(sections==1 and found==[[index['sha256'].encode(),str(index['bytes']).encode(),target]],
        'native_release_index_binding_refused')


def _verified_release_payload(result,signer,expected_digest):
    """Only verifier-emitted signed text may supply package-index bindings.

    The raw armored bytes are pinned separately before invoking gpgv. No hand
    stripping of armor, dash escaping or trailing unsigned data occurs here.
    """
    native.require(type(result.stdout) is bytes and 0<len(result.stdout)<=2*1024**2
        and type(result.stderr) is bytes and len(result.stderr)<=4096 and result.returncode==0,
        'native_release_signature_refused')
    statuses=[]
    for line in result.stderr.splitlines():
        if not line.startswith(b'[GNUPG:]'):continue  # Native human diagnostics.
        fields=line.split()
        native.require(line.startswith(b'[GNUPG:] ') and len(fields)>=2
            and re.fullmatch(rb'[A-Z][A-Z0-9_]*',fields[1]),'native_release_status_schema_refused')
        statuses.append(fields)
    negative={b'BADSIG',b'ERRSIG',b'NO_PUBKEY',b'FAILURE',b'REVKEYSIG',b'KEYREVOKED',b'EXPKEYSIG',b'EXPSIG'}
    good=[f for f in statuses if f[1]==b'GOODSIG'];valid=[f for f in statuses if f[1]==b'VALIDSIG']
    native.require(not any(f[1] in negative for f in statuses)
        and len(good)==len(valid)==1,'native_release_signer_refused')
    # GnuPG DETAILS: nine required VALIDSIG arguments, optional primary FPR.
    # Authorization uses the full signing FPR, never a display name/primary hint.
    v=valid[0];g=good[0];timestamp=rb'(?:[0-9]+|[0-9]{8}T[0-9]{6})'
    native.require(type(signer) is str and re.fullmatch('[0-9A-F]{40}',signer)
        and len(v) in (11,12) and re.fullmatch(rb'[0-9A-Fa-f]{40}',v[2])
        and v[2].upper()==signer.encode('ascii')
        and re.fullmatch(rb'[0-9]{4}-[0-9]{2}-[0-9]{2}',v[3])
        and all(re.fullmatch(timestamp,v[i]) for i in (4,5))
        and all(re.fullmatch(rb'[0-9]+',v[i]) for i in (6,7,8,9))
        and re.fullmatch(rb'[0-9A-Fa-f]{2}',v[10])
        and (len(v)==11 or re.fullmatch(rb'[0-9A-Fa-f]{40}',v[11]))
        and len(g)>=4 and re.fullmatch(rb'(?:[0-9A-Fa-f]{16}|[0-9A-Fa-f]{40})',g[2])
        and g[2].upper() in (v[2].upper(),v[2][-16:].upper()),'native_release_signer_refused')
    try:
        datetime.date.fromisoformat(v[3].decode('ascii'))
        for field in (v[4],v[5]):
            if b'T' in field:datetime.datetime.fromisoformat(field.decode('ascii'))
    except ValueError:
        raise native.ns.NamespaceProofError('native_release_signer_refused') from None
    # These are schema checks, not a wall-clock expiry/freshness policy.
    # Informational statuses remain allowed, including KEYEXPIRED for an unused
    # subkey. EXPKEYSIG describes signing-key expiry. No key-freshness inference.
    native.require(type(expected_digest) is str and re.fullmatch('[0-9a-f]{64}',expected_digest)
        and hashlib.sha256(result.stdout).hexdigest()==expected_digest,'native_verified_release_payload_refused')
    return result.stdout


def _index_records(fd,index,deadline):
    """Bounded incremental XZ, including the publisher's empty second stream."""
    native.require(type(index['uncompressed_bytes']) is int and 0<index['uncompressed_bytes']<=64*1024**2)
    decoder=lzma.LZMADecompressor(memlimit=64*1024**2)
    pending=b'';offset=0;produced=0;digest=hashlib.sha256();carry=b'';streams=1
    try:
        while True:
            _check(deadline)
            if decoder.eof:
                carry=decoder.unused_data
                if not carry and offset<index['bytes']:
                    carry=os.pread(fd,min(65536,index['bytes']-offset),offset);offset+=len(carry)
                if not carry:break
                # Stream padding is bounded and only allowed at the final end.
                if not any(carry):
                    native.require(len(carry)%4==0 and offset==index['bytes'],'native_index_padding_refused');break
                streams+=1;native.require(streams<=4,'native_index_stream_bound')
                decoder=lzma.LZMADecompressor(memlimit=64*1024**2)
            if decoder.needs_input:
                if carry:raw=carry;carry=b''
                else:
                    raw=os.pread(fd,min(65536,index['bytes']-offset),offset);offset+=len(raw)
                native.require(raw,'native_index_truncated')
            else:raw=b''
            chunk=decoder.decompress(raw,max_length=65536);produced+=len(chunk)
            native.require(produced<=index['uncompressed_bytes'],'native_index_expansion_refused')
            digest.update(chunk);pending+=chunk
            while b'\n\n' in pending:
                record,pending=pending.split(b'\n\n',1)
                native.require(0<len(record)<=256*1024,'native_index_record_bound')
                yield record+b'\n'
            native.require(len(pending)<=256*1024,'native_index_record_bound')
        native.require(offset==index['bytes'] and pending in (b'',b'\n')
            and produced==index['uncompressed_bytes'] and digest.hexdigest()==index['uncompressed_sha256'],
            'native_index_terminal_refused')
    except lzma.LZMAError:raise native.ns.NamespaceProofError('native_index_compression_refused') from None


def _package_fields(raw):
    native.require(type(raw) is bytes and 0<len(raw)<=256*1024 and b'\r' not in raw,'native_package_record_bound')
    try:lines=raw.decode('utf-8').splitlines()
    except UnicodeError:raise native.ns.NamespaceProofError('native_package_encoding_refused') from None
    result={};last=None
    for line in lines:
        if line.startswith((' ','\t')):
            native.require(last is not None and last not in ('Package','Version','Architecture','Filename','SHA256','Size'),
                'native_package_identity_continuation_refused');continue
        key,sep,value=line.partition(': ')
        native.require(sep and re.fullmatch('[A-Za-z0-9-]+',key) and key not in result,
            'native_package_field_refused')
        result[key]=value;last=key
    native.require(all(key in result for key in ('Package','Version','Architecture')),'native_package_identity_missing')
    return result


def verify_index_records(directory,index,artifacts,deadline):
    """Every selected exact stanza, including duplicates, must be re-derived."""
    expected={(a['package'],a['version'],a['architecture']):a for a in artifacts}
    native.require(len(expected)==len(artifacts)>0 and all(a['index_sha256']==index['sha256'] for a in artifacts))
    path=Path(directory)/(index['sha256']+'.Packages.xz')
    try:fd=_artifact_fd(path,index['sha256'],index['bytes'],deadline)
    except FileNotFoundError:raise native.ns.NamespaceProofError('native_pinned_index_missing') from None
    try:
        before=native._metadata(os.fstat(fd));found={};count=0
        for raw in _index_records(fd,index,deadline):
            fields=_package_fields(raw);count+=1
            key=tuple(fields[k] for k in ('Package','Version','Architecture'))
            if key not in expected:continue
            native.require(key not in found,'native_duplicate_package_record_refused')
            artifact=expected[key]
            filename=fields.get('Filename','')
            native.require(re.fullmatch(r'pool/[A-Za-z0-9_./+~%\-]+\.deb',filename)
                and '..' not in Path(filename).parts
                and 'https://archive.ubuntu.com/ubuntu/'+filename==artifact['url']
                and fields.get('SHA256')==artifact['artifact_sha256']
                and fields.get('Size')==str(artifact['artifact_bytes'])
                and hashlib.sha256(raw).hexdigest()==artifact['package_record_sha256'],
                'native_package_record_binding_refused')
            found[key]=artifact['artifact_sha256']
        native.require(set(found)==set(expected),'native_exact_package_record_missing')
        native.require(_verify_fd(fd,index['sha256'],index['bytes'],deadline)==before,'native_index_changed_during_parse')
        return {'index_sha256':index['sha256'],'compressed_bytes':index['bytes'],
            'uncompressed_bytes':index['uncompressed_bytes'],'parsed_records':count,'matched_artifacts':sorted(found.values())}
    finally:os.close(fd)


def verify_publisher_metadata(directory,deadline):
    """Pinned snapshot proposal, not owner acceptance or a signed Python build."""
    manifest=native.installation_manifest();inventory=native.require_complete_inventory()
    anchor=manifest['trust_root'];path=Path(directory)/'ubuntu-archive-keyring.gpg'
    fd=_artifact_fd(path,anchor['sha256'],anchor['bytes'],deadline)
    try:key=os.pread(fd,anchor['bytes']+1,0)
    finally:os.close(fd)
    native.require(len(key)==anchor['bytes'] and hashlib.sha256(key).hexdigest()==anchor['sha256'])
    from hosted_synthetic_runtime import read_native
    tool,mode=read_native('/usr/bin/gpgv');native.require(mode==0o555)
    signatures=[];index_results=[]
    with _sealed_metadata(key) as keyfd:
        for suite in ('noble','noble-updates'):
            _check(deadline)
            records=[a for a in inventory['artifacts'].values() if a.get('suite')==suite]
            expected={a['signed_release']['signed_sha256'] for a in records};native.require(len(expected)==1)
            raw=(_EVIDENCE/('oct29-native-'+suite+'-InRelease')).read_bytes()
            native.require(len(raw)<=2*1024**2 and hashlib.sha256(raw).hexdigest() in expected,
                'native_release_snapshot_refused')
            with _sealed_metadata(raw) as releasefd, _empty_directory('buffalo-gpgv-') as home:
                remaining=deadline-time.monotonic();_check(deadline)
                argv=('/usr/bin/gpgv','--homedir',home,'--keyring','/proc/self/fd/'+str(keyfd),
                    '--output','-','--status-fd','2','/proc/self/fd/'+str(releasefd))
                result=subprocess.run(argv,env=boot._ENVIRONMENT,stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,pass_fds=(keyfd,releasefd),timeout=remaining,check=False)
                payload_digests={a['signed_release']['verified_release_sha256'] for a in records}
                native.require(len(payload_digests)==1,'native_release_payload_selection_refused')
                payload=_verified_release_payload(result,anchor['signer'],next(iter(payload_digests)))
                indexes={a['packages_index']['sha256']:a['packages_index'] for a in records}
                native.require(len(indexes)==1,'native_index_selection_refused')
                index=next(iter(indexes.values()));_release_index_binding(payload,index)
                index_results.append(verify_index_records(directory,index,records,deadline))
                signatures.append({'suite':suite,'sha256':hashlib.sha256(raw).hexdigest(),'signer':anchor['signer'],'verified_payload_sha256':hashlib.sha256(payload).hexdigest(),'verified_payload_bytes':len(payload)})
    python=next(a for a in inventory['artifacts'].values() if a['publisher']=='actions/python-versions')
    raw=(_EVIDENCE/'native-python-publisher-hashes.txt').read_bytes()
    native.require(len(raw)==python['publisher_hash_list']['bytes'] and hashlib.sha256(raw).hexdigest()==python['publisher_hash_list']['sha256']
        and [line.split() for line in raw.lower().splitlines() if len(line.split())==2 and line.split()[1]==Path(python['url']).name.encode()]==[[python['artifact_sha256'].encode(),Path(python['url']).name.encode()]],
        'native_python_publisher_digest_refused')
    # Complete artifact bytes are checked BEFORE InstallationLease._create.
    # Each is held/reverified again at materialization, then every member/output.
    artifacts=[]
    for digest,artifact in sorted(inventory['artifacts'].items()):
        fd=_artifact_fd(Path(directory)/digest,digest,artifact['artifact_bytes'],deadline)
        try:artifacts.append({'sha256':digest,'bytes':artifact['artifact_bytes']})
        finally:os.close(fd)
    return {'indexes':index_results,'artifacts':artifacts,'snapshot_policy':manifest['proposed_snapshot_policy'],'signatures':signatures,'keyring_sha256':anchor['sha256'],
        'keyring_protection':'HELD_FULLY_SEALED_READONLY_MEMFD','gpgv_sha256':hashlib.sha256(tool).hexdigest(),
        'ubuntu':'VERIFIED_RELEASE_EXACT_INDEX_EXACT_RECORD_COMPLETE_ARTIFACT_BYTES',
        'python':'PUBLISHER_HTTPS_GITHUB_DIGEST_NOT_SIGNED_BUILD_ATTESTATION','installation_authority':False}


@contextmanager
def _empty_directory(prefix):
    path=Path(tempfile.mkdtemp(prefix=prefix));before=path.lstat();failure=None
    try:yield str(path)
    except BaseException as error:failure=error
    finally:
        try:
            after=path.lstat()
            native.require((after.st_dev,after.st_ino,after.st_uid,after.st_mode)==
                (before.st_dev,before.st_ino,os.geteuid(),stat.S_IFDIR|0o700),'native_temporary_directory_replaced')
            path.rmdir()  # Empty exact inode only; never recursive cleanup.
        except BaseException as cleanup:
            if failure is None:failure=cleanup
            else:failure.add_note('native_temporary_directory_retained')
    if failure is not None:raise failure


class _Held(io.RawIOBase):
    def __init__(self,fd,deadline):self.fd=fd;self.deadline=deadline;self.position=0
    def readable(self):return True
    def readinto(self,buffer):
        _check(self.deadline);raw=os.pread(self.fd,min(len(buffer),65536),self.position)
        buffer[:len(raw)]=raw;self.position+=len(raw);return len(raw)


class _Pipe(io.RawIOBase):
    def __init__(self,pipe,deadline):self.pipe=pipe;self.deadline=deadline
    def readable(self):return True
    def readinto(self,buffer):
        _check(self.deadline)
        with selectors.DefaultSelector() as selected:
            selected.register(self.pipe,selectors.EVENT_READ)
            native.require(selected.select(self.deadline-time.monotonic()),'native_decode_timeout')
        raw=os.read(self.pipe.fileno(),min(len(buffer),65536));buffer[:len(raw)]=raw;return len(raw)


def materialize_artifact(stage,fd,digest,rows,deadline):
    """Exact publisher bytes -> selected normalized members. Never a package install."""
    inventory=native.require_complete_inventory();artifact=inventory['artifacts'].get(digest)
    selected=native.execution_inventory()['rows']
    native.require(artifact is not None and rows and all(r['artifact_sha256']==digest
        and selected.get(r['path'])==r for r in rows),'native_artifact_selection_refused')
    process=None;failure=None;result=None
    try:
        before=_verify_fd(fd,digest,artifact['artifact_bytes'],deadline)
        if artifact['publisher']=='actions/python-versions':
            with io.BufferedReader(_Held(fd,deadline),65536) as held,gzip.GzipFile(fileobj=held,mode='rb') as decoded:
                result=stage.materialize_tar(decoded,rows,deadline=deadline)
        else:
            from hosted_synthetic_runtime import read_native
            _,mode=read_native('/usr/bin/dpkg-deb');native.require(mode==0o555)
            _check(deadline)
            # stderr stays in a bounded anonymous stream read after exit; no package code executes.
            process=subprocess.Popen(('/usr/bin/dpkg-deb','--fsys-tarfile','/proc/self/fd/'+str(fd)),
                stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=boot._ENVIRONMENT,
                pass_fds=(fd,),start_new_session=True,bufsize=0)
            with io.BufferedReader(_Pipe(process.stdout,deadline),65536) as decoded:
                result=stage.materialize_tar(decoded,rows,deadline=deadline)
                while decoded.read(65536):_check(deadline)  # Drain archive padding; bounded wall time.
            _check(deadline)
            native.require(process.wait(timeout=deadline-time.monotonic())==0,'native_decode_refused')
            native.require(len(process.stderr.read(4097))<=4096,'native_decode_output_bound')
        native.require(_verify_fd(fd,digest,artifact['artifact_bytes'],deadline)==before,'native_artifact_changed_during_materialization')
    except BaseException as error:failure=error
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    # Owned unprivileged process group created by this call only.
                    os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
            except BaseException as error:
                if failure is None:failure=error
                else:failure.add_note('native_decode_reap_unverified')
            finally:
                for pipe in (process.stdout,process.stderr):
                    try:
                        if pipe is not None:pipe.close()
                    except BaseException as cleanup:
                        if failure is None:failure=cleanup
                        else:failure.add_note('native_decode_descriptor_unverified')
        if failure is not None and not stage.closed:
            try:stage.close()
            except BaseException:failure.add_note('native_stage_cleanup_unverified')
    if failure is not None:raise failure
    return result


def _select_and_execute(lease,binding,arguments,preflight):
    from hosted_synthetic_quarantine import reserved_ids
    reserved=reserved_ids();nonce=str(uuid.uuid4());request=None;consumer=None;collisions=0
    for number in range(900,1000):
        preflight.verify(preflight.source,binding.commit)
        if any(number in pair for pair in reserved):continue
        candidate=identity.DedicatedIdentity(nonce,number,number,os.geteuid(),os.getegid())
        try:identity.inspect_account(candidate,present=False)
        except boot.BootstrapRefusal as error:
            if str(error) in ('dedicated_identity_collision','dedicated_local_identity_collision','dedicated_subordinate_identity_collision'):continue
            raise
        if identity.identity_processes(candidate):continue
        if consumer is None:
            request=native.view_request(binding,lease.root,deadline=preflight.deadline,installation=lease)
            consumer=measurement.reserve_consumer(lease,request,binding,'buffalo-hosted-'+str(uuid.uuid4())+'.service',
                role='measurement',uid=os.geteuid(),gid=os.getegid())
        try:
            repository,event,ref,commit=arguments
            return identity.run_provisioned_candidate(candidate,source=preflight.source,python=native.PYTHON,
                repository=repository,event=event,ref=ref,commit=commit,diagnostic='postgres-version',
                ownership_deadline=preflight.deadline,preflight=preflight,native_measurement=consumer)
        except identity.CandidateOwnershipCollision:
            collisions+=1;native.require(collisions<3,'identity_selection_collision_bound')
    raise boot.BootstrapRefusal('dedicated_identity_range_unavailable')


def _run(arguments,directory):
    require_installation_scope()
    from hosted_synthetic_capability import _require_clean_supervisor
    from hosted_synthetic_source import bind_hosted_source
    _require_clean_supervisor();native.require(len(arguments)==4)
    source=str(Path(__file__).resolve().parents[2]);deadline=time.monotonic()+60
    repository,event,ref,commit=arguments
    binding=bind_hosted_source(Path(source),repository=repository,event=event,ref=ref,sha=commit,operation_deadline=deadline)
    preflight=identity.SelectionPreflight(source,commit,native.PYTHON,deadline)
    started=time.monotonic();provenance=verify_publisher_metadata(directory,deadline);preflight.record('PUBLISHER_SNAPSHOT',started)
    identity.inspect_account_platform();install.inspect_import_tools(deadline)
    lease=None;failure=None;result=None
    try:
        started=time.monotonic()
        lease=install.InstallationLease._create(uuid.uuid4().hex,deadline=deadline,binding=binding)
        rows=lease.rows;artifacts=native.require_complete_inventory()['artifacts']
        with _empty_directory('buffalo-native-stage-') as parent:
            for digest in sorted(artifacts):
                _check(deadline);artifact=artifacts[digest]
                selected=[r for r in rows.values() if r['artifact_sha256']==digest]
                fd=_artifact_fd(Path(directory)/digest,digest,artifact['artifact_bytes'],deadline);stage=None
                error=None
                try:
                    stage=native.Stage(Path(parent)/digest)
                    materialize_artifact(stage,fd,digest,selected,deadline)
                    lease.import_stage(stage,selected)
                except BaseException as caught:error=caught
                finally:
                    os.close(fd)
                    if stage is not None and not stage.closed:
                        try:stage.close()
                        except BaseException as cleanup:
                            if error is None:error=cleanup
                            else:error.add_note('native_stage_cleanup_unverified')
                if error is not None:raise error
        lease.finish();preflight.record('NATIVE_MATERIALIZATION',started)
        result=_select_and_execute(lease,binding,arguments,preflight)
    except BaseException as error:
        failure=error
        if lease is None:lease=getattr(error,'native_installation_lease',None)
    if lease is not None:
        try:lease.close()
        except BaseException as cleanup:
            if failure is None:failure=cleanup
            else:failure.add_note('native_installation_cleanup_unverified')
    if failure is not None:raise failure
    result.update(native_installation_removed=lease.closed,native_provenance=provenance,
        native_manifest=native.INSTALLATION_MANIFEST_SHA256)
    return result


def installation_measurement_entry(arguments,artifact_directory):
    """The precise future decision entry; current gate refuses before every effect."""
    _installation_decision_gate()
    token=_SCOPE.set(_TOKEN)
    try:return _run(arguments,artifact_directory)
    finally:_SCOPE.reset(token)
