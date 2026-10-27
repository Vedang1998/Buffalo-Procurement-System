"""Offline, byte-preserving projection of the unchanged 23 locked wheels.

No installer, build hook, relocation, download or installed RECORD trust. The
source-bound uv.lock authenticates each original archive before ZIP parsing.
"""
from dataclasses import dataclass
import csv
from email.parser import BytesParser
from email.policy import compat32
import hashlib
import io
from pathlib import Path
import re
import stat
import tomllib
from urllib.parse import urlsplit
import zipfile

import hosted_synthetic_namespace as ns
import hosted_synthetic_runtime as runtime

SITE = '/buffalo-runtime/site-packages'
MAX_ARCHIVE = 8 * 1024**2
MAX_EXPANDED = 34 * 1024**2
MAX_FILES = 1000
_NATIVE_TAGS = {
    'argon2-cffi-bindings': 'cp310-abi3-manylinux_2_26_x86_64.manylinux_2_28_x86_64',
    'cffi': 'cp313-cp313-manylinux2014_x86_64.manylinux_2_17_x86_64',
    'psycopg-binary': 'cp313-cp313-manylinux2014_x86_64.manylinux_2_17_x86_64',
    'pydantic-core': 'cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64',
}
_EXTERNAL = frozenset(('ld-linux-x86-64.so.2','libc.so.6','libdl.so.2','libgcc_s.so.1',
    'libm.so.6','libpthread.so.0','libresolv.so.2','librt.so.1','libz.so.1'))
_PRIVATE = 'psycopg_binary.libs/'


@dataclass(frozen=True)
class LockedWheel:
    name: str
    version: str
    dist_info: str
    filename: str
    sha256: str
    size: int
    tags: tuple[str, ...]


def locked_wheels(raw):
    """The existing tuple pins select one compatible artifact per distribution."""
    ns._require(type(raw) is bytes and len(raw) <= 2*1024**2)
    lock = tomllib.loads(raw.decode('utf-8'))
    result=[]
    for name, version, dist in ns.backend._BROWSER_DEPENDENCY_DISTRIBUTIONS:
        packages=[p for p in lock['package'] if p['name']==name]
        ns._require(len(packages)==1 and packages[0]['version']==version
                    and packages[0]['source']=={'registry':'https://pypi.org/simple'})
        tag=_NATIVE_TAGS.get(name,'py3-none-any')
        filename=dist.removesuffix('.dist-info')+'-'+tag+'.whl'
        matches=[w for w in packages[0]['wheels'] if urlsplit(w['url']).path.rsplit('/',1)[-1]==filename]
        ns._require(len(matches)==1)
        item=matches[0];url=urlsplit(item['url'])
        ns._require(url.scheme=='https' and url.netloc=='files.pythonhosted.org'
                    and not url.query and not url.fragment and re.fullmatch(r'/packages/[A-Za-z0-9_./+-]+',url.path)
                    and re.fullmatch('sha256:[0-9a-f]{64}',item['hash'])
                    and type(item['size']) is int and 0 < item['size'] <= MAX_ARCHIVE)
        python,abi,platform=tag.split('-')
        tags=tuple(sorted(f'{python}-{abi}-{value}' for value in platform.split('.')))
        result.append(LockedWheel(name,version,dist,filename,item['hash'][7:],item['size'],tags))
    return tuple(result)


def _path(value):
    path=ns.backend._canonical_browser_dependency_relative_path(value)
    parts=path.split('/')
    ns._require(not any(p.endswith(('.data','.pth','.egg-link','.pyc')) or p=='__pycache__'
        or p in ('sitecustomize.py','usercustomize.py','INSTALLER','REQUESTED','direct_url.json') for p in parts))
    return path


