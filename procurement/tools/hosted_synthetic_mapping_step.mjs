// Dormant synthetic mapping/selection browser component. No login, DB, network
// client, runtime activation or release grant. Expectations come from the bound
// fixture/intake identity, never a first matching DOM row or offer option.
import {ORIGIN,snapshotBinding,armSinglePost} from './hosted_synthetic_apply_step.mjs';
import crypto from 'node:crypto';
const HASH=/^[0-9a-f]{64}$/, OID=/^[0-9a-f]{40}$/;
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const CSRF='_buffalo_staging_csrf';
const VARIANTS=['1001','4001','4002','4003','4004','4005'];
const requireValue=v=>{if(!v)throw new Error('staging mapping component refused');};
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const keys=(v,expected)=>v && !Array.isArray(v) && same(Object.keys(v).sort(),[...expected].sort());
const exact=(a,b)=>keys(a,Object.keys(b)) && Object.entries(b).every(([k,v])=>a[k]===v);

function validate(supplied) {
  const value=snapshotBinding(supplied);
  requireValue(keys(value,['commit','tree','fixture_proof_sha256','apply_proof_sha256','candidates']) &&
    ['commit','tree','fixture_proof_sha256','apply_proof_sha256'].every(k=>typeof value[k]==='string') &&
    OID.test(value.commit) && OID.test(value.tree) && HASH.test(value.fixture_proof_sha256) && HASH.test(value.apply_proof_sha256) &&
    Array.isArray(value.candidates) && value.candidates.length===7);
  for(const item of value.candidates) {
    requireValue(keys(item,['candidate_id','occurrence_key','vendor','variant_id','offer_id']) &&
      typeof item.candidate_id==='string' && UUID.test(item.candidate_id) && typeof item.occurrence_key==='string' && /^[A-Za-z0-9:_-]{1,120}$/.test(item.occurrence_key) &&
      typeof item.vendor==='string' && /^[\x20-\x7e]{1,120}$/.test(item.vendor) &&
      (item.variant_id===null ? item.offer_id===null && item.occurrence_key==='synthetic-unresolved-occurrence-001' :
        VARIANTS.includes(item.variant_id) && typeof item.offer_id==='string' && /^[1-9][0-9]{0,17}$/.test(item.offer_id)));
  }
  requireValue(new Set(value.candidates.map(x=>x.candidate_id)).size===7 &&
    new Set(value.candidates.map(x=>x.occurrence_key)).size===7 &&
    same(value.candidates.filter(x=>x.variant_id!==null).map(x=>x.variant_id).sort(),VARIANTS) &&
    value.candidates.filter(x=>x.variant_id===null).length===1);
  return value;
}

// Actual API confirmation forms omit action; their destination is the current
// exact POST URL. All named controls, including gateway CSRF, are inspected and
// rechecked immediately before requestSubmit. Fill only ordinary chosen fields.
export function mappingFormProgram(path,locationPath,names,expected={},fill={},submit=null) {
  return `(async()=>{
    const path=${JSON.stringify(ORIGIN+path)}, at=${JSON.stringify(ORIGIN+locationPath)};
    const matches=[...document.forms].filter(f=>f.method==='post' && new URL(f.getAttribute('action')||location.href,location.href).href===path);
    if(matches.length!==1)throw new Error('mapping form count refused');
    const form=matches[0], expected=${JSON.stringify(expected)}, fill=${JSON.stringify(fill)};
    const inspect=()=>{
      const pairs=[...new FormData(form)];
      if(location.href!==at || !form.isConnected || form.method!=='post' || form.enctype!=='application/x-www-form-urlencoded' ||
        !['','_self'].includes(form.target) || new URL(form.getAttribute('action')||location.href,location.href).href!==path ||
        pairs.some(([k,v])=>typeof v!=='string') || JSON.stringify(pairs.map(([k])=>k).sort())!==${JSON.stringify(JSON.stringify([CSRF,...names].sort()))})
        throw new Error('mapping form fields refused');
      return pairs;
    };
    inspect();
    for(const [key,value] of Object.entries(fill)) {
      const input=form.elements.namedItem(key);if(!input)throw new Error('mapping fill absent');
      if(input.options && ![...input.options].some(option=>option.value===value))throw new Error('mapping option absent');
      input.value=value;
    }
    const pairs=inspect(), values=Object.fromEntries(pairs), raw=values[${JSON.stringify(CSRF)}];
    if(!/^[A-Za-z0-9_-]{43}$/.test(raw||'') || Object.entries({...expected,...fill}).some(([k,v])=>values[k]!==v))throw new Error('mapping form binding refused');
    const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw));
    const csrfSha256=[...new Uint8Array(digest)].map(v=>v.toString(16).padStart(2,'0')).join('');
    if(JSON.stringify(inspect())!==JSON.stringify(pairs))throw new Error('mapping form changed');
    ${submit ? `if(csrfSha256!==${JSON.stringify(submit.csrfSha256)})throw new Error('mapping CSRF changed');HTMLFormElement.prototype.requestSubmit.call(form);` : ''}
    return {fields:Object.fromEntries(pairs.filter(([k])=>k!==${JSON.stringify(CSRF)})),csrfSha256};
  })()`;
}

