"""Bounded read-only target observations, never publisher trust or provisioning.

No apt, ldd, artifact execution, privilege, source mutation or package selection.
A new runner's dpkg claims do not prove equivalence to a previously measured VM.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

if __package__ is None:sys.path.insert(0,str(Path(__file__).resolve().parent))
import hosted_synthetic_native_inputs as native
import hosted_synthetic_runtime as runtime
import hosted_synthetic_bootstrap as boot
import hosted_synthetic_source as source

ROOTS=('/usr/lib/x86_64-linux-gnu','/lib/x86_64-linux-gnu','/usr/lib64','/lib64','/usr/lib','/lib')
PACKAGE=re.compile(r'[a-z0-9][a-z0-9+.-]*(?::amd64)?\Z')
VERSION=re.compile(r'[A-Za-z0-9.+:~_-]{1,128}\Z')


def package_rows(raw,expected):
    native.require(type(raw) is bytes and len(raw)<=32768,'native_package_output_refused')
    try:lines=raw.decode('ascii').splitlines()
    except UnicodeDecodeError:raise native.ns.NamespaceProofError('native_package_output_refused') from None
    rows={}
    for line in lines:
        fields=line.split('\t')
        native.require(len(fields)==4 and PACKAGE.fullmatch(fields[0]) and VERSION.fullmatch(fields[1])
            and fields[2] in ('amd64','all') and fields[3]=='ii ' and fields[0] not in rows,
            'native_package_output_refused')
        rows[fields[0]]={'version':fields[1],'architecture':fields[2]}
    native.require(set(rows)==set(expected),'native_package_population_refused');return rows


def package_owner(raw,path):
    native.require(type(raw) is bytes and len(raw)<=8192)
    try:line=raw.decode('ascii')
    except UnicodeDecodeError:raise native.ns.NamespaceProofError('native_package_owner_refused') from None
    native.require(line.endswith('\n') and line.count('\n')==1,'native_package_owner_ambiguous')
    owner,sep,name=line[:-1].partition(': ')
    native.require(sep and PACKAGE.fullmatch(owner) and name==path,'native_package_owner_ambiguous')
    return owner


def observe_file(path,deadline):
    native.require(time.monotonic()<deadline,'native_observation_deadline')
    requested=Path(path);resolved=requested.resolve(strict=True)
    native.require(str(resolved).startswith(('/usr/','/lib/','/lib64/','/bin/','/opt/'))
        and re.fullmatch(r'/[A-Za-z0-9_./+\-]+',str(resolved)),'native_observation_path_refused')
    before=resolved.lstat()
    native.require(stat.S_ISREG(before.st_mode) and 0<=before.st_size<=83886080,'native_observation_file_bound')
    fd=os.open(resolved,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        native.require(native._metadata(before)==native._metadata(os.fstat(fd)))
        digest=hashlib.sha256();data=[];offset=0
        while offset<before.st_size:
            native.require(time.monotonic()<deadline,'native_observation_deadline')
            chunk=os.pread(fd,min(1024**2,before.st_size-offset),offset)
            native.require(bool(chunk));digest.update(chunk);data.append(chunk);offset+=len(chunk)
        native.require(native._metadata(before)==native._metadata(os.fstat(fd))==native._metadata(resolved.lstat())
            and requested.resolve(strict=True)==resolved,'native_observation_file_drift')
    finally:os.close(fd)
    raw=b''.join(data)
    return {'path':path,'physical_path':str(resolved),'bytes':len(raw),'sha256':digest.hexdigest(),
        'uid':before.st_uid,'gid':before.st_gid,'mode':format(stat.S_IMODE(before.st_mode),'04o')},raw


def observe(binding, *, image_os='',image_version=''):
    native.require(os.geteuid()>0,'native_observation_root_refused')
    native.require(all(type(x) is str and re.fullmatch(r'[A-Za-z0-9_.-]{0,80}',x) for x in (image_os,image_version)))
    deadline=time.monotonic()+60;value=native.inventory();rows={};unresolved=[];graph={};total=0
    from hosted_synthetic_postgres_runtime import _LOCALE
    pending=[native.PYTHON,native.RUNPATH+'/libpython3.13.so.1.0','/usr/lib/postgresql/16/bin/postgres',
             '/bin/sh','/usr/bin/locale',*('/usr/lib/locale/C.utf8/'+p for p in _LOCALE)]
    pending.extend(value['required_unresolved_timezone_files'])
    names=list(value['required_unresolved_external_sonames']);resolved_names={}
    while names or pending:
        native.require(time.monotonic()<deadline and len(rows)<3000,'native_observation_bound')
        if names:
            name=names.pop()
            if name in resolved_names:continue
            native.require(re.fullmatch(r'[A-Za-z0-9_.+\-]+',name))
            candidates=[]
            for base in ROOTS:
                p=Path(base)/name
                if p.exists():candidates.append(str(p.resolve(strict=True)))
            candidates=sorted(set(candidates))
            if len(candidates)!=1:
                unresolved.append({'soname':name,'reason':'MISSING_OR_AMBIGUOUS','candidates':candidates});resolved_names[name]=None;continue
            resolved_names[name]=candidates[0];pending.append(candidates[0]);continue
        path=pending.pop()
        if path in rows:continue
        try:row,raw=observe_file(path,deadline)
        except (OSError,native.ns.NamespaceProofError) as error:
            unresolved.append({'path':path,'reason':'READ_OR_IDENTITY_REFUSED','error_type':type(error).__name__});continue
        total+=len(raw);native.require(total<=134217728,'native_observation_byte_bound')
        rows[path]=row
        if raw.startswith(b'\x7fELF'):
            try:
                elf=native.parse_selected_elf(path,raw) if path.startswith('/opt/') else runtime.parse_elf(raw)
            except native.ns.NamespaceProofError:
                unresolved.append({'path':path,'reason':'STATIC_ELF_POLICY_REFUSED'});continue
            graph[path]=elf;names.extend(elf['needed'])
            if elf['interpreter']:pending.append(elf['interpreter'])
        del raw
    packages=set()
    for path,row in rows.items():
        owners=[]
        if path.startswith('/opt/'):continue
        for selected in sorted(set((path,row['physical_path']))):
            code,raw=boot._collect_native_client(('/usr/bin/dpkg-query','--search','--',selected),cwd='/',operation_deadline=deadline)
            if code!=0:
                unresolved.append({'path':selected,'reason':'PACKAGE_OWNER_MISSING'});continue
            try:owners.append(package_owner(raw,selected))
            except native.ns.NamespaceProofError:
                unresolved.append({'path':selected,'reason':'PACKAGE_OWNER_AMBIGUOUS'});continue
        if len(set(owners))==1:row['package']=owners[0];packages.add(owners[0])
        elif owners:unresolved.append({'path':path,'reason':'PACKAGE_ALIAS_OWNER_DISAGREEMENT'})
    claims={}
    if packages:
        code,raw=boot._collect_native_client(('/usr/bin/dpkg-query','--show',
            '--showformat=${binary:Package}\t${Version}\t${Architecture}\t${db:Status-Abbrev}\n','--',*sorted(packages)),
            cwd='/',operation_deadline=deadline)
        native.require(code==0,'native_package_query_refused');claims=package_rows(raw,packages)
    expected={r['path']:r['sha256'] for r in value['rows']}
    for path,row in rows.items():
        row['selected_artifact_file_match']=None if path not in expected else row['sha256']==expected[path]
    native.require(time.monotonic()<deadline,'native_observation_deadline')
    return {'policy':'BUFFALO_TARGET_NATIVE_OBSERVATION_V1','commit':binding.commit,'tree':binding.tree,
        'manifest_sha256':native.DIGESTS[0],'image_os':image_os,'image_version':image_version,
        'status':'TARGET_OBSERVATION_ONLY','rows':list(rows.values()),'elf_graph':graph,'resolved_sonames':resolved_names,
        'package_claims':claims,'unresolved':unresolved,'bytes_read':total,'elapsed_seconds':60-(deadline-time.monotonic()),
        'publisher_authentication':'NOT_ESTABLISHED_BY_DPKG_OR_LOCAL_HASH','earlier_runner_equality':'NOT_PROVEN',
        'version_selection_authority':False,'execution_authority':False,'account_mutations':0,
        'capture_memory_readiness':'NOT_MEASURED_NATIVE_INPUT_PREREQUISITE','privileged_activation':'DISABLED'}


def main(argv):
    native.require(len(argv)==1)
    root=Path(__file__).resolve().parents[2]
    binding=source.bind_hosted_source(root,repository=source.REPOSITORY,event='push',ref=source.REF,sha=argv[0])
    report=observe(binding,image_os=os.environ.get('IMAGE_OS',''),image_version=os.environ.get('IMAGE_VERSION',''))
    native.require(source.bind_hosted_source(root,repository=source.REPOSITORY,event='push',ref=source.REF,sha=argv[0])==binding)
    raw=json.dumps(report,sort_keys=True,separators=(',',':'),allow_nan=False)
    native.require(len(raw)<=65536,'native_observation_output_bound');print(raw)
    return 0

if __name__=='__main__':raise SystemExit(main(sys.argv[1:]))
