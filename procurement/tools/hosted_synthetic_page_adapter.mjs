// Inert adapter for one already-owned, reviewed Chromium session. It does not
// connect/launch/login or grant containment/release authority. The outer driver
// retains its all-target/egress policy; integration must have exactly ONE Fetch
// resolution owner combining that policy with these mutation gates. Never attach
// the legacy driver's independently continuing Fetch handler alongside this one.
import {ORIGIN} from './hosted_synthetic_apply_step.mjs';
const requireValue=v=>{if(!v)throw new Error('hosted page adapter refused');};
const refusal=()=>new Error('hosted page adapter refused');
const sockets=new WeakSet();
const ID=/^[A-Za-z0-9_.:-]{1,128}$/;
const UUID='[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}';
const READ=new RegExp('^/(?:health|favicon\\.ico|supplier-mapping(?:/status|/'+UUID+')?|price-books(?:/'+UUID+'(?:/apply-preview)?)?|monday-runs(?:/'+UUID+')?)$');
const WRITE=new RegExp('^/(?:price-books/'+UUID+'/(?:apply-preview|apply)|supplier-mapping/intake|supplier-mapping/'+UUID+'/(?:decision|selection)|monday-runs/prepare|monday-runs/'+UUID+'/(?:retire-stale-forecast|build|blockers/[1-9][0-9]*/exclude|recommendations/[1-9][0-9]*/review))$');
const ARTIFACT=new RegExp('^/monday-runs/'+UUID+'/artifacts/[1-9][0-9]{0,17}$');
const urlValue=raw=>{try{const u=new URL(raw);return u.origin===ORIGIN && !u.username && !u.password && !u.search && !u.hash && raw===u.href?u:null;}catch{return null;}};

// Connected-socket ownership and kernel/browser/TLS identity are outer gates.
// Cancellation removes only this transport's pending command; no remote side
// effect is claimed undone. Late responses cannot satisfy a different command.
export class HostedCdpTransport {
 constructor(socket,expectedUrl){
   requireValue(typeof expectedUrl==='string' && /^ws:\/\/127\.0\.0\.1:[1-9][0-9]{0,4}\/devtools\/browser\/[A-Za-z0-9_-]{16,128}$/.test(expectedUrl) &&
     Number(new URL(expectedUrl).port)<=65535 && socket?.url===expectedUrl && !sockets.has(socket) && socket.readyState===1 && typeof socket.send==='function' && typeof socket.addEventListener==='function' && typeof socket.removeEventListener==='function');
   // Exclusive dispatcher ownership is permanent for this socket. Outer guards
   // must subscribe to this transport, never attach a second command allocator.
   sockets.add(socket);
   this.socket=socket;this.pending=new Map();this.events=new Set();this.failures=new Set();this.next=1;this.closed=false;
   this.message=e=>{
     try{
       requireValue(typeof e.data==='string' && Buffer.byteLength(e.data)<=2*1024*1024);
       const m=JSON.parse(e.data);
       if(m.id!==undefined){
         requireValue(Number.isSafeInteger(m.id) && m.id>0);
         const pending=this.pending.get(m.id);if(!pending)return;
         requireValue((m.sessionId??null)===pending.sessionId);this.pending.delete(m.id);
         if(m.error){const error=refusal();if(m.error.code===-32000 && m.error.message==='Execution context was destroyed.')error.navigationContextDestroyed=true;pending.reject(error);}
         else pending.resolve(m.result);
       }else{requireValue(typeof m.method==='string');for(const callback of this.events)callback(m);}
     }catch{this.fail();}
   };
   this.ended=()=>this.fail();socket.addEventListener('message',this.message);socket.addEventListener('close',this.ended);socket.addEventListener('error',this.ended);
 }
 command(method,params={},sessionId=null){
   requireValue(!this.closed && this.pending.size<64 && typeof method==='string' && (sessionId===null || typeof sessionId==='string' && ID.test(sessionId)));
   const id=this.next++;let rejectOwned;
   const promise=new Promise((resolve,reject)=>{rejectOwned=reject;this.pending.set(id,{resolve,reject,sessionId});});
   try{this.socket.send(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})}));}
   catch{this.pending.delete(id);rejectOwned(refusal());this.fail();}
   return {promise,cancel:()=>{if(this.pending.delete(id))rejectOwned(refusal());}};
 }
 onEvent(callback){requireValue(!this.closed && typeof callback==='function');this.events.add(callback);return ()=>this.events.delete(callback);}
 onFailure(callback){requireValue(!this.closed && typeof callback==='function');this.failures.add(callback);return ()=>this.failures.delete(callback);}
 fail(){if(this.closed)return;this.closed=true;for(const item of this.pending.values())item.reject(refusal());this.pending.clear();for(const callback of this.failures)callback();}
 close(){this.fail();this.socket.removeEventListener('message',this.message);this.socket.removeEventListener('close',this.ended);this.socket.removeEventListener('error',this.ended);this.events.clear();this.failures.clear();this.socket.close();}
}

