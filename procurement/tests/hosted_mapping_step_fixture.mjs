// Scripted DOM/request component only; no server, DB, browser or credentials.
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import vm from 'node:vm';
import {runStagingMappingStep,mappingDecisionId} from '../tools/hosted_synthetic_mapping_step.mjs';
import {ORIGIN} from '../tools/hosted_synthetic_apply_step.mjs';
const scenario=process.argv[2],CSRF='_buffalo_staging_csrf',token='S'.repeat(43);
const uuid=n=>`aaaaaaaa-bbbb-4ccc-8ddd-${String(n).padStart(12,'0')}`;
const binding={commit:'a'.repeat(40),tree:'b'.repeat(40),fixture_proof_sha256:'c'.repeat(64),apply_proof_sha256:'d'.repeat(64),
  candidates:[null,'1001','4001','4002','4003','4004','4005'].map((variant_id,i)=>({candidate_id:uuid(i+1),variant_id,
    occurrence_key:variant_id?'synthetic-'+variant_id:'synthetic-unresolved-occurrence-001',vendor:'Synthetic vendor',offer_id:variant_id?String(i+10):null}))};
let forms=[],current=null,phase='list',guard=null,armed=0,closed=0,posts=[],faultReached=false,blocked=false,browserCalls=0;
let decisions=0,selections=0,intaken=false,lastRedirect=null,lastResponse=null;
const decisionIds=new Map();
const location={href:ORIGIN+'/supplier-mapping'};
const fault=name=>{if(scenario===name){faultReached=true;return true;}return false;};
const mutate=(fields,key,value)=>{fields.find(f=>f.name===key).value=value;};
class Form {
 constructor(action,fields){this.action=action;this.method='post';this.enctype='application/x-www-form-urlencoded';this.target='';
   this.fields=Object.entries({...fields,[CSRF]:token}).map(([name,value])=>({name,value}));
   this.elements={namedItem:name=>this.fields.find(f=>f.name===name)};}
 get isConnected(){return forms.includes(this);}
 getAttribute(name){return this[name];}
 requestSubmit(){
   const url=new URL(this.action||location.href,location.href).href,path=new URL(url).pathname;
   let postData=new URLSearchParams(this.fields.map(f=>[f.name,f.value])).toString();
   const final=phase==='decision_preview',selected=phase==='selection_preview';
   if(final&&fault('request_duplicate'))postData+='&confirm=CONFIRM';
   if(final&&fault('request_csrf'))postData=postData.replace(token,'T'.repeat(43));
   const request={url,method:'POST',postData};
   if(final&&fault('request_route'))request.url=ORIGIN+'/auth/logout';
   assert(guard);const allowed=guard.check(request);guard.receipts.push(allowed);
   if(!allowed){blocked=true;throw new Error('actual mapping request blocked');}
   posts.push({path,fields:Object.fromEntries(new URLSearchParams(postData))});
   if(final&&fault('request_repeat')){const allowedAgain=guard.check(request);guard.receipts.push(allowedAgain);assert.equal(allowedAgain,false);blocked=true;throw new Error('actual mapping request blocked');}
   lastRedirect=null;lastResponse={status:200};
   const values=Object.fromEntries(this.fields.filter(f=>f.name!==CSRF).map(f=>[f.name,f.value]));
   if(path.endsWith('/intake')){intaken=true;phase='list';forms=[];lastRedirect={status:303,location:'/supplier-mapping'};location.href=ORIGIN+'/supplier-mapping';}
   else if(final || selected){
     if(final){decisions++;decisionIds.set(current.candidate_id,mappingDecisionId(values.idempotency_key));}else selections++;
     lastRedirect={status:303,location:'/supplier-mapping/'+current.candidate_id};
     if(fault('redirect_foreign'))lastRedirect.location='https://foreign.invalid/supplier-mapping/'+current.candidate_id;
     if(fault('redirect_status'))lastRedirect.status=307;
     detail();
   }else{
     phase=path.endsWith('/selection')?'selection_preview':'decision_preview';location.href=url;
     if(phase==='selection_preview')values.mapping_decision_id=decisionIds.get(current.candidate_id);
     values.expected_preview_sha256='e'.repeat(64);values.confirm='CONFIRM';
     forms=[new Form('',values)];
     if(phase==='decision_preview'){
       for(const [test,key,val] of [['preview_actor','actor','spoof'],['preview_reason','reason','changed'],['preview_offer','existing_offer_id','999'],['preview_idempotency','idempotency_key',uuid(999)],['preview_hash','expected_preview_sha256','bad'],['preview_confirm','confirm','YES']]){
         if(fault(test)){if(key==='actor')forms[0].fields.push({name:key,value:val});else mutate(forms[0].fields,key,val);}
       }
       if(fault('preview_duplicate'))forms[0].fields.push({name:'confirm',value:'CONFIRM'});
       if(fault('preview_http'))lastResponse.status=409;
       if(fault('preview_legacy'))forms[0].fields.push({name:'review_token',value:'synthetic'});
     }else{
       if(fault('selection_mapping_id'))mutate(forms[0].fields,'mapping_decision_id','bad');
       if(fault('selection_foreign_mapping'))mutate(forms[0].fields,'mapping_decision_id',uuid(999));
       if(fault('selection_clear'))mutate(forms[0].fields,'selection_action','CLEAR');
       if(fault('selection_date'))mutate(forms[0].fields,'effective_from','2026-11-01');
     }
   }
 }
}
function detail(){
 phase='detail';location.href=ORIGIN+'/supplier-mapping/'+current.candidate_id;
 const decision=new Form(location.href+'/decision',{idempotency_key:uuid(200+Number(current.candidate_id.slice(-3))),action:'DEFER',offer_link_kind:'',existing_offer_id:'',reason:''});
 for(const [key,values] of Object.entries({action:['DEFER','APPROVE_MAPPING'],offer_link_kind:['','LINKED_EXISTING'],existing_offer_id:['',current.offer_id].filter(v=>v!==null)})){
   decision.elements.namedItem(key).options=values.map(value=>({value}));
 }
 if(current.variant_id==='1001'&&fault('missing_exact_offer'))decision.elements.namedItem('existing_offer_id').options=[{value:''},{value:'999'}];
 forms=[decision];
 if(current.variant_id&&decisions>=2)forms.push(new Form(location.href+'/selection',{idempotency_key:uuid(300+Number(current.offer_id)),selection_action:'SELECT',effective_from:'2026-10-27',reason:''}));
 if(current.variant_id===null&&fault('detail_wrong_url'))location.href=ORIGIN+'/supplier-mapping/'+uuid(999);
}
const rows=()=>binding.candidates.map(item=>{
 const cells=[item.occurrence_key,item.vendor,item.variant_id||'—','VALUE','REGULAR','PENDING'];
 let href='/supplier-mapping/'+item.candidate_id;
 if(item.variant_id==='1001'){
   if(fault('row_variant'))cells[2]='10010';
   if(fault('row_occurrence'))cells[0]='changed';
   if(fault('row_vendor'))cells[1]='foreign';
   if(fault('row_predecided'))cells[5]='APPROVE_MAPPING';
   if(fault('row_route'))href='https://foreign.invalid'+href;
   if(fault('row_duplicate'))href='/supplier-mapping/'+binding.candidates[0].candidate_id;
 }
 return {querySelector:()=>({getAttribute:()=>href}),querySelectorAll:()=>cells.map(textContent=>({textContent}))};
});
const document={get forms(){return forms;},querySelectorAll(selector){assert.equal(selector,'tbody tr');return rows();}};
class FormData {constructor(form){this.fields=form.fields.map(f=>[f.name,f.value]);}*[Symbol.iterator](){yield*this.fields;}}
const page={
 async navigate(url){browserCalls++;assert(url.startsWith(ORIGIN+'/supplier-mapping'));location.href=url;
   if(url===ORIGIN+'/supplier-mapping'){phase='list';forms=[new Form('/supplier-mapping/intake',{})];}
   else{current=binding.candidates.find(x=>url.endsWith('/'+x.candidate_id));assert(current);detail();}},
 async evaluate(program){browserCalls++;return await vm.runInNewContext(program,{document,location,FormData,HTMLFormElement:Form,URL,TextEncoder,Uint8Array,crypto:crypto.webcrypto});},
 async submit(program){
   if(phase==='decision_preview'){
     if(fault('changed_csrf'))mutate(forms[0].fields,CSRF,'T'.repeat(43));
     if(fault('changed_action'))forms[0].action='https://foreign.invalid/changed';
     if(fault('changed_target'))forms[0].target='_blank';
   }
   return await this.evaluate(program);
 },
 mark(){return posts.length;},responseAfter(path,method,mark){assert(posts.length>mark);return lastResponse;},
 redirectAfter(path,method,mark){assert(posts.length>mark);return lastRedirect;},
 armRequestGuard(check){assert.equal(guard,null);armed++;guard={check,receipts:[]};const owned=guard;
   return {matches:()=>owned.receipts,close(){assert.equal(guard,owned);guard=null;closed++;}};},
 async readJson(path){assert.equal(path,'/supplier-mapping/status');browserCalls++;
   const body={review_batch_count:intaken?3:0,candidate_count:intaken?7:0,decision_count:decisions,
     selection_event_count:selections,selection_head_count:selections,authority:'SHADOW_ONLY',recommendation_cutover_enabled:false,offer_activation_enabled:false};
   if(!intaken&&fault('preseeded'))body.decision_count=7;
   if(!intaken&&fault('authority'))body.recommendation_cutover_enabled=true;
   if(intaken&&decisions===0&&fault('intake_count'))body.candidate_count=8;
   if(decisions===7&&selections===5&&fault('final_count'))body.selection_head_count=4;
   return {status:200,url:ORIGIN+path,body};
 }
};
if(fault('binding_variant'))binding.candidates[1].variant_id='10010';
if(fault('binding_duplicate'))binding.candidates[1].candidate_id=binding.candidates[0].candidate_id;
if(fault('binding_source'))binding.commit='bad';
if(scenario.startsWith('binding_array:')){faultReached=true;const key=scenario.slice(14);if(key==='candidate_id')binding.candidates[0][key]=[binding.candidates[0][key]];else binding[key]=[binding[key]];}
if(fault('binding_getter'))Object.defineProperty(binding,'tree',{enumerable:true,get(){throw new Error('getter must not execute');}});
let result,error;
try{result=await runStagingMappingStep(page,binding);}catch(e){
 if(e.name==='AssertionError'||!/^(staging mapping component refused|staging APPLY component refused|mapping |actual mapping request blocked)/.test(e.message))throw e;
 error=e.message;
}
assert.equal(guard,null);assert.equal(armed,closed);
if(scenario==='success'){
 assert.equal(error,undefined);assert.equal(posts.length,25);assert.equal(decisions,7);assert.equal(selections,5);
 assert.equal(result.observations.find(x=>x.variant_id==='4003').selection,null);
 assert.equal(result.observations.find(x=>x.variant_id===null).action,'DEFER');
 assert.equal(result.execution_authority,false);assert(!JSON.stringify(result).includes(token));
}else{
 assert(error,scenario+' did not refuse');assert(faultReached,scenario+' fault not reached');
 if(scenario.startsWith('binding_')){assert.equal(browserCalls,0);assert.equal(posts.length,0);}
 else if(['preseeded','authority'].includes(scenario))assert.equal(posts.length,0);
 else if(scenario.startsWith('row_')||['intake_count','detail_wrong_url'].includes(scenario))assert.equal(posts.length,1);
 else if(scenario==='missing_exact_offer')assert.equal(posts.length,3);
 else if(scenario.startsWith('selection_'))assert.equal(posts.length,6);
 else if(scenario==='final_count')assert.equal(posts.length,25);
 else if(scenario.startsWith('redirect_')||scenario==='request_repeat')assert.equal(posts.length,3);
 else assert.equal(posts.length,2);
 if(scenario.startsWith('request_'))assert(blocked);
}
console.log(JSON.stringify({scenario,assertion:'PASS',forwarded_posts:posts.length,guards_closed:closed,scope:'SCRIPTED_DOM_AND_REQUEST_COMPONENT_ONLY'}));