export async function post(page,path,at,fields) {
  const names=Object.keys(fields), checked=await page.evaluate(mappingFormProgram(path,at,names,fields));
  requireValue(exact(checked?.fields,fields) && HASH.test(checked.csrfSha256));
  const mark=page.mark(), guard=armSinglePost(page,path,fields,checked.csrfSha256);
  try {await page.submit(mappingFormProgram(path,at,names,fields,{},checked));guard.verify();}
  finally {guard.close();}
  return mark;
}

export function redirect(page,path,destination,mark) {
  const value=page.redirectAfter(path,'POST',mark);
  requireValue(value?.status===303 && typeof value.location==='string' && new URL(value.location,ORIGIN+path).href===ORIGIN+destination);
}

async function status(page,decisions,selections) {
  const response=await page.readJson('/supplier-mapping/status');
  requireValue(response?.status===200 && response.url===ORIGIN+'/supplier-mapping/status');
  const s=response.body;
  requireValue(shadowOnly(s) && s.review_batch_count===3 && s.candidate_count===7 && s.decision_count===decisions &&
    s.selection_event_count===selections && s.selection_head_count===selections);
  return {review_batch_count:3,candidate_count:7,decision_count:decisions,selection_event_count:selections,selection_head_count:selections};
}

function shadowOnly(value) {
  return value && value.authority==='SHADOW_ONLY' && value.recommendation_cutover_enabled===false && value.offer_activation_enabled===false;
}

