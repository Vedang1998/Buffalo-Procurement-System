import sys,os,tempfile,pathlib,time,json,resource,tracemalloc
sys.path.insert(0,'/workspace/Buffalo-Procurement-System/procurement/tools')
import hosted_synthetic_native_inputs as native
started=time.monotonic();before=set(os.listdir('/proc/self/fd'));stage=None;fd=None
tracemalloc.start()
try:
 with tempfile.TemporaryDirectory(prefix='buffalo-authenticated-stage-') as parent:
  stage=native.Stage(pathlib.Path(parent)/'stage')
  fd=os.open('/tmp/buffalo-native-manifest-readonly/python-3.13.5-linux-24.04-x64.tar.gz',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
  result=stage.materialize_python_artifact(fd)
  parsed=[]
  for row in native.inventory()['rows']:
   if row['artifact_sha256']==result['artifact_sha256'] and row.get('elf'):
    elf=native.parse_selected_elf(row['path'],(stage.path/row['path'].lstrip('/')).read_bytes())
    assert elf['needed']==tuple(row['elf']['needed']) and elf['interpreter']==row['elf']['interpreter'] and elf['soname']==row['elf']['soname']
    parsed.append(row['path'])
  result['exact_authenticated_elf_objects_parsed']=len(parsed)
  result.update(traced_peak_bytes=tracemalloc.get_traced_memory()[1],rss_peak_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,seconds=time.monotonic()-started)
  stage.close();stage=None
finally:
 if stage is not None and not stage.closed:stage.close()
 if fd is not None:os.close(fd)
 tracemalloc.stop()
assert before==set(os.listdir('/proc/self/fd'))
result.update(cleanup='EXACT_OWNED_FILES_REMOVED_FD_INVENTORY_MATCH',scope='UNPRIVILEGED_SELECTED_ARCHIVE_MATERIALIZATION_NOT_RUNTIME_CAPTURE_OR_SERVICE')
print(json.dumps(result,sort_keys=True))
