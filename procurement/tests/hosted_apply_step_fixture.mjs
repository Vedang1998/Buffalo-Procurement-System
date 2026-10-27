// Synthetic DOM/request adapter only. No Chromium, network, DB or credentials.
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import vm from 'node:vm';
import {runStagingApplyStep,canonicalIntent,applyRequestMatches,ORIGIN} from '../tools/hosted_synthetic_apply_step.mjs';
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const batch='aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee';
const intent={contract:'BUFFALO_SYNTHETIC_PRICE_APPLY_PREVIEW_V1',price_book_batch_id:batch,
  vendor_id:'11111111-2222-4333-8444-555555555555',price_scope_key:'COMPLETE_VENDOR',apply_idempotency_key:'synthetic-apply-v1',
  prior_event_id:'99999999-8888-4777-8666-555555555555',expected_prior_head_version:2,
  expected_current_scope_sha256:'1'.repeat(64),resulting_current_scope_sha256:'2'.repeat(64),scope_membership_sha256:'3'.repeat(64),
  raw_content_sha256:'4'.repeat(64),declaration_sha256:'5'.repeat(64),policy_ref:'registered-synthetic-policy',policy_sha256:'6'.repeat(64),
  confirmation_payload_sha256:'7'.repeat(64),price_book_promotion_event_id:1,expected_current_row_count:4,resulting_current_row_count:4,
  unaffected_price_state_sha256:'8'.repeat(64),backup_manifest_sha256:'9'.repeat(64),backup_prechange_scope_sha256:'a'.repeat(64),
  application_at:'2026-10-01T14:00:00+00:00',variant_ids:['1001'],principal_ref:'owner:railway-staging:01',role_ref:'procurement.price.approve',
  commercial_authority:false,real_price_approval:false,staging_backup_release_sha256:'b'.repeat(64),staging_runtime_attestation_identity:'c'.repeat(64)};
const binding={intent,commit:'a'.repeat(40),tree:'b'.repeat(40),operator_proof_sha256:'d'.repeat(64),confirmation_proof_sha256:'e'.repeat(64),backup:{
  target_kind:'staging',manifest_sha256:intent.backup_manifest_sha256,prechange_scope_sha256:intent.backup_prechange_scope_sha256,
  batch_id:batch,vendor_id:intent.vendor_id,price_scope_key:intent.price_scope_key,prior_event_id:intent.prior_event_id,
  prior_head_version:intent.expected_prior_head_version,raw_content_sha256:intent.raw_content_sha256,
  staging_release_sha256:intent.staging_backup_release_sha256,runtime_attestation_identity:intent.staging_runtime_attestation_identity}};