export function mappingDecisionId(key) {
  requireValue(typeof key==='string' && UUID.test(key));
  const bytes=crypto.createHash('sha1').update(Buffer.from('6ba7b8119dad11d180b400c04fd430c8','hex'))
    .update('buffalo-mapping-decision:'+key,'utf8').digest().subarray(0,16);
  bytes[6]=(bytes[6]&15)|80;bytes[8]=(bytes[8]&63)|128;
  const hex=bytes.toString('hex');return [hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join('-');
}

async function confirm(page,path,fields,kind,decisionId=null) {
  // Preview SHA is server-produced, then bound unchanged into its confirmation.
  // We do not claim an independent recomputation of the DB-dependent preview.
  const extra=kind==='selection'?['mapping_decision_id']:[];
  const names=[...Object.keys(fields),...extra,'confirm','expected_preview_sha256'];
  const preview=await page.evaluate(mappingFormProgram(path,path,names,{...fields,confirm:'CONFIRM'}));
  requireValue(preview && HASH.test(preview.fields?.expected_preview_sha256) && HASH.test(preview.csrfSha256) &&
    Object.entries(fields).every(([k,v])=>preview.fields[k]===v) && preview.fields.confirm==='CONFIRM' &&
    (kind!=='selection' || UUID.test(decisionId) && preview.fields.mapping_decision_id===decisionId));
  const mark=await post(page,path,path,preview.fields);
  redirect(page,path,path.slice(0,path.lastIndexOf('/')),mark);
  return {preview_sha256:preview.fields.expected_preview_sha256,
    ...(kind==='selection'?{mapping_decision_id:preview.fields.mapping_decision_id}:{})};
}

export async function runStagingMappingStep(page,supplied) {
  const binding=validate(supplied);
  for(const name of ['navigate','evaluate','submit','mark','responseAfter','redirectAfter','armRequestGuard','readJson'])requireValue(typeof page[name]==='function');
  const list='/supplier-mapping';
  await page.navigate(ORIGIN+list);
  // Replays/preseeded final decisions are not accepted as a fresh workflow.
  const before=await page.readJson(list+'/status');
  requireValue(before?.status===200 && before.url===ORIGIN+list+'/status' && shadowOnly(before.body) &&
    ['review_batch_count','candidate_count','decision_count','selection_event_count','selection_head_count'].every(k=>before.body[k]===0));
  redirect(page,list+'/intake',list,await post(page,list+'/intake',list,{}));
  await status(page,0,0);
  const rows=await page.evaluate(`({url:location.href,rows:[...document.querySelectorAll('tbody tr')].map(row=>({href:row.querySelector('a')?.getAttribute('href'),cells:[...row.querySelectorAll('td')].map(cell=>cell.textContent.trim())}))})`);
  requireValue(rows?.url===ORIGIN+list && Array.isArray(rows.rows) && rows.rows.length===7);
  const actual=new Map();
  for(const row of rows.rows) {
    requireValue(typeof row.href==='string' && !actual.has(row.href) && Array.isArray(row.cells) && row.cells.length===6);
    actual.set(row.href,row.cells);
  }
  for(const item of binding.candidates) {
    const cells=actual.get(list+'/'+item.candidate_id);
    requireValue(cells && cells[0]===item.occurrence_key && cells[1]===item.vendor &&
      cells[2]===(item.variant_id||'—') && cells[5]==='PENDING');
  }
  const observations=[];let decisionCount=0,selectionCount=0;
  const ordered=[binding.candidates.find(x=>x.variant_id===null),...VARIANTS.map(v=>binding.candidates.find(x=>x.variant_id===v))];
  for(const item of ordered) {
    const detail=list+'/'+item.candidate_id,path=detail+'/decision';
    await page.navigate(ORIGIN+detail);
    const chosen={action:item.variant_id?'APPROVE_MAPPING':'DEFER',
      reason:item.variant_id?'Fabricated exact acceptance mapping for '+item.variant_id:'Synthetic unresolved identity remains deferred',
      offer_link_kind:item.variant_id?'LINKED_EXISTING':'',existing_offer_id:item.offer_id||''};
    const names=[...Object.keys(chosen),'idempotency_key'];
    const form=await page.evaluate(mappingFormProgram(path,detail,names,{},chosen));
    requireValue(UUID.test(form?.fields?.idempotency_key));
    const mark=await post(page,path,detail,form.fields);
    requireValue(page.responseAfter(path,'POST',mark)?.status===200);
    const decision=await confirm(page,path,form.fields,'decision');decision.mapping_decision_id=mappingDecisionId(form.fields.idempotency_key);decisionCount++;
    await status(page,decisionCount,selectionCount);
    let selection=null;
    if(item.variant_id && item.variant_id!=='4003') {
      const selectionPath=detail+'/selection',values={selection_action:'SELECT',effective_from:'2026-10-05',reason:'Fabricated routine selection for '+item.variant_id};
      const selected=await page.evaluate(mappingFormProgram(selectionPath,detail,[...Object.keys(values),'idempotency_key'],{selection_action:'SELECT'},
        {effective_from:values.effective_from,reason:values.reason}));
      requireValue(UUID.test(selected?.fields?.idempotency_key));
      const selectionMark=await post(page,selectionPath,detail,selected.fields);
      requireValue(page.responseAfter(selectionPath,'POST',selectionMark)?.status===200);
      selection=await confirm(page,selectionPath,selected.fields,'selection',decision.mapping_decision_id);selectionCount++;
      await status(page,decisionCount,selectionCount);
    }
    observations.push({candidate_id:item.candidate_id,variant_id:item.variant_id,offer_id:item.offer_id,action:chosen.action,decision,selection});
  }
  const controls=await status(page,7,5);
  return {contract:'BUFFALO_STAGING_MAPPING_STEP_OBSERVATION_V1',commit:binding.commit,tree:binding.tree,
    fixture_proof_sha256:binding.fixture_proof_sha256,apply_proof_sha256:binding.apply_proof_sha256,observations,controls,
    scope:'MAPPING_SELECTION_STEP_ONLY',preview_digest_scope:'SERVER_OBSERVED_AND_RETAINED_NOT_INDEPENDENTLY_RECOMPUTED',
    commercial_authority:false,execution_authority:false};
}