export class HostedPageAdapter {
 constructor(transport,{sessionId,frameId,targetId},signal=null,{timeoutMs=10000}={}){
   requireValue(transport && ['command','onEvent','onFailure'].every(k=>typeof transport[k]==='function') &&
     [sessionId,frameId,targetId].every(v=>typeof v==='string' && ID.test(v)) && Number.isInteger(timeoutMs) && timeoutMs>=1 && timeoutMs<=10000 &&
     (signal===null || typeof signal.addEventListener==='function' && !signal.aborted));
   this.transport=transport;this.sessionId=sessionId;this.frameId=frameId;this.targetId=targetId;this.timeout=timeoutMs;
   this.failed=false;this.closed=false;this.started=false;this.active=false;this.guard=null;this.downloadGuard=null;this.sequence=0;
   this.requests=new Map();this.allowed=new Map();this.continuations=[];this.responses=[];this.redirects=[];this.frames=new Map();this.loads=new Map();
   this.tasks=new Set();this.commands=new Set();this.waiters=new Set();this.marks=new WeakMap();this.signal=signal;
   this.unsubscribe=transport.onEvent(m=>this.event(m));this.unfail=transport.onFailure(()=>this.fail());
   this.abort=()=>this.fail();signal?.addEventListener('abort',this.abort,{once:true});
 }
 healthy(){requireValue(!this.failed && !this.closed);}
 fail(){
   if(this.failed)return;this.failed=true;this.guard=null;this.downloadGuard=null;
   for(const cancel of this.commands)cancel();
   for(const wake of this.waiters)wake();
 }
 async command(method,params={},sessionId=this.sessionId,deadline=Date.now()+this.timeout,{cleanup=false}={}){
   if(!cleanup)this.healthy();const remaining=deadline-Date.now();requireValue(remaining>0);
   const owned=this.transport.command(method,params,sessionId);requireValue(owned && owned.promise instanceof Promise && typeof owned.cancel==='function');
   let timer;const cancel=()=>owned.cancel();if(!cleanup)this.commands.add(cancel);
   try{return await Promise.race([owned.promise,new Promise((_,reject)=>{timer=setTimeout(()=>{cancel();reject(refusal());},remaining);})]);}
   finally{clearTimeout(timer);this.commands.delete(cancel);}
 }
 async operation(action){
   this.healthy();if(!this.started || this.active){this.fail();throw refusal();}this.active=true;
   const deadline=Date.now()+this.timeout;
   try{const result=await action(deadline);await this.flush(deadline);this.healthy();return result;}
   catch{this.fail();throw refusal();}finally{this.active=false;}
 }
 async start(){
   this.healthy();requireValue(!this.started && !this.active);this.active=true;const deadline=Date.now()+this.timeout;
   try{
     // An omitted targetId asks the attached session for its OWN target. A
     // browser-level lookup of the supplied ID would not bind S to T.
     const target=await this.command('Target.getTargetInfo',{},this.sessionId,deadline);
     requireValue(target?.targetInfo?.targetId===this.targetId && target.targetInfo.type==='page' && target.targetInfo.attached===true);
     const context=target.targetInfo.browserContextId;
     requireValue(context===undefined || typeof context==='string' && ID.test(context));this.browserContextId=context??null;
     const tree=await this.command('Page.getFrameTree',{},this.sessionId,deadline);
     requireValue(tree?.frameTree?.frame?.id===this.frameId && !tree.frameTree.frame.parentId);
     for(const [method,params] of [['Page.enable',{}],['Network.enable',{}],['Network.setCacheDisabled',{cacheDisabled:true}],
       ['Network.setBypassServiceWorker',{bypass:true}],['Page.setLifecycleEventsEnabled',{enabled:true}],
       ['Fetch.enable',{patterns:[{urlPattern:'*',requestStage:'Request'}],handleAuthRequests:true}]])await this.command(method,params,this.sessionId,deadline);
     this.started=true;return this;
   }catch{this.fail();throw refusal();}finally{this.active=false;}
 }
 event(message){
   try{
     const {method,sessionId}=message,p=message.params||{};this.sequence++;requireValue(this.sequence<=20000);
     if(method==='Fetch.requestPaused'){
       // Consume allowance synchronously before the first await, including when
       // two paused events are delivered in the same JavaScript turn.
       const task=this.paused(sessionId,p);this.tasks.add(task);
       void task.catch(()=>this.fail()).finally(()=>{this.tasks.delete(task);this.wake();});
       return;
     }
     if(method==='Fetch.authRequired'){
       const task=this.command('Fetch.continueWithAuth',{requestId:p.requestId,authChallengeResponse:{response:'CancelAuth'}},sessionId,Date.now()+this.timeout,{cleanup:true});
       this.tasks.add(task);void task.catch(()=>{}).finally(()=>{this.tasks.delete(task);this.wake();});this.fail();return;
     }
     if(method==='Target.detachedFromTarget' && p.sessionId===this.sessionId){this.fail();return;}
     if(sessionId!==this.sessionId)return;
     if(method==='Network.requestWillBeSent')this.requestEvent(p);
     if(method==='Network.responseReceived')this.responseEvent(p);
     if(method==='Page.frameNavigated' && p.frame?.id===this.frameId){
       requireValue(!p.frame.parentId && typeof p.frame.loaderId==='string' && ID.test(p.frame.loaderId));
       this.frames.set(p.frame.loaderId,{url:p.frame.url,sequence:this.sequence});
     }
     if(method==='Page.lifecycleEvent' && p.frameId===this.frameId && p.name==='load'){
       requireValue(typeof p.loaderId==='string' && ID.test(p.loaderId));this.loads.set(p.loaderId,this.sequence);
     }
     this.wake();
   }catch{this.fail();}
 }
 async paused(sessionId,p){
   let continued=false,receipt=null;
   try{
   const r=p.request||{},url=urlValue(r.url),main=sessionId===this.sessionId && p.frameId===this.frameId;
   let permit=false;
   if(!this.failed && !this.closed && main && url && typeof p.networkId==='string' && ID.test(p.networkId)){
     if(r.method==='GET'){
       if(ARTIFACT.test(url.pathname) && this.downloadGuard){
         receipt=this.downloadGuard;permit=!receipt.attempted && receipt.url===r.url;receipt.attempted=true;
         receipt.receipts.push(permit);if(!permit)receipt.refused=true;
       }else permit=READ.test(url.pathname);
     }
     else if(r.method==='POST' && WRITE.test(url.pathname) && this.guard){
       receipt=this.guard;
       if(!receipt.attempted){receipt.attempted=true;permit=receipt.check({method:r.method,url:r.url,postData:r.postData})===true;}
       receipt.receipts.push(permit);if(!permit)receipt.refused=true;
     }
   }
   requireValue(permit);
   requireValue(typeof p.requestId==='string' && ID.test(p.requestId));
   requireValue(!this.allowed.has(p.networkId) || r.method==='GET' && this.allowed.get(p.networkId).method==='POST' &&
     !this.continuations.some(c=>c.id===p.networkId && c.method==='GET') &&
     this.redirects.some(x=>x.id===p.networkId && new URL(x.location,x.url).href===r.url));
   const record={id:p.networkId,method:r.method,url:r.url,frame:this.frameId,sequence:this.sequence,continued:false,continueIssued:false,receipt};
   if(r.method==='POST')this.allowed.set(p.networkId,record);
   else if(!this.allowed.has(p.networkId))this.allowed.set(p.networkId,record);
   this.continuations.push(record);
   if(receipt)receipt.pending++;
   try{record.continueIssued=true;await this.command('Fetch.continueRequest',{requestId:p.requestId},sessionId);record.continued=true;continued=true;}
   finally{if(receipt)receipt.pending--;}
   }catch{
     // Refusal is latched synchronously before any denial ACK. A thrown guard,
     // collision, or ambiguous continuation cannot leave a live allowance.
     this.fail();
     if(!continued)await this.command('Fetch.failRequest',{requestId:p.requestId,errorReason:'BlockedByClient'},sessionId,Date.now()+this.timeout,{cleanup:true});
     throw refusal();
   }
 }
 requestEvent(p){
   if(p.frameId!==this.frameId)return;
   const r=p.request||{},url=urlValue(r.url);requireValue(url && typeof p.requestId==='string' && ID.test(p.requestId) && typeof p.loaderId==='string' && ID.test(p.loaderId));
   const previous=this.requests.get(p.requestId);
   if(p.redirectResponse){
     requireValue(previous && p.redirectResponse.url===previous.url && p.redirectResponse.status===303 && r.method==='GET');
     const pairs=Object.entries(p.redirectResponse.headers||{}).filter(([k])=>k.toLowerCase()==='location');
     requireValue(pairs.length===1 && typeof pairs[0][1]==='string');
     const dest=new URL(pairs[0][1],previous.url);requireValue(urlValue(dest.href) && dest.href===r.url);
     this.redirects.push({...previous,status:303,location:pairs[0][1],sequence:this.sequence});
   }
   this.requests.set(p.requestId,{id:p.requestId,method:r.method,url:r.url,loader:p.loaderId,frame:p.frameId,sequence:this.sequence,requestSequence:this.sequence});
 }
 responseEvent(p){
   if(p.frameId!==this.frameId)return;
   const request=this.requests.get(p.requestId);requireValue(request && p.loaderId===request.loader && p.response?.url===request.url && Number.isInteger(p.response.status));
   let headers;
   if(ARTIFACT.test(new URL(request.url).pathname)){
     headers={};
     for(const name of ['content-type','content-disposition','cache-control']){
       const pairs=Object.entries(p.response.headers||{}).filter(([k])=>k.toLowerCase()===name);
       requireValue(pairs.length===1 && typeof pairs[0][1]==='string' && pairs[0][1].length<=256);headers[name]=pairs[0][1];
     }
   }
   this.responses.push({...request,status:p.response.status,sequence:this.sequence,...(headers?{headers}:{})});
 }
 wake(){for(const resolve of this.waiters)resolve();}
 async wait(predicate,deadline){
   while(true){this.healthy();const result=predicate();if(result)return result;const remaining=deadline-Date.now();requireValue(remaining>0);
     await new Promise(resolve=>{let timer;const wake=()=>{clearTimeout(timer);this.waiters.delete(wake);resolve();};this.waiters.add(wake);timer=setTimeout(wake,remaining);});
   }
 }
 async flush(deadline){await this.wait(()=>this.tasks.size===0,deadline);}
 armRequestGuard(check){
   this.healthy();requireValue(this.started && !this.active && this.guard===null && this.downloadGuard===null && typeof check==='function');
   const receipt={check,attempted:false,refused:false,receipts:[],pending:0,closed:false};this.guard=receipt;
   return {matches:()=>{this.healthy();requireValue(!receipt.closed && this.guard===receipt && receipt.pending===0 && !receipt.refused);return [...receipt.receipts];},
     close:()=>{if(receipt.closed)return;receipt.closed=true;if(this.guard===receipt)this.guard=null;if(receipt.pending)this.fail();}};
 }
 armArtifactGet(url){
   this.healthy();const parsed=urlValue(url);
   requireValue(this.started && !this.active && this.guard===null && this.downloadGuard===null && parsed && ARTIFACT.test(parsed.pathname));
   const receipt={url,attempted:false,refused:false,receipts:[],pending:0,closed:false,before:this.sequence};this.downloadGuard=receipt;
   const request=(issuedOnly=false)=>{
     const records=this.continuations.filter(r=>r.receipt===receipt && r.sequence>receipt.before && (issuedOnly?r.continueIssued:r.continued));
     requireValue(records.length===1);const r=records[0],n=this.requests.get(r.id);
     requireValue(n && n.requestSequence>receipt.before && n.url===url && n.method==='GET' && r.method==='GET' && r.url===url);
     return {requestId:r.id,frameId:this.frameId,url};
   };
   return {request:()=>request(),issued:()=>{this.healthy();return request(true);},ready:()=>{
     this.healthy();const r=request();return this.responses.some(x=>x.id===r.requestId);
   },observe:()=>{
     this.healthy();requireValue(!receipt.closed && this.downloadGuard===receipt && receipt.pending===0 && !receipt.refused &&
       receipt.attempted && receipt.receipts.length===1 && receipt.receipts[0]===true);
     const r=request(),matches=this.responses.filter(x=>x.id===r.requestId && x.requestSequence>receipt.before && x.sequence>receipt.before && x.url===url && x.method==='GET');
     requireValue(matches.length===1 && matches[0].status===200 && !this.redirects.some(x=>x.id===r.requestId));
     return {...r,status:200,headers:{...matches[0].headers}};
   },close:()=>{if(receipt.closed)return;receipt.closed=true;if(this.downloadGuard===receipt)this.downloadGuard=null;if(receipt.pending)this.fail();}};
 }
 mark(){this.healthy();requireValue(this.started && !this.active);const mark=Object.freeze({});this.marks.set(mark,this.sequence);return mark;}
 lookup(records,pathname,method,mark){
   this.healthy();requireValue(this.marks.has(mark));
   const matches=records.filter(r=>r.sequence>this.marks.get(mark) && r.requestSequence>this.marks.get(mark) && r.url===ORIGIN+pathname && r.method===method &&
     this.allowed.get(r.id)?.continued && this.allowed.get(r.id).sequence>this.marks.get(mark) && this.allowed.get(r.id).url===r.url && this.allowed.get(r.id).method===r.method);
   requireValue(matches.length<=1);return matches.length?{status:matches[0].status,...('location'in matches[0]?{location:matches[0].location}:{})}:null;
 }
 responseAfter(path,method,mark){return this.lookup(this.responses,path,method,mark);}
 redirectAfter(path,method,mark){return this.lookup(this.redirects,path,method,mark);}
 async rawEvaluate(expression,deadline){
   requireValue(typeof expression==='string' && Buffer.byteLength(expression)<=128*1024);
   const r=await this.command('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true,userGesture:true},this.sessionId,deadline);
   requireValue(r && !r.exceptionDetails && r.result && Buffer.byteLength(JSON.stringify(r.result.value)??'null')<=1024*1024);
   return r.result.value;
 }
 evaluate(expression){return this.operation(deadline=>this.rawEvaluate(expression,deadline));}
 navigate(url){return this.operation(async deadline=>{
   const u=urlValue(url);requireValue(u && READ.test(u.pathname) && this.guard===null && this.downloadGuard===null);const before=this.sequence;
   const r=await this.command('Page.navigate',{url},this.sessionId,deadline);requireValue(r?.frameId===this.frameId && typeof r.loaderId==='string' && ID.test(r.loaderId) && !r.errorText);
   await this.wait(()=>this.frames.get(r.loaderId)?.sequence>before && this.frames.get(r.loaderId).url===url && this.loads.get(r.loaderId)>before &&
     this.responses.some(x=>x.sequence>before && x.requestSequence>before && x.loader===r.loaderId && x.url===url && x.method==='GET' && x.status===200 && this.allowed.get(x.id)?.sequence>before && this.allowed.get(x.id)?.continued),deadline);
 });}
 submit(expression){return this.operation(async deadline=>{
   const receipt=this.guard;requireValue(receipt && !receipt.closed && !receipt.attempted);const before=this.sequence;
   let destroyed=false;
   try{await this.rawEvaluate(expression,deadline);}catch(error){if(error.navigationContextDestroyed===true)destroyed=true;else throw error;}
   await this.wait(()=>{
     const records=[...this.allowed.values()].filter(r=>r.receipt===receipt && r.method==='POST' && r.sequence>before && r.continued);
     requireValue(records.length<=1);if(!records.length)return false;
     const req=this.requests.get(records[0].id);if(!req || req.requestSequence<=before)return false;
     const frame=this.frames.get(req.loader);
     return frame && frame.sequence>before && frame.url===req.url && this.loads.get(req.loader)>before &&
       this.responses.some(x=>x.id===req.id && x.loader===req.loader && x.url===req.url && x.status===200 && x.sequence>before &&
         this.continuations.some(c=>c.id===x.id && c.method===x.method && c.url===x.url && c.sequence>before && c.continued));
   },deadline);
   requireValue(receipt.attempted && !receipt.refused && receipt.receipts.length===1 && receipt.receipts[0]===true);
   if(destroyed)requireValue([...this.redirects,...this.responses].some(r=>r.sequence>before && this.allowed.get(r.id)?.receipt===receipt));
 });}
 readJson(path){return this.operation(async deadline=>{
   requireValue(path==='/supplier-mapping/status' && this.guard===null && this.downloadGuard===null);const before=this.sequence;
   const result=await this.rawEvaluate(`(async()=>{if(location.origin!==${JSON.stringify(ORIGIN)})throw new Error('status origin refused');
     const before=location.href,controller=new AbortController(),timer=setTimeout(()=>controller.abort(),${Math.max(1,deadline-Date.now()-10)});
     try{const r=await fetch(${JSON.stringify(ORIGIN+path)},{method:'GET',credentials:'same-origin',redirect:'error',cache:'no-store',signal:controller.signal});
       if(r.url!==${JSON.stringify(ORIGIN+path)}||r.status!==200||r.headers.get('content-type')?.split(';')[0]!=='application/json')throw new Error('status response refused');
       const reader=r.body.getReader();let size=0,text='';const decoder=new TextDecoder('utf-8',{fatal:true});
       try{while(true){const part=await reader.read();if(part.done)break;size+=part.value.length;if(size>16384)throw new Error('status size refused');text+=decoder.decode(part.value,{stream:true});}text+=decoder.decode();}
       finally{await reader.cancel();reader.releaseLock();}
       if(location.href!==before)throw new Error('status navigation refused');return {url:r.url,status:r.status,body:JSON.parse(text)};
     }finally{clearTimeout(timer);controller.abort();}})()`,deadline);
   await this.flush(deadline);
   const reads=this.continuations.filter(r=>r.sequence>before && r.method==='GET' && r.url===ORIGIN+path && r.continued);
   requireValue(reads.length===1 && result?.url===ORIGIN+path && result.status===200 &&
     this.responses.filter(r=>r.sequence>before && r.requestSequence>before && r.id===reads[0].id && r.url===reads[0].url && r.method==='GET' && r.status===200).length===1 &&
     !this.redirects.some(r=>r.sequence>before && r.id===reads[0].id));
   return result;
 });}
 close(){this.closed=true;this.fail();this.signal?.removeEventListener('abort',this.abort);}
 async disposeAfterTargetExit(){
   requireValue(this.closed && this.commands.size===0 && this.tasks.size===0);
   const r=await this.command('Target.getTargets',{},null,Date.now()+this.timeout,{cleanup:true});
   requireValue(Array.isArray(r?.targetInfos) && !r.targetInfos.some(x=>x.targetId===this.targetId));
   this.unsubscribe();this.unfail();this.waiters.clear();
 }
}
