// Real preparation programs against a causal scripted DOM/request adapter only.
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import vm from 'node:vm';
import {ORIGIN} from '../tools/hosted_synthetic_apply_step.mjs';
import {runStagingPreparationStep} from '../tools/hosted_synthetic_prepare_step.mjs';
const scenario=process.argv[2],STALE='00000000-0000-4000-8000-000000000901',runId='aaaaaaaa-bbbb-4ccc-8ddd-000000000001';
const CSRF='_buffalo_staging_csrf',token='S'.repeat(43),reason='Synthetic retired forecast method requires V2 re-preparation';
const b={commit:'a'.repeat(40),tree:'b'.repeat(40),fixture_proof_sha256:'c'.repeat(64),mapping_proof_sha256:'d'.repeat(64),
 stale_input_fingerprint:'e'.repeat(64),principal_ref:'owner:staging:01',price_batch_id:'aaaaaaaa-bbbb-4ccc-8ddd-000000000002',
 vendors:{southern:'aaaaaaaa-bbbb-4ccc-8ddd-000000000003',western:'aaaaaaaa-bbbb-4ccc-8ddd-000000000004'}};
let phase='stale',forms=[],guard=null,armed=0,closed=0,posts=[],response=null,redirect=null,faultReached=false,calls=0,blocked=false;
const location={href:ORIGIN+'/monday-runs/'+STALE};
const fault=name=>{if(scenario===name){faultReached=true;return true;}return false;};
const textNode=textContent=>({textContent,childNodes:[{textContent}]});
function forecast(variant){
 const h=variant==='1001'?17:3,counts=h===17?[22,22,18]:[36,36,32];
 const e={contract:'BUFFALO_DEVELOPMENT_FORECAST_EVIDENCE_V2',method_version:'DEVELOPMENT_ROLLING_ORIGIN_V2',status:'READY',commercial_authority:false,production_activation:false,
 selected_model:'SYNTHETIC',xyz_class:'X',abc_class:'NOT_CONFIGURED',confidence:'LOW',availability:{proven_in_stock_days:0,unknown_days:138,protection_qualification:'LIMITED'},
 protection:{status:'CALCULATED_LIMITED_AVAILABILITY',availability_qualification:'LIMITED'},reason_codes:['AVAILABILITY_COVERAGE_LIMITS_PROTECTION'],sha256:'f'.repeat(64),
 point_forecast_units:variant==='1001'?'34.0000':'1.0000',protection_units:'0.0000',target_units:variant==='1001'?'34.0000':'1.0000',
 horizon_days:h,history_start:'2026-05-20',history_end:'2026-10-04',
 policy:{source_sha256:'fee2e91e14565a835f1d69c27ff547ccdda5c22c6bc43a2bb003c2b78f190b52',canonical_sha256:'ff62b1d313a18a907e811e8722431efee2349fc95a31e08ea5843ca2664ca858'},
 confidence_policy:{metric:'EVALUATION_WAPE',boundary_rule:'UPPER_BOUNDS_INCLUSIVE',high_max_inclusive:'0.200000',medium_max_inclusive:'0.500000'},
 protection_calendar:{vendor_id:variant==='1001'?b.vendors.southern:b.vendors.western,basis:'ANCHORED_REVIEW_SUBMISSION_DELIVERY_SCHEDULE_V2',business_date:'2026-10-05',timezone_name:'America/New_York',horizon_days:h,
 current_order_receipt_date:'2026-10-08',next_submission_date:h===17?'2026-10-19':'2026-10-07',next_order_receipt_date:h===17?'2026-10-22':'2026-10-08',
 schedule:{source_sha256:'1cb3edf5e82f2f01cb0a4c972d6140f97e767df67468373b245d6dd414ad8640',canonical_sha256:'2c8b09152d3005b227a93ba700a8c08eec68433bf8f39e44c85353af34639c39'},
 interval_semantics:'HALF_OPEN_[EVALUATION_DATE,NEXT_RECEIPT)_RECEIPT_START_OF_DAY'},
 origin_windows:Object.fromEntries(['selection','calibration','evaluation'].map((key,index)=>[key,{planned_origin_count:counts[index],usable_origin_count:counts[index],censored_origin_count:0,
 overlapping_targets:true,independent_samples:false,target_interval_semantics:'HALF_OPEN_[ORIGIN,ORIGIN_PLUS_HORIZON)'}]))};
 if(variant==='1001'){
   if(fault('forecast_authority'))e.commercial_authority=true;
   if(fault('forecast_availability'))e.availability.unknown_days=84;
   if(fault('forecast_calendar'))e.protection_calendar.next_order_receipt_date='2026-10-15';
   if(fault('forecast_policy'))e.policy.source_sha256='0'.repeat(64);
   if(fault('forecast_units'))e.target_units='35.0000';
   if(fault('forecast_origins'))e.origin_windows.calibration.usable_origin_count--;
   if(fault('forecast_independence'))e.origin_windows.evaluation.independent_samples=true;
   if(fault('forecast_confidence'))e.confidence='HIGH';
   if(fault('forecast_qualification'))e.protection.availability_qualification='FULL';
   if(fault('forecast_vendor'))e.protection_calendar.vendor_id=b.vendors.western;
   if(fault('forecast_conflicting_alias'))e.protection_calendar.next_receipt_date='2026-11-01';
   if(fault('forecast_alias_only')){e.protection_calendar.next_receipt_date=e.protection_calendar.next_order_receipt_date;delete e.protection_calendar.next_order_receipt_date;}
   if(fault('forecast_timezone'))e.protection_calendar.timezone_name='UTC';
   if(fault('forecast_business_date'))e.protection_calendar.business_date='2026-10-06';
   if(fault('forecast_basis'))e.protection_calendar.basis='OLD_V1';
   if(fault('forecast_horizon'))e.protection_calendar.horizon_days=10;
 }
 return e;
}
class Form {
 constructor(action,values,variant=null){this.action=action;this.method='post';this.enctype='application/x-www-form-urlencoded';this.target='';this.variant=variant;
   this.fields=Object.entries({...values,[CSRF]:token}).map(([name,value])=>({name,value}));this.elements={namedItem:name=>this.fields.find(x=>x.name===name)};}
 get isConnected(){return forms.includes(this);}
 getAttribute(name){return this[name];}
 closest(){const variant=this.variant;
   return {cells:[{...textNode(variant),textContent:variant+'Synthetic title'},textNode('vendor'),textNode(variant==='4003'?(fault('blocker_reason')?'UNKNOWN':'ROUTINE_SELECTED_OFFER_HEAD_REQUIRED'):'LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED')],
     querySelectorAll:selector=>{
       if(selector==='pre.development-forecast-evidence-json'){const nodes=[textNode(JSON.stringify(forecast(variant)))];if(variant==='1001'&&fault('forecast_duplicate'))nodes.push(nodes[0]);return nodes;}
       assert.equal(selector,'.development-forecast-evidence');
       let disclosure='LOCAL SYNTHETIC DEVELOPMENT ONLY — NO COMMERCIAL AUTHORITY empirical full-horizon protection CALCULATED_LIMITED_AVAILABILITY availability qualification LIMITED confidence LOW';
       if(variant==='1001'&&fault('forecast_warning'))disclosure=disclosure.replace('NO COMMERCIAL AUTHORITY','');
       if(variant==='1001'&&fault('forecast_visible_qualification'))disclosure=disclosure.replace('availability qualification LIMITED','');
       return [textNode(disclosure)];
     }};}
 requestSubmit(){
   const url=new URL(this.action||location.href,location.href).href,path=new URL(url).pathname;
   let postData=new URLSearchParams(this.fields.map(x=>[x.name,x.value])).toString();
   if(phase==='retire_preview'&&fault('request_duplicate'))postData+='&reason=changed';
   const request={method:'POST',url,postData};
   if(phase==='retire_preview'&&fault('request_route'))request.url=ORIGIN+'/auth/logout';
   assert(guard);const allowed=guard.check(request);guard.receipts.push(allowed);
   if(!allowed){blocked=true;throw new Error('actual preparation request blocked');}
   posts.push(path);response={status:200};redirect=null;
   if(phase==='retire_preview'&&fault('request_repeat')){const again=guard.check(request);guard.receipts.push(again);assert.equal(again,false);blocked=true;throw new Error('actual preparation request blocked');}
   if(phase==='stale'){
     phase='retire_preview';location.href=url;forms=[new Form('',{reason,expected_confirmation_sha256:'1'.repeat(64)})];
     if(fault('confirmation_hash'))forms[0].elements.namedItem('expected_confirmation_sha256').value='2'.repeat(64);
     if(fault('retire_http'))response.status=409;
   }else if(phase==='retire_preview'){
     phase='retired';location.href=ORIGIN+'/monday-runs/'+STALE;forms=[];redirect={status:303,location:'/monday-runs/'+STALE};
   }else if(phase==='list'){
     phase='prepared';location.href=ORIGIN+'/monday-runs/'+runId;redirect={status:303,location:'../monday-runs/'+runId};
     if(fault('prepare_redirect'))redirect.location='https://foreign.invalid/monday-runs/'+runId;
     forms=['1001','4001','4002','4005'].map((v,i)=>new Form(runId+'/recommendations/'+(i+1)+'/review',{approved_cases:v==='1001'?'6':v==='4005'?'0':'1',approved_loose_units:'0'},v));
     if(fault('prepared_quantity'))forms[0].elements.namedItem('approved_cases').value='3';
     if(fault('prepared_route'))forms[0].action='https://foreign.invalid/monday-runs/'+runId+'/recommendations/1/review';
     if(fault('prepared_duplicate_id'))forms[1].action=forms[0].action;
     forms.push(...['4003','4004'].map((v,i)=>new Form(runId+'/blockers/'+(i+1)+'/exclude',{},v)));
   }else throw new Error('unexpected fixture phase');
 }
}
function states(){
 let values=phase==='prepared'?['RUNNING','AWAITING_REVIEW','DEVELOPMENT_ROLLING_ORIGIN_V2']:phase==='retired'?['FAILED','FAILED','EMERGENCY_TRANSPARENT_V1']:['RUNNING','PREPARING','EMERGENCY_TRANSPARENT_V1'];
 if(phase==='stale'&&fault('stale_state'))values[1]='PACKET_BUILT';
 if(phase==='retired'&&fault('retired_state'))values[0]='RUNNING';
 if(phase==='prepared'&&fault('prepared_method'))values[2]='DEVELOPMENT_ROLLING_ORIGIN_V1';
 return values;
}
function retirementEntries(){
 const e={'Run':STALE,'Business date':'2026-10-05','Frozen fingerprint':b.stale_input_fingerprint,'Retired method':'EMERGENCY_TRANSPARENT_V1','Transition':'RUNNING/PREPARING → FAILED/FAILED',
   'Purchase orders / artifacts':'0 / 0','Authenticated actor':b.principal_ref,'Reason':reason,'Confirmation SHA-256':'1'.repeat(64)};
 if(scenario.startsWith('retirement:')){faultReached=true;e[scenario.slice(11)]='changed';}
 const entries=Object.entries(e);
 if(fault('retirement_duplicate'))entries.push(['Run',STALE]);
 return entries.map(([key,value])=>({...textNode(key),nextElementSibling:textNode(value)}));
}
const document={get forms(){return forms;},get body(){return {innerText:'TEST DATA — NOT FOR ORDERING business date: 2026-10-05'};},
 querySelector(selector){assert.equal(selector,'h1');return {textContent:phase==='retire_preview'?'Confirm exact V1 retirement':'Monday run '+(phase==='prepared'?runId:STALE),
   nextElementSibling:{querySelectorAll:()=>states().map(textNode),querySelector:()=>textNode(phase==='prepared'?'2'.repeat(64):b.stale_input_fingerprint)}};},
 querySelectorAll(selector){
   if(selector==='a[href*="/artifacts/"]')return phase==='stale'&&fault('stale_artifact')?[{}]:[];
   if(selector==='h3')return [];
   if(selector==='dl > dt')return retirementEntries();
   if(selector==='pre.development-forecast-evidence-json')return Array(fault('forecast_orphan')?5:4).fill({});
   if(selector.startsWith('form[action*=')){
     const fragment=selector.includes('/recommendations/')?'/recommendations/':'/blockers/';
     const selected=forms.filter(f=>f.action.includes(fragment));
     if(fragment==='/blockers/')return selected.map(f=>{const base=f.closest.bind(f);f.closest=()=>{const r=base();r.cells[0].textContent=f.variant;return r;};return f;});
     return selected;
   }
   throw new Error('unexpected fixture selector');
 }};
