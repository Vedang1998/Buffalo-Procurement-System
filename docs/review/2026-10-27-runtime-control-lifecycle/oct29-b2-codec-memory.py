import sys,os,hashlib,tracemalloc,resource,json,time
sys.path.insert(0,'procurement/tools')
import hosted_synthetic_namespace as ns
from hosted_synthetic_runtime_regression import _descriptor_inventory
before=_descriptor_inventory();tracemalloc.start();started=time.monotonic()
raw=b'x'*(16*1024**2)
e=ns.backend._BrowserRuntimeBundleEntry('/synthetic','F',0o444,0,0,'audit-source:'+hashlib.sha256(raw).hexdigest(),raw)
b=ns.backend._seal_browser_runtime_bundle((e,))
current,peak=tracemalloc.get_traced_memory();info=os.fstat(b.descriptor)
result={'synthetic_regular_bytes':len(raw),'traced_current_bytes':current,'traced_peak_bytes':peak,'process_peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'owned_memfd_allocated_bytes':info.st_blocks*512,'codec_sha256':b.sha256,'manifest_sha256':b.manifest_sha256,'seconds':round(time.monotonic()-started,3),'scope':'SCALED_CODEC_COMPONENT_NOT_TARGET_SERVICE_MEMORY'}
assert ns.backend._close_browser_runtime_bundle_descriptor(b.descriptor,b.target,(b.device,b.inode));assert _descriptor_inventory()==before
result['owned_descriptors_closed']=True;print(json.dumps(result,sort_keys=True))