def verify_wheel(spec, raw):
    """Return authenticated regular bytes only; nothing is extracted to disk."""
    ns._require(type(spec) is LockedWheel and type(raw) is bytes
                and len(raw)==spec.size<=MAX_ARCHIVE and hashlib.sha256(raw).hexdigest()==spec.sha256)
    regular={};folded=set();directories=set();total=0
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members=archive.infolist()
            ns._require(0 < len(members) <= MAX_FILES and not archive.comment)
            for item in members:
                ns._require(item.filename==item.orig_filename and not item.flag_bits&1
                            and item.compress_type in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED))
                is_dir=item.is_dir();path=_path(item.filename[:-1] if is_dir else item.filename)
                ns._require(path.casefold() not in folded);folded.add(path.casefold())
                mode=item.external_attr>>16
                ns._require(item.create_system==3 and stat.S_IFMT(mode) in (0,stat.S_IFDIR if is_dir else stat.S_IFREG)
                            and stat.S_IMODE(mode) in ((0o755,) if is_dir else (0o600,0o644,0o664,0o755)))
                if is_dir:
                    ns._require(item.file_size==0);directories.add(path);continue
                ns._require(0 <= item.file_size <= MAX_EXPANDED)
                total+=item.file_size;ns._require(total<=MAX_EXPANDED)
                data=archive.read(item)
                ns._require(len(data)==item.file_size);regular[path]=data
    except (zipfile.BadZipFile,NotImplementedError,RuntimeError,ValueError,UnicodeError,EOFError):
        raise ns.NamespaceProofError('wheel_archive_refused') from None
    for path in (*regular,*directories):
        ns._require(all(str(parent) not in regular for parent in Path(path).parents if str(parent)!='.'))
    infos={path.split('/')[0] for path in regular if path.split('/')[0].endswith('.dist-info')}
    ns._require(infos=={spec.dist_info})
    prefix=spec.dist_info+'/'
    metadata,record,wheel=(prefix+name for name in ('METADATA','RECORD','WHEEL'))
    ns._require({metadata,record,wheel}.issubset(regular))
    ns.backend._parse_browser_dependency_metadata(regular[metadata],expected_name=spec.name,expected_version=spec.version)
    ns._require(len(regular[wheel])<=8192 and len(regular[record])<=1024**2)
    headers=BytesParser(policy=compat32).parsebytes(regular[wheel])
    ns._require(not headers.defects and headers.get_payload() in ('','\n')
        and headers.get_all('Wheel-Version')==['1.0']
        and headers.get_all('Root-Is-Purelib')==[('true' if spec.tags==('py3-none-any',) else 'false')]
        and tuple(sorted(headers.get_all('Tag',[])))==spec.tags
        and not headers.get_all('Build'))
    seen=set()
    try:
        for row in csv.reader(io.StringIO(regular[record].decode('utf-8'),newline=''),strict=True):
            ns._require(len(row)==3)
            path=_path(row[0]);ns._require(path not in seen and path in regular);seen.add(path)
            if path==record:ns._require(row[1:]==['',''])
            else:
                ns._require(ns.backend._decode_browser_dependency_record_hash(row[1])==hashlib.sha256(regular[path]).digest()
                    and re.fullmatch(r'(?:0|[1-9][0-9]{0,9})',row[2]) and int(row[2])==len(regular[path]))
    except (UnicodeError,csv.Error):raise ns.NamespaceProofError('wheel_record_refused') from None
    ns._require(seen==set(regular))
    return regular


def capture_wheels(specs, cache):
    from hosted_synthetic_source import _held_source_file
    ns._require(tuple((s.name,s.version,s.dist_info) for s in specs)==ns.backend._BROWSER_DEPENDENCY_DISTRIBUTIONS)
    payload={};owners={};total=0;folded=set()
    root=Path(cache).resolve(strict=True)
    for spec in specs:
        with _held_source_file(root,spec.filename) as fd:
            import os
            before=os.fstat(fd)
            ns._require(stat.S_ISREG(before.st_mode) and before.st_nlink==1
                        and not before.st_mode&0o6022 and before.st_size==spec.size)
            raw=os.pread(fd,spec.size+1,0)
            files=verify_wheel(spec,raw)
            ns._require(runtime._metadata(before)==runtime._metadata(os.fstat(fd)))
        for path,data in files.items():
            ns._require(path.casefold() not in folded);folded.add(path.casefold())
            total+=len(data);ns._require(total<=MAX_EXPANDED and len(payload)<MAX_FILES)
            payload[path]=data;owners[path]=spec
    return payload,owners


def _rpath(path):
    if path in ('psycopg_binary/_psycopg.cpython-313-x86_64-linux-gnu.so',
                'psycopg_binary/pq.cpython-313-x86_64-linux-gnu.so'):
        return '$ORIGIN/../psycopg_binary.libs'
    if path in (_PRIVATE+'libpq-2be5f14a.so.5.18',_PRIVATE+'libldap-fa0f4823.so.2.0.200'):
        return '$ORIGIN'
    return None


def elf_closure(payload, owners):
    """Resolve exact private RPATH ancestry; never substitute a host private lib.

    No RUNPATH, LD_LIBRARY_PATH, loader cache, relocation, or ELF rewriting.
    Each authenticated private SONAME has one exact member in the same wheel.
    """
    elfs={};private={}
    for path,data in payload.items():
        if not data.startswith(b'\x7fELF'):continue
        allowed=_rpath(path)
        if allowed or path.startswith(_PRIVATE):
            ns._require(owners[path].name=='psycopg-binary' and owners[path].version=='3.3.4')
        image=runtime.parse_elf(data,expected_rpath=allowed)
        ns._require(image['interpreter'] is None)
        elfs[path]=image
        if path.startswith(_PRIVATE):
            name=image['soname']
            ns._require(str(Path(path).parent)==_PRIVATE.rstrip('/')
                        and name==Path(path).name and name not in private and name not in _EXTERNAL)
            private[name]=path
    pending=[(path,()) for path in elfs if not path.startswith(_PRIVATE)]
    reached=set();states=set();external=set()
    while pending:
        path,ancestors=pending.pop()
        ns._require((path,ancestors) not in states or path in reached)
        if (path,ancestors) in states:continue
        states.add((path,ancestors));ns._require(len(states)<=128)
        reached.add(path)
        # Both approved forms normalize to this one same-wheel directory. A
        # child lacking its own RPATH inherits its loader ancestry (DT_RPATH).
        own=(_PRIVATE.rstrip('/'),) if _rpath(path) else ()
        search=tuple(dict.fromkeys((*own,*ancestors)))
        for name in elfs[path]['needed']:
            if name in private:
                child=private[name]
                ns._require(owners[path]==owners[child] and _PRIVATE.rstrip('/') in search)
                pending.append((child,search))
            else:
                ns._require(name in _EXTERNAL)
                external.add(name)
    ns._require(reached==set(elfs))
    return tuple(sorted(external)),tuple(sorted(elfs))
