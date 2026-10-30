import hashlib,json,os,pathlib,resource,sys,time,tracemalloc
sys.path.insert(0,'/workspace/Buffalo-Procurement-System/procurement/tools')
import hosted_synthetic_namespace as ns
from hosted_synthetic_runtime_regression import _descriptor_inventory
assert os.getuid()!=0
size=152170928;assert sys.argv[1]=='34e8f3ad16ef31e8ecafc2d915b76f16a9571b98'
before=_descriptor_inventory();owned=None;started=time.monotonic();tracemalloc.start();stages=[]
def stage(name):
 current,peak=tracemalloc.get_traced_memory();stages.append({'name':name,'current':current,'peak':peak,'rss_peak':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024})
try:
 entries=tuple(ns.backend._BrowserRuntimeBundleEntry('/'+name,'F',0o444,0,0,'audit-source:'+hashlib.sha256(raw).hexdigest(),raw) for name,raw in [('a',b'a'*(size//2)),('b',b'b'*(size-size//2))])
 stage('original_entries')
 owned=ns.backend._seal_browser_runtime_bundle(entries);stage('seal_and_readback')
 validated=ns.backend._validate_pinned_browser_runtime_bundle(owned);stage('retained_validation_result')
 assert validated==entries
 allocated=os.fstat(owned.descriptor).st_blocks*512;rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
 result={'regular_bytes':owned.regular_bytes,'bundle_bytes':owned.size,'bundle_sha256':owned.sha256,'manifest_sha256':owned.manifest_sha256,'stages':stages,'memfd_allocated_bytes':allocated,'same_formula_rss_plus_memfd':rss+allocated,'within_512m_component_envelope':rss+allocated<=536870912,'seconds':round(time.monotonic()-started,6),'scope':'SYNTHETIC_CODEC_ONLY_NOT_ACTUAL_PG_OR_SERVICE'}
finally:
 if owned is not None: assert ns.backend._close_browser_runtime_bundle_descriptor(owned.descriptor,owned.target,(owned.device,owned.inode))
 assert _descriptor_inventory()==before;tracemalloc.stop()
from hosted_synthetic_postgres_runtime import memory_envelope
result['source_commit']=sys.argv[1];result['source_tree']='5d300467117ce3e577c72c92068a13970aa1826f';result['memory_envelope']=memory_envelope(0,allocated);result['python_version']=sys.version;result['python_executable']=str(pathlib.Path('/proc/self/exe').resolve());result['cleanup']='FD_INVENTORY_MATCH';print(json.dumps(result,sort_keys=True))