class FormData {constructor(form){this.pairs=form.fields.map(x=>[x.name,x.value]);}*[Symbol.iterator](){yield*this.pairs;}}
const page={
 async navigate(url){calls++;location.href=url;
   if(url.endsWith('/'+STALE)){phase='stale';forms=[new Form(STALE+'/retire-stale-forecast',{reason:''})];}
   else {assert.equal(url,ORIGIN+'/monday-runs');phase='list';forms=[new Form('monday-runs/prepare',{business_date:'',idempotency_key:'',variant_ids:'',actor:'browser-value-ignored'})];}},
 async evaluate(program){calls++;return await vm.runInNewContext(program,{document,location,FormData,HTMLFormElement:Form,URL,TextEncoder,Uint8Array,crypto:crypto.webcrypto});},
 async submit(program){if(phase==='retire_preview'&&fault('changed_csrf'))forms[0].elements.namedItem(CSRF).value='T'.repeat(43);return await this.evaluate(program);},
 mark(){return posts.length;},responseAfter(path,method,mark){assert(posts.length>mark);return response;},redirectAfter(path,method,mark){assert(posts.length>mark);return redirect;},
 armRequestGuard(check){assert.equal(guard,null);armed++;guard={check,receipts:[]};const owned=guard;return {matches:()=>owned.receipts,close(){assert.equal(guard,owned);guard=null;closed++;}};},
 async readJson(path){calls++;return {url:ORIGIN+path,status:200,body:{review_batch_count:3,candidate_count:7,decision_count:fault('mapping_incomplete')?6:7,selection_head_count:5,selection_event_count:5,authority:'SHADOW_ONLY',recommendation_cutover_enabled:false,offer_activation_enabled:false}};}
};
if(fault('binding_array'))b.commit=[b.commit];
if(fault('binding_getter'))Object.defineProperty(b,'tree',{enumerable:true,get(){throw new Error('getter must not execute');}});
let result,error;
try{result=await runStagingPreparationStep(page,b);}catch(e){if(e.name==='AssertionError'||!/^(staging (preparation|mapping|APPLY) component refused|mapping |preparation forecast |actual preparation request blocked)/.test(e.message))throw e;error=e.message;}
assert.equal(guard,null);assert.equal(armed,closed);
if(scenario==='success'){
 assert.equal(error,undefined);assert.equal(posts.length,3);assert.equal(result.review_binding.run_id,runId);assert.equal(result.review_binding.recommendations.length,4);assert.equal(result.review_binding.blockers.length,2);
 assert.equal(result.observation.execution_authority,false);assert(!JSON.stringify(result).includes(token));
 assert.equal(result.observation.principal_ref,b.principal_ref);assert.equal(result.observation.price_batch_id,b.price_batch_id);assert.deepEqual(result.observation.vendors,b.vendors);
 assert.equal(result.review_binding.preparation_proof_sha256,crypto.createHash('sha256').update(JSON.stringify(result.observation)).digest('hex'));
}else{
 assert(error,scenario+' did not refuse');assert(faultReached,scenario+' fault not reached');
 if(scenario.startsWith('binding_')){assert.equal(calls,0);assert.equal(posts.length,0);}
 else if(scenario.startsWith('stale_')||scenario==='mapping_incomplete')assert.equal(posts.length,0);
 else if(scenario==='retired_state'||scenario==='request_repeat')assert.equal(posts.length,2);
 else if(scenario.startsWith('forecast_')||scenario.startsWith('prepared_')||scenario==='prepare_redirect'||scenario==='blocker_reason')assert.equal(posts.length,3);
 else assert.equal(posts.length,1);
 if(scenario.startsWith('request_'))assert(blocked);
}
console.log(JSON.stringify({scenario,assertion:'PASS',forwarded_posts:posts.length,guards_closed:closed,scope:'SCRIPTED_DOM_AND_REQUEST_COMPONENT_ONLY'}));
