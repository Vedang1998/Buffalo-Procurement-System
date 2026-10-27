// Scripted CDP/browser-DOM protocol only; no actual download, file or credentials.
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {HostedCdpTransport,HostedPageAdapter} from '../tools/hosted_synthetic_page_adapter.mjs';
import {HostedDownloadCollector} from '../tools/hosted_synthetic_download.mjs';
import {ORIGIN} from '../tools/hosted_synthetic_apply_step.mjs';
const mode=process.argv[2],run='00000000-0000-4000-8000-000000000001',guid='00000000-0000-4000-8000-000000000002';
const S='S',F='F',T='T',L='L',N='N',url=ORIGIN+'/monday-runs/'+run+'/artifacts/42';
const filename='monday-'+run+'-42.internal.csv';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
class Socket{
 constructor(){this.url='ws://127.0.0.1:9222/devtools/browser/abcdefghijklmnop';this.readyState=1;this.listeners=new Map();this.sent=[];this.forwarded=0;this.denied=0;this.clicked=0;}
 addEventListener(k,cb){if(!this.listeners.has(k))this.listeners.set(k,new Set());this.listeners.get(k).add(cb);}
 removeEventListener(k,cb){this.listeners.get(k)?.delete(cb);}
 emit(k,v){for(const cb of [...this.listeners.get(k)||[]])cb(v);}
 message(m){this.emit('message',{data:JSON.stringify(m)});}
 event(method,params,sessionId=S){this.message({method,params,...(sessionId?{sessionId}:{})});}
 reply(c,result={}){this.message({id:c.id,...(c.sessionId?{sessionId:c.sessionId}:{}),result});}
 begin(extra={}){this.event('Browser.downloadWillBegin',{guid,frameId:F,url,suggestedFilename:filename,...extra},mode==='browser_session'?S:null);}
 progress(extra={}){this.event('Browser.downloadProgress',{guid,totalBytes:10,receivedBytes:10,state:'completed',...extra},null);}
 startDownload(){
   this.clicked++;
   if(mode==='old_transfer_before_get'){this.begin();this.progress();}
   if(mode!=='no_network'){
     this.event('Network.requestWillBeSent',{requestId:N,loaderId:L,frameId:F,request:{method:'GET',url}});
     this.event('Fetch.requestPaused',{requestId:'fetch1',networkId:N,frameId:F,request:{method:'GET',url}});
     if(mode==='duplicate_get')this.event('Fetch.requestPaused',{requestId:'fetch2',networkId:'N2',frameId:F,request:{method:'GET',url}});
     const headers={'Content-Type':'text/csv; charset=utf-8','Content-Disposition':'attachment; filename='+filename,'Cache-Control':'no-store'};
     if(mode==='wrong_type')headers['Content-Type']='text/html';
     if(mode==='wrong_filename_header')headers['Content-Disposition']='attachment; filename="foreign.csv"';
     if(mode==='cacheable')headers['Cache-Control']='public';
     if(mode==='duplicate_header')headers['content-type']=headers['Content-Type'];
     const respond=()=>this.event('Network.responseReceived',{requestId:N,loaderId:L,frameId:F,response:{url,status:mode==='http_error'?500:200,headers}});
     if(mode==='late_response')setImmediate(respond);else respond();
   }
   if(mode==='no_begin')return;
   if(mode==='unknown_progress'){this.progress();return;}
   this.begin(mode==='wrong_frame'?{frameId:'OTHER'}:mode==='wrong_url'?{url:ORIGIN+'/unrelated'}:mode==='wrong_name'?{suggestedFilename:'foreign.csv'}:{});
   if(mode==='extra_begin')this.begin({guid:'00000000-0000-4000-8000-000000000003'});
   if(['timeout','abort','abort_context','concurrent','socket_close'].includes(mode)){this.progress({receivedBytes:2,state:'inProgress'});return;}
   if(mode==='decrease_received'){this.progress({receivedBytes:8,state:'inProgress'});this.progress({receivedBytes:7,state:'inProgress'});return;}
   if(mode==='decrease_total'){this.progress({receivedBytes:2,state:'inProgress'});this.progress({totalBytes:9,receivedBytes:3,state:'inProgress'});return;}
   if(mode==='zero_total')this.progress({totalBytes:0,receivedBytes:2,state:'inProgress'});
   const changes={canceled:{state:'canceled'},wrong_guid:{guid:'00000000-0000-4000-8000-000000000003'},negative:{receivedBytes:-1},fraction:{receivedBytes:2.5},nonfinite:{totalBytes:Infinity},oversize:{receivedBytes:11,totalBytes:11},short_complete:{receivedBytes:9},path_ignored:{filePath:'/unrelated/do-not-read'}};
   this.progress(changes[mode]||{});
   if(mode==='completed_then_canceled')this.progress({state:'canceled'});
 }
 send(raw){
   const c=JSON.parse(raw);this.sent.push(c);
   if(c.method==='Target.getTargetInfo'){this.reply(c,{targetInfo:{targetId:T,type:'page',attached:true,...(mode==='abort_context'?{browserContextId:'context-1'}:{})}});return;}
   if(c.method==='Page.getFrameTree'){this.reply(c,{frameTree:{frame:{id:F}}});return;}
   if(c.method==='Fetch.continueRequest'){this.forwarded++;if(mode==='late_continue_ack')setImmediate(()=>this.reply(c));else this.reply(c);return;}
   if(c.method==='Fetch.failRequest'){this.denied++;this.reply(c);return;}
   if(c.method==='Runtime.evaluate'){
     const anchor={href:url,isConnected:mode!=='disconnected',target:mode==='blank_target'?'_blank':'',hasAttribute:()=>mode==='download_attr',click:()=>this.startDownload()};
     const document={querySelectorAll:()=>mode==='duplicate_anchor'?[anchor,anchor]:[anchor]};
     const location={href:mode==='wrong_page'?ORIGIN+'/monday-runs':ORIGIN+'/monday-runs/'+run};
     try{const value=vm.runInNewContext(c.params.expression,{document,location});if(mode==='delayed_evaluate')setImmediate(()=>this.reply(c,{result:{value}}));else this.reply(c,{result:{value}});}
     catch{this.reply(c,{exceptionDetails:{text:'refused'},result:{}});}return;
   }
   this.reply(c);
 }
 close(){this.emit('close',{});}
}
const socket=new Socket(),transport=new HostedCdpTransport(socket,socket.url),page=new HostedPageAdapter(transport,{sessionId:S,frameId:F,targetId:T},null,{timeoutMs:100});
await page.start();const signal=new AbortController(),collector=new HostedDownloadCollector(page,signal.signal,{timeoutMs:80});
const b={run_id:run,artifact_id:'42',kind:'VENDOR_INTERNAL_CSV',size_bytes:10};
if(['success','delayed_evaluate','path_ignored','zero_total','late_extra','late_continue_ack','late_response'].includes(mode)){
 const result=await collector.collect(b);assert.equal(result.guid,guid);assert.equal(result.file_bytes,'NOT_READ');assert.equal(result.size_bytes,10);assert.equal(result.execution_authority,false);
 assert.equal(result.scope,'PROVISIONAL_BROWSER_TRANSFER_OBSERVATION_ONLY');assert.equal(socket.clicked,1);assert.equal(socket.forwarded,1);assert.equal(collector.failed,false);
 if(mode==='late_extra'){socket.begin({guid:'00000000-0000-4000-8000-000000000003'});assert.equal(collector.failed,true);await assert.rejects(collector.collect({...b,artifact_id:'43'}));}
}else if(['bad_binding','binding_getter','oversize_binding','replay_url','max_count','default_artifact'].includes(mode)){
 if(mode==='bad_binding')await assert.rejects(collector.collect({...b,run_id:3}));
 if(mode==='binding_getter'){const value={...b};Object.defineProperty(value,'size_bytes',{get(){throw new Error('untrusted getter');},enumerable:true});await assert.rejects(collector.collect(value));}
 if(mode==='oversize_binding')await assert.rejects(collector.collect({...b,size_bytes:1024*1024+1}));
 if(mode==='replay_url'){collector.urls.add(url);await assert.rejects(collector.collect(b));}
 if(mode==='max_count'){collector.observations.push({}, {}, {});await assert.rejects(collector.collect(b));}
 if(mode==='default_artifact'){socket.event('Fetch.requestPaused',{requestId:'unexpected',networkId:N,frameId:F,request:{method:'GET',url}});await tick();assert.equal(socket.denied,1);}
 assert.equal(socket.clicked,0);assert.equal(socket.forwarded,0);
}else{
 const p=collector.collect(b),rejected=assert.rejects(p);
 if(['abort','abort_context','concurrent','socket_close'].includes(mode)){
   await tick();
   if(mode==='abort'||mode==='abort_context')signal.abort();
   else if(mode==='concurrent')await assert.rejects(collector.collect({...b,artifact_id:'43'}));
   else socket.emit('close',{});
 }
 await rejected;assert.equal(collector.failed,true);
 if(['abort','abort_context','concurrent','canceled','wrong_name'].includes(mode))assert.equal(socket.sent.filter(c=>c.method==='Browser.cancelDownload' && c.params.guid===guid).length,1);
 if(mode==='abort_context')assert.equal(socket.sent.find(c=>c.method==='Browser.cancelDownload').params.browserContextId,'context-1');
 if(['wrong_frame','wrong_url','unknown_progress'].includes(mode))assert.equal(socket.sent.filter(c=>c.method==='Browser.cancelDownload').length,0);
 if(['wrong_page','duplicate_anchor','disconnected','blank_target','download_attr'].includes(mode))assert.equal(socket.clicked,0);
}
assert.throws(()=>collector.close());page.close();transport.close();collector.close();await tick();
assert.equal(collector.waiters.size,0);assert.equal(page.commands.size,0);assert.equal(page.tasks.size,0);assert.equal(transport.pending.size,0);
assert.ok([...socket.listeners.values()].every(set=>set.size===0));
console.log(JSON.stringify({assertion:'PASS',scope:'SCRIPTED_CDP_DOWNLOAD_ONLY'}));
