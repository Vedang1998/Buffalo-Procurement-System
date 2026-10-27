// Dormant transfer collector. No browser launch, login, file read or packet PASS.
// Requires the sole shared CDP dispatcher and the preventive page adapter. The
// outer reviewed driver owns download storage, all targets and browser lifetime,
// and must configure downloads in this session-reported browserContextId.
import {HostedPageAdapter} from './hosted_synthetic_page_adapter.mjs';
import {ORIGIN,snapshotBinding} from './hosted_synthetic_apply_step.mjs';
const requireValue=v=>{if(!v)throw new Error('hosted download refused');};
const refusal=()=>new Error('hosted download refused');
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
export const MAX_ARTIFACT_BYTES=1024*1024; // Parser bound; not a host budget increase.
function binding(value){
 const b=snapshotBinding(value);requireValue(b && JSON.stringify(Object.keys(b).sort())===JSON.stringify(['artifact_id','kind','run_id','size_bytes'].sort()) &&
   typeof b.run_id==='string' && UUID.test(b.run_id) && typeof b.artifact_id==='string' && /^[1-9][0-9]{0,17}$/.test(b.artifact_id) &&
   ['VENDOR_INTERNAL_CSV','EMERGENCY_REVIEW_PACKET'].includes(b.kind) && Number.isSafeInteger(b.size_bytes) && b.size_bytes>0 && b.size_bytes<=MAX_ARTIFACT_BYTES);
 const suffix=b.kind==='VENDOR_INTERNAL_CSV'?'internal.csv':'review.zip';
 return {...b,url:ORIGIN+'/monday-runs/'+b.run_id+'/artifacts/'+b.artifact_id,
   filename:'monday-'+b.run_id+'-'+b.artifact_id+'.'+suffix,contentType:b.kind==='VENDOR_INTERNAL_CSV'?'text/csv; charset=utf-8':'application/zip'};
}
export class HostedDownloadCollector{
 constructor(page,signal=null,{timeoutMs=10000}={}){
   requireValue(page instanceof HostedPageAdapter && page.started && !page.failed && !page.closed &&
     Number.isInteger(timeoutMs) && timeoutMs>=1 && timeoutMs<=10000 && (signal===null || typeof signal.addEventListener==='function' && !signal.aborted));
   this.page=page;this.transport=page.transport;this.timeout=timeoutMs;this.signal=signal;this.failed=false;this.closed=false;this.active=null;this.seen=new Set();this.urls=new Set();this.waiters=new Set();this.observations=[];
   // Installed before any click, including a synchronously completing transfer.
   this.unsubscribe=this.transport.onEvent(message=>this.event(message));this.unfail=this.transport.onFailure(()=>this.fail());
   this.abort=()=>this.fail();signal?.addEventListener('abort',this.abort,{once:true});
 }
 healthy(){requireValue(!this.failed && !this.closed);this.page.healthy();}
 fail(){this.failed=true;this.page.fail();for(const wake of this.waiters)wake();}
 event(message){
   if(!['Browser.downloadWillBegin','Browser.downloadProgress'].includes(message.method)){for(const wake of this.waiters)wake();return;}
   try{
     this.healthy();const a=this.active,p=message.params;requireValue(a && !message.sessionId && p && typeof p.guid==='string' && UUID.test(p.guid));
     if(message.method==='Browser.downloadWillBegin'){
       requireValue(!a.guid && !this.seen.has(p.guid) && p.frameId===this.page.frameId && p.url===a.binding.url);
       // A delayed old transfer cannot be retroactively joined to a later GET.
       // Request + preventive continuation must already be issued in this lease;
       // the continuation ACK may legitimately arrive after Browser events.
       const issued=a.allowance.issued();requireValue(issued.url===p.url && issued.frameId===p.frameId);a.requestId=issued.requestId;
       // Only this exact owned-frame/URL generation is a cancellation candidate;
       // a continued request receipt must additionally prove ownership below.
       a.guid=p.guid;this.seen.add(p.guid);requireValue(p.suggestedFilename===a.binding.filename);
     }else{
       requireValue(a.guid===p.guid && !a.completed && ['inProgress','completed'].includes(p.state) &&
         Number.isSafeInteger(p.totalBytes) && Number.isSafeInteger(p.receivedBytes) && p.totalBytes>=0 && p.receivedBytes>=0 &&
         p.totalBytes<=a.binding.size_bytes && p.receivedBytes<=a.binding.size_bytes &&
         p.totalBytes>=a.total && p.receivedBytes>=a.received && (p.totalBytes===0 || p.receivedBytes<=p.totalBytes));
       a.total=p.totalBytes;a.received=p.receivedBytes;
       if(p.state==='completed'){requireValue(a.total===a.binding.size_bytes && a.received===a.binding.size_bytes);a.completed=true;}
       // filePath is deliberately neither read nor used as authority.
     }
     for(const wake of this.waiters)wake();
   }catch{this.fail();}
 }
 async wait(predicate,deadline){
   while(true){this.healthy();if(predicate())return;const remaining=deadline-Date.now();requireValue(remaining>0);
     await new Promise(resolve=>{let timer;const wake=()=>{clearTimeout(timer);this.waiters.delete(wake);resolve();};this.waiters.add(wake);timer=setTimeout(wake,remaining);});
   }
 }
 async collect(value){
   this.healthy();if(this.active){this.fail();throw refusal();}const b=binding(value);requireValue(!this.urls.has(b.url) && this.observations.length<3);
   const deadline=Date.now()+this.timeout,allowance=this.page.armArtifactGet(b.url),a={binding:b,allowance,guid:null,completed:false,total:0,received:0};
   this.active=a;this.urls.add(b.url);let timer;
   try{
     await Promise.race([this.page.evaluate(`(()=>{if(location.href!==${JSON.stringify(ORIGIN+'/monday-runs/'+b.run_id)})throw new Error('artifact page refused');
       const nodes=[...document.querySelectorAll('a[href]')].filter(a=>a.href===${JSON.stringify(b.url)});
       if(nodes.length!==1||!nodes[0].isConnected||!['','_self'].includes(nodes[0].target)||nodes[0].hasAttribute('download'))throw new Error('artifact anchor refused');
       nodes[0].click();return true;})()`),new Promise((_,reject)=>{timer=setTimeout(()=>{this.fail();reject(refusal());},Math.max(1,deadline-Date.now()));})]);
     clearTimeout(timer);await this.wait(()=>a.completed && allowance.ready(),deadline);
     const request=allowance.observe();requireValue(request.requestId===a.requestId && request.url===b.url && request.frameId===this.page.frameId &&
       request.headers['content-type']===b.contentType && request.headers['content-disposition']==='attachment; filename='+b.filename && request.headers['cache-control']==='no-store');
     const observation=Object.freeze({run_id:b.run_id,artifact_id:b.artifact_id,kind:b.kind,url:b.url,guid:a.guid,
       suggested_filename:b.filename,size_bytes:a.received,request_id:request.requestId,browser_context_id:this.page.browserContextId,
       scope:'PROVISIONAL_BROWSER_TRANSFER_OBSERVATION_ONLY',file_bytes:'NOT_READ',packet_validation:'NOT_RUN',execution_authority:false});
     this.observations.push(observation);return observation;
   }catch{
     this.fail();
     // Do not cancel another frame's GUID or infer ownership from a filename.
     // Ambiguous/expired cleanup remains the outer owned-browser obligation.
     if(a.guid && !a.completed && Date.now()<deadline){
       try{const r=allowance.request();if(r.url===b.url && r.frameId===this.page.frameId)
         await this.page.command('Browser.cancelDownload',{guid:a.guid,...(this.page.browserContextId?{browserContextId:this.page.browserContextId}:{})},null,deadline,{cleanup:true});}catch{}
     }
     throw refusal();
   }finally{clearTimeout(timer);allowance.close();this.active=null;}
 }
 // Closing a socket does NOT establish process/resource cleanup. This merely
 // releases JS subscriptions after the outer owner has closed page+transport.
 close(){requireValue(this.active===null && this.page.closed && this.transport.closed);this.closed=true;this.unsubscribe();this.unfail();this.signal?.removeEventListener('abort',this.abort);for(const wake of this.waiters)wake();}
}
