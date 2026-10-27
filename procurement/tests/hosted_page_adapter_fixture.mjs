// SCRIPTED_CDP_PROTOCOL_ONLY: no browser, credentials, network or host proof.
import assert from 'node:assert/strict';
import vm from 'node:vm';
import crypto from 'node:crypto';
import {HostedCdpTransport,HostedPageAdapter} from '../tools/hosted_synthetic_page_adapter.mjs';
import {ORIGIN,armSinglePost} from '../tools/hosted_synthetic_apply_step.mjs';
const mode=process.argv[2],S='S1',F='F1',T='T1',L='L1',N='N1';
const batch='00000000-0000-4000-8000-000000000001',path='/price-books/'+batch+'/apply-preview';
const token='S'.repeat(43),body=new URLSearchParams({_buffalo_staging_csrf:token,actor:'owner:staging:01',apply_idempotency_key:'fixture'}).toString();
const url=ORIGIN+path,dest=ORIGIN+'/price-books/'+batch;
const tick=()=>new Promise(resolve=>setImmediate(resolve));
class Socket{
 constructor(){this.url='ws://127.0.0.1:9222/devtools/browser/abcdefghijklmnop';this.readyState=1;this.listeners=new Map();this.sent=[];this.forwarded=[];this.denied=[];this.delayed=[];this.closed=false;}
 addEventListener(name,fn){if(!this.listeners.has(name))this.listeners.set(name,new Set());this.listeners.get(name).add(fn);}
 removeEventListener(name,fn){this.listeners.get(name)?.delete(fn);}
 emit(name,event){for(const fn of [...this.listeners.get(name)||[]])fn(event);}
 message(value){this.emit('message',{data:JSON.stringify(value)});}
 reply(c,result={}){this.message({id:c.id,...(c.sessionId?{sessionId:c.sessionId}:{}),result});}
 error(c,code=-32000,message='refused'){this.message({id:c.id,...(c.sessionId?{sessionId:c.sessionId}:{}),error:{code,message}});}
 event(method,params,sessionId=S){this.message({method,params,...(sessionId?{sessionId}:{})});}
 request(method='POST',requestId=N,loaderId=L,requestUrl=url,extra={}){this.event('Network.requestWillBeSent',{requestId,loaderId,frameId:F,request:{method,url:requestUrl},...extra});}
 paused(method='POST',networkId=N,requestUrl=url,extra={}){this.event('Fetch.requestPaused',{requestId:'fetch-'+method+'-'+networkId,networkId,frameId:F,request:{method,url:requestUrl,postData:body},...extra});}
 response(method='POST',requestId=N,loaderId=L,requestUrl=url,status=200){this.event('Network.responseReceived',{requestId,loaderId,frameId:F,response:{url:requestUrl,status}});}
 loaded(requestUrl=url,loaderId=L,options={}){
   this.event('Page.frameNavigated',{frame:{id:F,loaderId,url:requestUrl}});
   if(!options.noLoad)this.event('Page.lifecycleEvent',{frameId:F,loaderId,name:'load'});
 }
 send(raw){
   const c=JSON.parse(raw);this.sent.push(c);
   if(c.method==='Fetch.failRequest'){this.denied.push(c.params.requestId);if(mode==='denial_race')this.delayed.push(c);else this.reply(c);return;}
   if(c.method==='Fetch.continueRequest'){
     this.forwarded.push(c.params.requestId);
     if(mode==='continue_error'){this.error(c);return;}
     if(mode==='continue_hangs'||mode==='delayed_ack'){this.delayed.push(c);return;}
     this.reply(c);
     if(this.onContinue)this.onContinue(c);return;
   }
   if(c.method==='Target.getTargetInfo'){assert.equal(c.sessionId,S);assert.deepEqual(c.params,{});this.reply(c,{targetInfo:{targetId:mode==='target_binding'?'OTHER':T,type:mode==='wrong_target'?'worker':'page',attached:true}});return;}
   if(c.method==='Page.getFrameTree'){this.reply(c,{frameTree:{frame:{id:mode==='wrong_frame'?'OTHER':F}}});return;}
   if(c.method==='Target.getTargets'){this.reply(c,{targetInfos:mode==='target_alive'?[{targetId:T}]:[]});return;}
   if(c.method==='Page.navigate'){
     this.request('GET',N,L,c.params.url);this.paused('GET',N,c.params.url);
     this.response('GET',N,L,c.params.url,mode==='navigate_http'?500:200);
     this.loaded(c.params.url,L,{noLoad:mode==='stale_load'});
     this.reply(c,{frameId:F,loaderId:L,...(mode==='navigate_error'?{errorText:'net::ERR_FAILED'}:{})});return;
   }
   if(c.method==='Runtime.evaluate'){
     if(c.params.expression==='SUBMIT'){
       if(mode==='socket_close'){this.emit('close',{});return;}
       if(mode==='no_request'){this.reply(c,{result:{value:true}});return;}
       if(mode==='wrong_context'){this.error(c,-32000,'Cannot find context with specified id');return;}
       if(mode==='fetch_first'){this.paused();this.request();}else{this.request();this.paused();}
       if(mode==='duplicate_post')this.paused('POST','N2');
       if(mode==='continue_hangs'){this.reply(c,{result:{value:true}});return;}
       if(mode==='redirect' || mode==='destroyed' || mode==='redirect_get_error'){
         this.request('GET',N,L,dest,{redirectResponse:{url,status:303,headers:{location:dest}}});
         if(mode!=='redirect_get_error')this.paused('GET',N,dest);
         this.response('GET',N,L,dest);this.loaded(dest);
       }else if(mode==='wrong_loader'){
         this.event('Network.responseReceived',{requestId:N,loaderId:'WRONG',frameId:F,response:{url,status:200}});
       }else if(mode==='wrong_response_session'){
         this.event('Network.responseReceived',{requestId:N,loaderId:L,frameId:F,response:{url,status:200}},'WRONG');this.loaded();
       }else if(mode==='wrong_response_frame'){
         this.event('Network.responseReceived',{requestId:N,loaderId:L,frameId:'WRONG',response:{url,status:200}});this.loaded();
       }else{this.response();this.loaded(url,L,{noLoad:mode==='no_load'});}
       if(mode==='destroyed')this.error(c,-32000,'Execution context was destroyed.');
       else this.reply(c,{result:{value:true}});return;
     }
     if(c.params.expression==='HANG')return;
     if(c.params.expression==='VALUE'){this.reply(c,{result:{value:{safe:true}}});return;}
     if(c.params.expression==='BAD_VALUE'){this.reply(c,{exceptionDetails:{text:'synthetic'},result:{}});return;}
     if(c.params.expression==='HUGE'){this.reply(c,{result:{value:'x'.repeat(1024*1024)}});return;}
     void this.read(c);return;
   }
   this.reply(c);
 }
 async read(c){
   let canceled=0,released=0,aborted=0,fetches=0;
   const location={origin:ORIGIN,href:dest};
   const bytes=mode==='read_size'?new Uint8Array(16385):mode==='read_utf8'?new Uint8Array([0xc3,0x28]):new TextEncoder().encode(mode==='read_json'?'!':'{"mapping_batches":3}');
   const fetch=async (target,options)=>{
     fetches++;assert.equal(target,ORIGIN+'/supplier-mapping/status');assert.equal(options.redirect,'error');assert.equal(options.credentials,'same-origin');
     options.signal.addEventListener('abort',()=>aborted++);
     if(mode!=='read_fabricated'){
       this.request('GET',N,L,target);this.paused('GET',N,target);this.response('GET',N,L,target);
       if(mode==='read_duplicate'){this.request('GET','N2',L,target);this.paused('GET','N2',target);this.response('GET','N2',L,target);}
     }
     if(mode==='read_navigated')location.href=ORIGIN+'/monday-runs';
     let first=true;
     return {url:mode==='read_redirect'?ORIGIN+'/else':target,status:mode==='read_status'?500:200,
       headers:{get:()=>mode==='read_type'?'text/html':'application/json; charset=utf-8'},
       body:{getReader:()=>({read:async()=>{if(first){first=false;return {done:false,value:bytes};}return {done:true};},cancel:async()=>{canceled++;},releaseLock:()=>released++})}};
   };
   try{const value=await vm.runInNewContext(c.params.expression,{location,fetch,AbortController,setTimeout,clearTimeout,TextDecoder});this.reply(c,{result:{value}});}
   catch{this.reply(c,{result:{},exceptionDetails:{text:'refused'}});}
   finally{this.readEvidence={canceled,released,aborted,fetches,href:location.href};}
 }
 close(){this.closed=true;this.emit('close',{});}
}
const socket=new Socket();
if(mode==='bad_port'){socket.url=socket.url.replace(':9222',':99999');assert.throws(()=>new HostedCdpTransport(socket,socket.url));}
else{
 const transport=new HostedCdpTransport(socket,socket.url);
 if(mode==='double_transport')assert.throws(()=>new HostedCdpTransport(socket,socket.url));
 else if(mode.startsWith('transport_')){
   socket.send=function(raw){this.sent.push(JSON.parse(raw));};
   const h=transport.command('Page.enable',{},S);const rejected=assert.rejects(h.promise);const c=socket.sent.at(-1);
   if(mode==='transport_cancel'){h.cancel();socket.reply(c);assert.equal(transport.pending.size,0);}
   else if(mode==='transport_session')socket.message({id:c.id,sessionId:'OTHER',result:{}});
   else if(mode==='transport_no_session')socket.message({id:c.id,result:{}});
   else if(mode==='transport_malformed')socket.emit('message',{data:'{'});
   else if(mode==='transport_socket_error')socket.emit('error',{});
   else throw new Error('unknown transport case');
   await rejected;assert.equal(transport.pending.size,0);
 }else{
   const abort=new AbortController(),page=new HostedPageAdapter(transport,{sessionId:S,frameId:F,targetId:T},abort.signal,{timeoutMs:80});
   if(mode==='wrong_target'||mode==='wrong_frame'||mode==='target_binding'){await assert.rejects(page.start());assert.equal(transport.events.size,1);}
   else{
     await page.start();
     const arm=()=>armSinglePost(page,path,{actor:'owner:staging:01',apply_idempotency_key:'fixture'},crypto.createHash('sha256').update(token).digest('hex'));
     if(['success','redirect','destroyed','fetch_first','delayed_ack'].includes(mode)){
       const mark=page.mark(),gate=arm();let finished=false;const submission=page.submit('SUBMIT').then(()=>{finished=true;});
       if(mode==='delayed_ack'){await tick();assert.equal(finished,false);assert.equal(socket.delayed.length,1);socket.reply(socket.delayed[0]);}
       await submission;gate.verify();gate.close();
       const redirects=['redirect','destroyed'].includes(mode);
       assert.deepEqual(redirects?page.redirectAfter(path,'POST',mark):page.responseAfter(path,'POST',mark),redirects?{status:303,location:dest}:{status:200});
       assert.equal(socket.forwarded.filter(x=>x.startsWith('fetch-POST')).length,1);assert.equal(page.failed,false);
     }else if(['no_request','wrong_context','duplicate_post','continue_error','continue_hangs','wrong_loader','wrong_response_session','wrong_response_frame','no_load','redirect_get_error','socket_close'].includes(mode)){
       const gate=arm();await assert.rejects(page.submit('SUBMIT'));assert.throws(()=>gate.verify());assert.equal(page.failed,true);
       if(mode==='continue_error'||mode==='continue_hangs')assert.ok(socket.denied.includes('fetch-POST-N1'));
     }else if(['default_deny','after_guard_close','throw_guard','promise_guard','denial_race','wrong_route','wrong_session','wrong_request_frame','wrong_body','network_collision'].includes(mode)){
       if(mode==='throw_guard')page.armRequestGuard(()=>{throw new Error('fixture');});
       else if(mode==='promise_guard')page.armRequestGuard(async()=>true);
       else if(mode!=='default_deny'){const gate=arm();if(mode==='after_guard_close')gate.close();}
       if(mode==='denial_race'){socket.paused('POST','BAD',ORIGIN+'/unapproved');socket.paused();assert.equal(socket.forwarded.length,0);for(const c of socket.delayed)socket.reply(c);}
       else if(mode==='wrong_route')socket.paused('POST',N,ORIGIN+'/monday-runs/prepare');
       else if(mode==='wrong_session')socket.event('Fetch.requestPaused',{requestId:'wrong',networkId:N,frameId:F,request:{method:'POST',url,postData:body}},'OTHER');
       else if(mode==='wrong_request_frame')socket.paused('POST',N,url,{frameId:'OTHER'});
       else if(mode==='wrong_body')socket.paused('POST',N,url,{request:{method:'POST',url,postData:body+'&unexpected=1'}});
       else if(mode==='network_collision'){socket.paused('GET',N,dest);await tick();socket.paused();}
       else socket.paused();
       await tick();assert.equal(page.failed,true);assert.equal(socket.forwarded.filter(x=>x.startsWith('fetch-POST')).length,0);assert.ok(socket.denied.length);
     }else if(['navigate','navigate_error','navigate_http','stale_load'].includes(mode)){
       if(mode==='stale_load')socket.event('Page.lifecycleEvent',{frameId:F,loaderId:L,name:'load'});
       if(mode==='navigate')await page.navigate(dest);else await assert.rejects(page.navigate(dest));
     }else if(mode==='stale_response'||mode==='stale_request'){
       socket.request('GET',N,L,dest);
       if(mode==='stale_response'){socket.paused('GET',N,dest);await tick();}
       const mark=page.mark();
       if(mode==='stale_request'){socket.paused('GET',N,dest);await tick();}
       socket.response('GET',N,L,dest);
       assert.equal(page.responseAfter('/price-books/'+batch,'GET',mark),null);
     }else if(mode==='concurrent'){
       const first=page.evaluate('HANG'),firstReject=assert.rejects(first);await assert.rejects(page.evaluate('VALUE'));await firstReject;assert.equal(page.failed,true);
     }else if(mode==='abort'){
       const result=assert.rejects(page.evaluate('HANG'));abort.abort();await result;assert.equal(page.commands.size,0);assert.equal(page.waiters.size,0);
     }else if(['command_timeout','bad_value','large_value'].includes(mode)){
       await assert.rejects(page.evaluate(mode==='command_timeout'?'HANG':mode==='bad_value'?'BAD_VALUE':'HUGE'));assert.equal(transport.pending.size,0);
     }else if(mode.startsWith('read_')){
       if(mode==='read_success'){assert.deepEqual(await page.readJson('/supplier-mapping/status'),{url:ORIGIN+'/supplier-mapping/status',status:200,body:{mapping_batches:3}});assert.equal(socket.readEvidence.href,dest);}
       else await assert.rejects(page.readJson('/supplier-mapping/status'));
       assert.equal(socket.readEvidence.aborted,1);assert.equal(socket.readEvidence.fetches,1);
       if(!['read_redirect','read_status','read_type'].includes(mode)){assert.equal(socket.readEvidence.canceled,1);assert.equal(socket.readEvidence.released,1);}
     }else if(['close','target_alive','auth_required','detached'].includes(mode)){
       if(mode==='auth_required'){socket.event('Fetch.authRequired',{requestId:'auth'});await tick();assert.equal(socket.sent.at(-1).params.authChallengeResponse.response,'CancelAuth');assert.equal(page.failed,true);}
       if(mode==='detached'){socket.event('Target.detachedFromTarget',{sessionId:S},null);assert.equal(page.failed,true);}
       page.close();socket.paused();await tick();assert.equal(socket.forwarded.length,0);assert.ok(socket.denied.length);
       if(mode==='target_alive'){await assert.rejects(page.disposeAfterTargetExit());assert.equal(transport.events.size,1);}
       else{await page.disposeAfterTargetExit();assert.equal(transport.events.size,0);assert.equal(transport.failures.size,0);}
     }else throw new Error('unknown adapter case');
   }
   page.close();await tick();assert.equal(page.commands.size,0);assert.equal(page.waiters.size,0);assert.equal(page.tasks.size,0);
 }
 transport.close();assert.equal(transport.pending.size,0);assert.equal(socket.closed,true);assert.ok([...socket.listeners.values()].every(x=>x.size===0));
}
console.log(JSON.stringify({assertion:'PASS',scope:'SCRIPTED_CDP_PROTOCOL_ONLY'}));