const scenario=process.argv[2];
if(scenario==='canonical') { process.stdout.write(JSON.stringify({intent,sha256:hash(canonicalIntent(intent))}));process.exit(0); }
const CSRF='_buffalo_staging_csrf';const token='S'.repeat(43);
let phase='confirmed',bodyPreview=null,guard=null,posted=[],guardsClosed=0,armed=0;
let faultReached=false,previewRead=false,browserCalls=0,guardRejected=false;
const location={href:ORIGIN+'/price-books/'+batch};
let form;
class Form {
  constructor(action,fields) {this.action=ORIGIN+action;this.method='post';this.enctype='application/x-www-form-urlencoded';this.target='';
    this.fields=Object.entries(fields).map(([name,value])=>({name,value}));this.elements={namedItem:name=>this.fields.find(f=>f.name===name)};}
  get isConnected(){return this===form;}
  getAttribute(name){return this[name];}
  requestSubmit(){
    const path=new URL(this.action).pathname;
    let postData=new URLSearchParams(this.fields.map(f=>[f.name,f.value])).toString();
    if(path.endsWith('/apply') && scenario==='request_duplicate'){postData+='&confirm=CONFIRM';faultReached=true;}
    if(path.endsWith('/apply') && scenario==='request_csrf'){postData=postData.replace(token,'T'.repeat(43));faultReached=true;}
    const request={url:this.action,method:'POST',postData};
    if(path.endsWith('/apply') && scenario==='request_wrong_route'){request.url=ORIGIN+'/price-books/'+batch+'/reject';faultReached=true;}
    assert(guard);const matches=guard.check(request);guard.receipts.push(matches);
    if(!matches){guardRejected=true;throw new Error('actual request blocked before forwarding');}
    posted.push(path);
    if(path.endsWith('/apply') && scenario==='request_repeat'){faultReached=true;const again=guard.check(request);guard.receipts.push(again);assert.equal(again,false);guardRejected=true;throw new Error('actual request blocked before forwarding');}
    if(path.endsWith('/apply-preview')){
      phase='preview';location.href=this.action;bodyPreview=structuredClone(intent);
      if(scenario.startsWith('preview:')){
        faultReached=true;const key=scenario.slice(8);
        if(key==='variant_ids')bodyPreview[key]=['1001','4001'];
        else if(typeof bodyPreview[key]==='number')bodyPreview[key]+=1;
        else if(typeof bodyPreview[key]==='boolean')bodyPreview[key]=true;
        else bodyPreview[key]=key.endsWith('sha256')||key.endsWith('identity')?'f'.repeat(64):bodyPreview[key]+'-changed';
      }
      bodyPreview.preview_sha256=hash(canonicalIntent(bodyPreview));
      if(scenario==='preview_hash'){bodyPreview.preview_sha256='0'.repeat(64);faultReached=true;}
      if(scenario==='preview_extra'){bodyPreview.unexpected=true;faultReached=true;}
      form=new Form('/price-books/'+batch+'/apply',{apply_idempotency_key:intent.apply_idempotency_key,
        expected_preview_sha256:bodyPreview.preview_sha256,actor:intent.principal_ref,confirm:'CONFIRM',[CSRF]:token});
      if(scenario==='form_duplicate')faultReached=true;
      if(scenario==='form_duplicate')form.fields.push({name:CSRF,value:token});
      if(scenario==='form_legacy')faultReached=true;
      if(scenario==='form_legacy')form.fields.push({name:'review_token',value:'synthetic-not-a-token'});
      if(scenario==='form_backup_path')faultReached=true;
      if(scenario==='form_backup_path')form.fields.push({name:'backup_path',value:'/synthetic'});
      if(scenario==='form_actor')faultReached=true;
      if(scenario==='form_actor')form.elements.namedItem('actor').value='spoof';
      if(scenario==='form_target')faultReached=true;
      if(scenario==='form_target')form.target='_blank';
    }else{phase='applied';location.href=ORIGIN+'/price-books/'+batch;form=null;}
  }
}
form=new Form('/price-books/'+batch+'/apply-preview',{apply_idempotency_key:'',actor:'',[CSRF]:token});
if(scenario==='before_wrong_batch'){location.href=ORIGIN+'/price-books/foreign';faultReached=true;}
const document={
  get forms(){return form?[form]:[];},
  querySelector(selector){
    if(selector==='#synthetic-price-apply-json'){previewRead=true;}
    if(selector==='#synthetic-price-apply-json')return {textContent:JSON.stringify(bodyPreview,null,2)};
    if(selector==='#price-book-durable-status' && ((scenario==='before_validated'&&phase==='confirmed') || (scenario==='after_not_applied'&&phase==='applied')))faultReached=true;
    if(selector==='#price-book-durable-status')return {textContent:(phase==='applied'?(scenario==='after_not_applied'?'VERIFIED_FUTURE':'APPLIED_CURRENT'):(scenario==='before_validated'?'VALIDATED':'VERIFIED_FUTURE'))};
    if(selector==='#price-book-operational-status')return {textContent:phase==='applied'?'APPLIED_CURRENT':'VERIFIED_FUTURE',dataset:{temporalBasis:'REGISTERED_OBSERVATION'}};
    if(selector==='[name="review_token"],input[type="file"]')return null;
    if(selector.includes('form[action$=')){
      const suffix=/action\$="([^"]+)"/.exec(selector)[1];return form?.action.endsWith(suffix)?form:null;
    }
    throw new Error('unexpected scripted DOM selector');
  }
};
class FormData {constructor(selected){this.pairs=selected.fields.map(f=>[f.name,f.value]);}*[Symbol.iterator](){yield*this.pairs;}}
const page={
  async evaluate(expression){browserCalls++;return await vm.runInNewContext(expression,{document,location,FormData,HTMLFormElement:Form,URL,TextEncoder,Uint8Array,crypto:crypto.webcrypto});},
  async submit(expression){
    if(phase==='preview'&&scenario==='form_csrf_changed'){form.elements.namedItem(CSRF).value='T'.repeat(43);faultReached=true;}
    if(phase==='preview'&&scenario==='form_action_changed'){form.action='https://foreign.invalid/price-books/'+batch+'/apply';faultReached=true;}
    return await this.evaluate(expression);
  },
  mark(){return posted.length;},
  responseAfter(path,method,mark){if(scenario==='preview_http')faultReached=true;return posted.slice(mark).includes(path)?{status:scenario==='preview_http'?409:200}:null;},
  redirectAfter(){if(scenario.startsWith('redirect_'))faultReached=true;return {status:scenario==='redirect_status'?307:303,location:scenario==='redirect_foreign'?'https://foreign.invalid/price-books/'+batch:'../../price-books/'+batch};},
  armRequestGuard(check){assert(!guard);armed++;guard={check,receipts:[]};const current=guard;
    return {matches:()=>current.receipts,close(){assert.equal(guard,current);guard=null;guardsClosed++;}};},
};
if(scenario.startsWith('backup:')){
 faultReached=true;const key=scenario.slice(7);binding.backup[key]=typeof binding.backup[key]==='number'?3:'changed';
}
if(scenario==='source_bad'){binding.commit='bad';faultReached=true;}
if(scenario==='binding_getter'){Object.defineProperty(binding,'intent',{get(){throw new Error('accessor must never execute');},enumerable:true});faultReached=true;}
if(scenario==='binding_tojson'){binding.toJSON=()=>{throw new Error('toJSON must never execute');};faultReached=true;}
let result=null,error=null;
try{result=await runStagingApplyStep(page,binding);}catch(e){
 if(e.name==='AssertionError' || !/^(staging APPLY component refused|APPLY |actual request blocked)/.test(e.message))throw e;
 error=e.name;
}
assert.equal(guard,null);assert.equal(guardsClosed,armed);
const finalPosts=posted.filter(p=>p.endsWith('/apply')).length;
if(scenario==='success'){
 assert.equal(error,null);assert.equal(finalPosts,1);assert.equal(posted.length,2);
 assert.equal(result.execution_authority,false);assert.equal(result.observed_status,'APPLIED_CURRENT');
 assert(!JSON.stringify(result).includes(token));
}else{
 assert(error,scenario+' did not refuse');assert(faultReached,scenario+' fault not reached');
 const early=scenario.startsWith('backup:')||scenario.startsWith('binding_')||scenario==='source_bad';
 if(early){assert.equal(browserCalls,0);assert.equal(posted.length,0);}
 else if(scenario.startsWith('before_'))assert.equal(posted.length,0);
 else {assert.equal(posted.filter(p=>p.endsWith('/apply-preview')).length,1);if(scenario!=='preview_http')assert(previewRead);}
 if(scenario.startsWith('request_'))assert(guardRejected);
 assert.equal(finalPosts,['redirect_status','redirect_foreign','after_not_applied','request_repeat'].includes(scenario)?1:0);
}
console.log(JSON.stringify({scenario,assertion:'PASS',final_posts:finalPosts,guards_closed:guardsClosed,scope:'SCRIPTED_DOM_AND_REQUEST_COMPONENT_ONLY'}));
