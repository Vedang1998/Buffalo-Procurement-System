import hashlib,json,lzma,pathlib,re,sys,time
D=pathlib.Path('/tmp/buffalo-native-packages-readonly')
report=json.loads(pathlib.Path(sys.argv[1]).read_text());assert report['status']=='TARGET_OBSERVATION_ONLY'
claims={name.split(':',1)[0]:row for name,row in report['package_claims'].items()}
observed_pg=claims.pop('postgresql-16',None)
for name in ('postgresql-16','postgresql-client-16','libpq5'):claims[name]={'version':'16.15-0ubuntu0.24.04.1','architecture':'amd64'}
expected={(name,row['version'],row['architecture']) for name,row in claims.items()};missing=set(expected);found={};coverage=[]
indexes=json.loads((D/'index-evidence.json').read_text())
order=sorted(indexes['indexes'],key=lambda r:({'main':0,'universe':1}[r['component']],{'noble-updates':0,'noble-security':1,'noble':2}[r['suite']]))
def records(path,max_output=64*1024**2):
 decompressor=lzma.LZMADecompressor(memlimit=64*1024**2);pending=b'';produced=0;started=time.monotonic()
 with path.open('rb') as source:
  while not decompressor.eof:
   data=source.read(65536) if decompressor.needs_input else b''
   if decompressor.needs_input and not data:raise RuntimeError('truncated compressed index')
   out=decompressor.decompress(data,max_length=65536);produced+=len(out)
   if produced>max_output:raise RuntimeError('decompressed_64MiB_coverage_bound')
   assert time.monotonic()-started<60
   pending+=out
   while b'\n\n' in pending:
    record,pending=pending.split(b'\n\n',1);assert len(record)<=256*1024
    yield record+b'\n'
   assert len(pending)<=256*1024
  assert not decompressor.unused_data and source.read(1)==b'' and pending in (b'',b'\n')
def parse(raw):
 fields={}
 for line in raw.decode('utf-8').splitlines():
  if not line or line[0].isspace():continue
  key,sep,value=line.partition(': ')
  assert sep and key not in fields;fields[key]=value
 return fields
for index in order:
 if not missing:break
 path=D/index['path'];assert hashlib.sha256(path.read_bytes()).hexdigest()==index['sha256'] and path.stat().st_size==index['bytes']
 seen=0;state='COMPLETE'
 try:
  for raw in records(path):
   seen+=len(raw)+1;row=parse(raw)
   identity=(row.get('Package'),row.get('Version'),row.get('Architecture'))
   if identity not in missing:continue
   assert re.fullmatch('[0-9a-f]{64}',row['SHA256']) and row['Size'].isdigit() and int(row['Size'])<=80*1024**2
   assert re.fullmatch(r'pool/[A-Za-z0-9_./+~%-]+\.deb',row['Filename']) and '..' not in pathlib.PurePosixPath(row['Filename']).parts
   key=identity[0];found[key]={'package':identity[0],'version':identity[1],'architecture':identity[2],
      'artifact_sha256':row['SHA256'],'artifact_bytes':int(row['Size']),'url':'https://archive.ubuntu.com/ubuntu/'+row['Filename'],
      'index_sha256':index['sha256'],'suite':index['suite'],'component':index['component'],
      'package_record_sha256':hashlib.sha256(raw).hexdigest(),'Depends':row.get('Depends'),'Pre-Depends':row.get('Pre-Depends')}
   (D/(key+'.Packages-record')).write_bytes(raw);missing.remove(identity)
   if not missing:state='STOPPED_AFTER_ALL_EXACT_RECORDS_FOUND';break
 except RuntimeError as error:state=str(error)
 coverage.append({'index_sha256':index['sha256'],'parsed_bytes':seen,'state':state})
 print(index['suite'],index['component'],'missing',len(missing),'state',state,flush=True)
result={'scope':'EXACT_VERSION_PUBLISHER_RECORDS_NOT_FULL_PAYLOAD_OR_TARGET_FILE_MATCH','target_commit':report['commit'],'target_tree':report['tree'],'target_image':{'os':report['image_os'],'version':report['image_version']},'observed_postgres_package_not_selected':observed_pg,'approved_postgres_version':'16.15-0ubuntu0.24.04.1','packages':found,'missing_exact_versions':[{'package':n,'version':v,'architecture':a} for n,v,a in sorted(missing)],'coverage':coverage,'source':'signed Release -> digest-bound full compressed Packages -> exact package paragraph','not_performed':['payload download','package install','artifact execution','privilege','repository mutation']}
(D/'exact-package-records.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'matched':len(found),'missing':result['missing_exact_versions']}))
