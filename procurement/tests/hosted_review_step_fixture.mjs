// Actual transition/DOM programs with a scripted adapter, never browser proof.
import assert from 'node:assert/strict';
import vm from 'node:vm';
import crypto from 'node:crypto';
import {ORIGIN} from '../tools/hosted_synthetic_apply_step.mjs';
import {runStagingReviewDraftStep} from '../tools/hosted_synthetic_review_step.mjs';
const scenario=process.argv[2],token='S'.repeat(43),CSRF='_buffalo_staging_csrf';
const uuid=n=>`aaaaaaaa-bbbb-4ccc-8ddd-${String(n).padStart(12,'0')}`;
const b={commit:'a'.repeat(40),tree:'b'.repeat(40),preparation_proof_sha256:'c'.repeat(64),run_id:uuid(1),input_fingerprint:'d'.repeat(64),
 principal_ref:'owner:staging:01',price_batch_id:uuid(2),recommendations:['1001','4001','4002','4005'].map((variant_id,i)=>({variant_id,recommendation_id:String(i+1)})),
 blockers:['4003','4004'].map((variant_id,i)=>({variant_id,exception_id:String(i+1)})),vendors:{southern:uuid(3),western:uuid(4)}};
const detail='/monday-runs/'+b.run_id,location={href:ORIGIN+detail};
let phase='run',forms=[],current=null,guard=null,armed=0,closed=0,posts=[],response=null,redirect=null,faultReached=false,blocked=false,calls=0;
const reviewed=new Set(),excluded=new Set();let built=false;
const fault=name=>{if(scenario===name){faultReached=true;return true;}return false;};
const amounts={'1001':['6','180.00','30.0000'],'4001':['2','84.00','42.0000'],'4002':['1','18.00','18.0000'],'4005':['0','0.00','0.0000']};
const packages={'1001':['SUP-001','36','5.0000'],'4001':['WEST-MULTI-4001','8','10.5000'],'4002':['WEST-BTL-4002','4','4.5000']};
const textNode=textContent=>({textContent,childNodes:[{textContent}]});
const cells=values=>values.map(textNode);
const mutate=(form,name,value)=>{form.elements.namedItem(name).value=value;};
class Form {
 constructor(action,values,variant=null){this.action=action;this.method='post';this.enctype='application/x-www-form-urlencoded';this.target='';this.variant=variant;
   this.fields=Object.entries({...values,[CSRF]:token}).map(([name,value])=>({name,value}));this.elements={namedItem:name=>this.fields.find(x=>x.name===name)};}
 get isConnected(){return forms.includes(this);}
 getAttribute(name){return this[name];}
 closest(){const first=textNode(this.variant);if(this.action.includes('/recommendations/'))first.textContent=this.variant+'Fixture product / variant';return {cells:[first]};}
 requestSubmit(){
   const url=new URL(this.action||location.href,location.href).href,path=new URL(url).pathname;
   let postData=new URLSearchParams(this.fields.map(x=>[x.name,x.value])).toString();
   if(phase==='review_preview'&&fault('request_duplicate'))postData+='&approved_cases=6';
   const request={method:'POST',url,postData};
   if(phase==='review_preview'&&fault('request_wrong_route'))request.url=ORIGIN+'/auth/logout';
   assert(guard);const allowed=guard.check(request);guard.receipts.push(allowed);
   if(!allowed){blocked=true;throw new Error('actual review request blocked');}
   posts.push(path);response={status:200};redirect=null;
   const values=Object.fromEntries(this.fields.filter(x=>x.name!==CSRF).map(x=>[x.name,x.value]));
   if(phase==='review_preview'&&fault('request_repeat')){const again=guard.check(request);guard.receipts.push(again);assert.equal(again,false);blocked=true;throw new Error('actual review request blocked');}
   if(path.includes('/blockers/')){excluded.add(this.variant);redirect={status:303,location:'../../../'+b.run_id};run();}
   else if(path.endsWith('/review')){
     current=b.recommendations.find(x=>path===detail+'/recommendations/'+x.recommendation_id+'/review');assert(current);
     if(phase==='review_preview'||values.action==='REJECT'){
       reviewed.add(current.variant_id);redirect={status:303,location:'../../../'+b.run_id};
       if(fault('redirect_foreign'))redirect.location='https://foreign.invalid'+detail;
       if(fault('redirect_status'))redirect.status=307;
       run();
     }else{
       phase='review_preview';location.href=url;forms=[new Form('',{...values,review_preview_fingerprint:'e'.repeat(64)})];
       if(fault('preview_fingerprint'))mutate(forms[0],'review_preview_fingerprint','bad');
       if(fault('preview_quantity'))mutate(forms[0],'approved_cases','7');
       if(fault('material_required'))forms[0].fields.push({name:'material_confirmation_reason',value:''});
       if(fault('preview_legacy'))forms[0].fields.push({name:'review_token',value:'synthetic'});
       if(fault('preview_http'))response.status=409;
     }
   }else if(path.endsWith('/build')){
     if(phase==='build_preview'){built=true;redirect={status:303,location:'../'+b.run_id};run();}
     else{phase='build_preview';location.href=url;forms=[new Form('',{actor:b.principal_ref,draft_preview_fingerprint:'f'.repeat(64),minimum_disposition:'PAY_FEE'})];
       if(fault('build_hash'))mutate(forms[0],'draft_preview_fingerprint','bad');
       if(fault('build_disposition'))mutate(forms[0],'minimum_disposition','ADD_LEGITIMATE_NEED');}
   }else throw new Error('unexpected fixture request');
 }
}
function run(){
 phase='run';location.href=ORIGIN+detail;forms=[];
 for(const item of b.blockers)if(!excluded.has(item.variant_id))forms.push(new Form(b.run_id+'/blockers/'+item.exception_id+'/exclude',{expected_input_fingerprint:b.input_fingerprint,reason:'',actor:'browser-value-ignored'},item.variant_id));
 for(const item of b.recommendations)if(!reviewed.has(item.variant_id))forms.push(new Form(b.run_id+'/recommendations/'+item.recommendation_id+'/review',{
   expected_input_fingerprint:b.input_fingerprint,action:'ACCEPT',approved_cases:item.variant_id==='1001'?'6':item.variant_id==='4005'?'0':'1',approved_loose_units:'0',comment:'',actor:'browser-value-ignored'},item.variant_id));
 if(reviewed.size===4)forms.push(new Form(b.run_id+'/build',{actor:'browser-value-ignored'}));
}
function state(){
 const stage=built?'PACKET_BUILT':reviewed.size===4?'REVIEWED':'AWAITING_REVIEW';
 const states=['RUNNING',stage,'DEVELOPMENT_ROLLING_ORIGIN_V2'];
 if(!posts.length&&fault('initial_stage'))states[1]='PACKET_BUILT';
 if(!posts.length&&fault('initial_model'))states[2]='DEVELOPMENT_ROLLING_ORIGIN_V1';
 if(built&&fault('final_stage'))states[1]='REVIEWED';
 return {querySelectorAll:()=>states.map(textNode),querySelector:()=>textNode(b.input_fingerprint)};
}
function tier(v=current.variant_id){return {case_price:amounts[v][2],unit_price:packages[v][2],level_type:v==='4002'?'BASE':'BREAK',break_unit:v==='1001'?'CS':v==='4001'?'BT':null,break_qty:v==='1001'?'2.0000':v==='4001'?'18.0000':null,source_price_book_batch_id:v==='1001'?b.price_batch_id:null};}
function economics(){
 const [cases,amount,price]=amounts[current.variant_id];
 const data=[['Cases',cases],['Loose units','0'],['Frozen applicable case price','$'+price],['Merchandise total','$'+amount],['Loose-order fee','$0.00'],['Recalculated line total','$'+amount]];
 data.push(['Ordered units',fault('economics_units')?'12':packages[current.variant_id][1]],['Frozen applicable unit cost',fault('economics_unit_cost')?'$1.0000':'$'+packages[current.variant_id][2]]);
 if(fault('economics_total'))data[5][1]='$999.00';
 if(fault('economics_case_price'))data[2][1]='$31.0000';
 if(fault('economics_fee'))data[4][1]='$7.00';
 return data.map(([name,val])=>({...textNode(name),nextElementSibling:textNode(val)}));
}
function draftRows(){
 const rows=[[b.vendors.southern,'$180.00','6','DOLLAR 20.0000','0.00','$0.00','$0.00','$0.00','$180.00'],[b.vendors.western,'$102.00','3','DOLLAR 120.0000','18.00','$0.00','$7.00','$7.00','$109.00']];
 if(fault('draft_fee'))rows[1][7]='$0.00';
 if(fault('draft_vendor'))rows[1][0]=b.vendors.southern;
 if(fault('draft_total'))rows[1][8]='$102.00';
 if(fault('draft_minimum'))rows[1][3]='DOLLAR 200';
 if(fault('draft_shortfall'))rows[1][4]='98';
 return rows.map(row=>({cells:cells(row)}));
}
function drafts(){
 if(!built)return [];
 const groups=[['Synthetic Southern',['1001']],['Synthetic Western Acceptance',['4001','4002']]];
 if(fault('final_partition')){groups[0][1]=['4001'];groups[1][1]=['1001','4002'];}
 return groups.map(([vendor,variants])=>({
   ...textNode(vendor+' — DRAFT'),nextElementSibling:{textContent:'PO '+uuid(fault('final_duplicate_po')?20:vendor==='Synthetic Southern'?20:21)+'; merchandise $'+(vendor==='Synthetic Southern'?'180.00':'102.00')+'; loose-order fees $0.00; below-minimum fee $'+(vendor==='Synthetic Southern'?'0.00':'7.00')+'; total fees $'+(fault('final_vendor_fee')?'99.00':vendor==='Synthetic Southern'?'0.00':'7.00')+'; total $'+(vendor==='Synthetic Southern'?'180.00':'109.00')+'; minimum shortfall '+(fault('final_shortfall')?'98.00':vendor==='Synthetic Southern'?'0.00':'18.00')+'; disposition '+(fault('final_disposition')?'DELAY':vendor==='Synthetic Southern'?'NOT_APPLICABLE':'PAY_FEE')+'; confirmed by '+(fault('final_actor')?'foreign':b.principal_ref),nextElementSibling:{querySelectorAll:()=>variants.map(v=>{
     const [cases,amount]=amounts[v],[sku,units,price]=packages[v];const row=[v,sku,cases,'0',units,'$'+price,'0.0000','2026-10-05 12:00:00+00:00','$'+amount,'$0.00','$'+amount];
     if(v==='4001'&&fault('final_line'))row[10]='$83.00';
     if(v==='4001'&&fault('final_stock'))row[6]='1.0000';
     if(v==='4001'&&fault('final_capture'))row[7]='2026-10-06 12:00:00+00:00';
     if(v==='4001'&&fault('final_sku'))row[1]='FOREIGN';
     if(v==='4001'&&fault('final_units'))row[4]='24';
     if(v==='4001'&&fault('final_unit_cost'))row[5]='$3.5000';
     return {cells:cells(row),querySelector:()=>{const t=tier(v);if(v==='4001'&&fault('final_tier'))t.source_price_book_batch_id=uuid(999);return textNode(JSON.stringify(t));}};
   })}}}));
}
function artifacts(){
 if(!built)return [];
 return [1,2,3].map(id=>({href:ORIGIN+detail+'/artifacts/'+(fault('artifact_duplicate')?1:id),
   closest:()=>({cells:cells([fault('artifact_kind')?'UNKNOWN':id===3?'EMERGENCY_REVIEW_PACKET':'VENDOR_INTERNAL_CSV',
     id===3?'—':id===1?b.vendors.southern:b.vendors.western,'100','Download'])})}));
}
const document={get forms(){return forms;},get body(){return {innerText:'TEST DATA — NOT FOR ORDERING business date: 2026-10-05 merchandise $282.00 fees $7.00 internal DRAFT total $289.00'};},
 querySelector(selector){assert.equal(selector,'h1');return {textContent:phase==='review_preview'?'Confirm recommendation economics':phase==='build_preview'?'Confirm vendor DRAFT economics':'Monday run '+b.run_id,nextElementSibling:state()};},
 querySelectorAll(selector){
   if(selector.startsWith('form[action*=')){
     const fragment=selector.includes('/recommendations/')?'/recommendations/':'/blockers/';
     const selected=forms.filter(f=>f.action.includes(fragment));
     if(!posts.length&&fragment==='/recommendations/'&&fault('initial_variant'))selected[0].variant='10010';
     if(!posts.length&&fragment==='/recommendations/'&&fault('initial_quantity'))mutate(selected[0],'approved_cases','3');
     return selected;
   }
   if(selector==='h3')return drafts();
   if(selector==='a[href*="/artifacts/"]')return artifacts();
   if(selector==='dl > dt')return economics();
   if(selector==='pre.final-price-tier-json'){
     const t=tier();if(fault('tier_binding'))t.source_price_book_batch_id=uuid(999);
     if(fault('tier_southern_break'))t.break_qty='18.0000';
     if(fault('tier_unit_price'))t.unit_price='1.0000';
     if(current.variant_id==='4002'&&fault('tier_bottle_break')){t.level_type='BREAK';t.break_unit='BT';t.break_qty='18.0000';}
     return [textNode(JSON.stringify(t))];
   }
   if(selector==='tbody tr')return draftRows();
   throw new Error('unexpected fixture selector');
 }
};
class FormData {constructor(form){this.pairs=form.fields.map(x=>[x.name,x.value]);}*[Symbol.iterator](){yield*this.pairs;}}
const page={
 async navigate(url){calls++;assert.equal(url,ORIGIN+detail);run();},
 async evaluate(program){calls++;return await vm.runInNewContext(program,{document,location,FormData,HTMLFormElement:Form,URL,TextEncoder,Uint8Array,crypto:crypto.webcrypto});},
 async submit(program){if(phase==='review_preview'&&fault('changed_csrf'))mutate(forms[0],CSRF,'T'.repeat(43));return await this.evaluate(program);},
 mark(){return posts.length;},responseAfter(path,method,mark){assert(posts.length>mark);return response;},
 redirectAfter(path,method,mark){assert(posts.length>mark);return redirect;},
 armRequestGuard(check){assert.equal(guard,null);armed++;guard={check,receipts:[]};const owned=guard;
   return {matches:()=>owned.receipts,close(){assert.equal(guard,owned);guard=null;closed++;}};}
};
if(fault('binding_source'))b.commit=[b.commit];
if(fault('binding_recommendations'))b.recommendations[0].variant_id='10010';
if(fault('binding_getter'))Object.defineProperty(b,'tree',{get(){throw new Error('getter must not run');},enumerable:true});
let result,error;
try{result=await runStagingReviewDraftStep(page,b);}catch(e){if(e.name==='AssertionError'||!/^(staging (review|mapping|APPLY) component refused|mapping |actual review request blocked)/.test(e.message))throw e;error=e.message;}
assert.equal(guard,null);assert.equal(armed,closed);
if(scenario==='success'){
 assert.equal(error,undefined);assert.equal(posts.length,11);assert.equal(result.observed_controls.total_cents,28900);
 assert.equal(result.packet_members,'NOT_DOWNLOADED_OR_VALIDATED');assert.equal(result.execution_authority,false);assert(!JSON.stringify(result).includes(token));
}else{
 assert(error,scenario+' did not refuse');assert(faultReached,scenario+' fault not reached');
 if(scenario.startsWith('binding_')){assert.equal(calls,0);assert.equal(posts.length,0);}
 else if(scenario.startsWith('initial_'))assert.equal(posts.length,0);
 else if(scenario.startsWith('draft_')||scenario.startsWith('build_'))assert.equal(posts.length,10);
 else if(scenario.startsWith('final_')||scenario.startsWith('artifact_'))assert.equal(posts.length,11);
 else if(scenario.startsWith('redirect_')||scenario==='request_repeat')assert.equal(posts.length,4);
 else if(scenario==='tier_bottle_break')assert.equal(posts.length,7);
 else assert.equal(posts.length,3);
 if(scenario.startsWith('request_'))assert(blocked);
}
console.log(JSON.stringify({scenario,assertion:'PASS',forwarded_posts:posts.length,guards_closed:closed,scope:'SCRIPTED_DOM_AND_REQUEST_COMPONENT_ONLY'}));
