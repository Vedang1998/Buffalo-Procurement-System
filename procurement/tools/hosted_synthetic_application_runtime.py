"""Current Git application + original locked wheels in the existing sealed root.

A credential-free import component, not an application server, database client,
browser runtime pin or release grant. Existing frozen OCI/Nix identities remain
unchanged; actual current native and application bytes have their own identity.
"""
from dataclasses import asdict
import hashlib
import os
from pathlib import Path
import sys
import sysconfig

import hosted_synthetic_namespace as ns
import hosted_synthetic_runtime as runtime
import hosted_synthetic_source as source
import hosted_synthetic_wheels as wheels

INPUT_DIRECTORIES=('procurement/src/','procurement/config/','procurement/db/')
INPUT_FILES=('procurement/seed/variant_aliases.csv',
    'procurement/review/phase4_identity_manifest_corrected.csv',
    'procurement/review/phase4_terminal_disposition_manifest.csv')
_TIMEZONES=('/usr/share/zoneinfo/America/New_York','/usr/share/zoneinfo/UTC')


def application_files(root,binding):
    ns._require(source._oid(source._git(root,'rev-parse','HEAD'))==binding.commit
                and source._oid(source._git(root,'rev-parse','HEAD^{tree}'))==binding.tree)
    result={}
    for relative,mode,blob in source._tree_records(root,binding.tree):
        if not (relative.startswith(INPUT_DIRECTORIES) or relative in INPUT_FILES):continue
        ns._require(mode==b'100644' and '__pycache__' not in Path(relative).parts
                    and not relative.endswith(('.pyc','.pth','.egg-link')))
        raw=source._file_bytes(root,relative,mode)
        ns._require(len(raw)<=2*1024**2 and hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest().encode()==blob)
        result[relative]=raw
    ns._require(set(INPUT_FILES).issubset(result)
                and all(any(p.startswith(d) for p in result) for d in INPUT_DIRECTORIES))
    ns._require(source._oid(source._git(root,'rev-parse','HEAD'))==binding.commit
                and source._oid(source._git(root,'rev-parse','HEAD^{tree}'))==binding.tree)
    return result


def _finish_capture(base,result,error):
    try:base.close()
    except BaseException as cleanup:
        if error is None:error=cleanup
        else:error.add_note('application_base_bundle_cleanup_failed')
    if error is not None:
        if result is not None:
            try:result.close()
            except BaseException:error.add_note('application_result_bundle_cleanup_failed')
        raise error
    ns._require(result is not None)
    return result


def capture_application(generation,root,binding,cache):
    # Restoring the caller's signal mask may deliver cancellation after the
    # operation returned an owned bundle. Until this wrapper returns, ownership
    # remains here and must be disposed even on that restoration failure.
    result=None
    @source._resource_operation
    def guarded():
        nonlocal result
        result=_capture_application(generation,root,binding,cache)
        return result
    try:return guarded()
    except BaseException as error:
        if result is not None:
            try:result.close()
            except BaseException:error.add_note('application_cancelled_bundle_cleanup_failed')
        raise


def _capture_application(generation,root,binding,cache):
    ns._require(sys.version_info[:2]==(3,13) and not sysconfig.get_config_var('Py_GIL_DISABLED'))
    root=Path(root).resolve(strict=True)
    lock=source._file_bytes(root,'uv.lock',b'100644')
    ns._require(hashlib.sha256(lock).hexdigest()==dict(binding.files).get('uv.lock'))
    specs=wheels.locked_wheels(lock)
    payload,owners=wheels.capture_wheels(specs,cache)
    external,elfs=wheels.elf_closure(payload,owners)
    app=application_files(root,binding)
    base=runtime.capture_runtime(generation,root,binding)
    result=None;error=None
    try:
        entries={e.path:e for e in runtime._entries(base) if e.kind=='F' and e.path!=runtime._IDENTITY_PATH}
        native=dict(base.native_files);source_files=dict(base.source_files)
        def add(path,data,mode,provenance,inventory):
            ns._require(path not in entries)
            digest=hashlib.sha256(data).hexdigest();inventory[path]=digest
            entries[path]=ns.backend._BrowserRuntimeBundleEntry(path=path,kind='F',mode=mode,user_id=0,group_id=0,
                provenance=provenance,content=data)
        for path,data in app.items():
            add(runtime.PROJECTED_ROOT+'/'+path,data,0o444,'audit-source:'+hashlib.sha256(data).hexdigest(),source_files)
        for path,data in payload.items():
            # Archive 0664 metadata is not installed as writable authority:
            # every projected member is read-only, ELF executable where needed.
            add(wheels.SITE+'/'+path,data,0o555 if path in elfs else 0o444,'dependency-layer:'+owners[path].sha256,native)
        roots=runtime._library_roots()
        pending=[runtime._resolve_library(name,roots) for name in external]
        while pending:
            path,data,mode=pending.pop()
            if path in entries:
                ns._require(entries[path].content==data);continue
            add(path,data,mode,'native-runtime:'+hashlib.sha256(data).hexdigest(),native)
            image=runtime.parse_elf(data)
            if image['interpreter']:
                p=image['interpreter'];pending.append((p,*runtime.read_native(p)))
            pending.extend(runtime._resolve_library(name,roots) for name in image['needed'])
        for path in _TIMEZONES:
            data,mode=runtime.read_native(path)
            ns._require(data.startswith(b'TZif'))
            add(path,data,0o444,'native-runtime:'+hashlib.sha256(data).hexdigest(),native)
        application={'site':wheels.SITE,'uv_lock_sha256':hashlib.sha256(lock).hexdigest(),
            'wheels':[asdict(s) for s in specs], 'application_files':len(app),
            'dependency_files':len(payload),'dependency_regular_bytes':sum(map(len,payload.values())),
            'native_wheel_elf_files':list(elfs),'timezones':list(_TIMEZONES),
            'scope':'IMPORT_COMPONENT_ONLY','browser_closure':'UNSEALED','credential_release':'WITHHELD'}
        identity=runtime._identity_bytes(generation,base.executable,source_files,native,binding.commit,binding.tree,
            policy=runtime.APPLICATION_POLICY,application=application)
        add(runtime._IDENTITY_PATH,identity,0o444,'audit-source:'+hashlib.sha256(identity).hexdigest(),{})
        complete=ns.backend._complete_browser_runtime_bundle_parents(tuple(entries.values()))
        ns._require(len(complete)<=runtime.MAX_ENTRIES and sum(len(e.content) for e in complete)<=runtime.MAX_BYTES)
        bundle=ns.backend._seal_browser_runtime_bundle(complete)
        result=runtime.RuntimeBundle(bundle,generation,base.executable,source_files,native,binding.commit,binding.tree,
            runtime.APPLICATION_POLICY,application)
        result._owner=result;result._token=runtime._TOKEN
        runtime._entries(result)
    except BaseException as failure:error=failure
    return _finish_capture(base,result,error)
