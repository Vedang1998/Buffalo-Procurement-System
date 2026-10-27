// Dormant canonical V1 retirement -> V2 preparation component. No credentials,
// runtime activation or DB writes outside the supplied guarded browser adapter.
import crypto from 'node:crypto';
import {ORIGIN,snapshotBinding} from './hosted_synthetic_apply_step.mjs';
import {mappingFormProgram,post,redirect} from './hosted_synthetic_mapping_step.mjs';
const HASH=/^[0-9a-f]{64}$/,OID=/^[0-9a-f]{40}$/,UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const STALE='00000000-0000-4000-8000-000000000901';
const REASON='Synthetic retired forecast method requires V2 re-preparation';
const requireValue=v=>{if(!v)throw new Error('staging preparation component refused');};
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const keys=(v,n)=>v && !Array.isArray(v) && same(Object.keys(v).sort(),[...n].sort());
const expectedQuantities={'1001':['6',17,'2026-10-19','2026-10-22'],'4001':['1',3,'2026-10-07','2026-10-08'],
 '4002':['1',3,'2026-10-07','2026-10-08'],'4005':['0',3,'2026-10-07','2026-10-08']};
const headerProgram=`({url:location.href,title:document.querySelector('h1')?.textContent.trim(),
 states:[...document.querySelector('h1').nextElementSibling.querySelectorAll('b')].map(n=>n.textContent.trim()),
 fingerprint:document.querySelector('h1').nextElementSibling.querySelector('code')?.textContent.trim(),
 text:document.body.innerText,artifacts:document.querySelectorAll('a[href*="/artifacts/"]').length,
 drafts:[...document.querySelectorAll('h3')].filter(n=>n.textContent.endsWith(' — DRAFT')).length})`;

function validate(value) {
 const b=snapshotBinding(value);
 requireValue(keys(b,['commit','tree','fixture_proof_sha256','mapping_proof_sha256','stale_input_fingerprint','principal_ref','price_batch_id','vendors']) &&
   ['commit','tree','fixture_proof_sha256','mapping_proof_sha256','stale_input_fingerprint','principal_ref','price_batch_id'].every(k=>typeof b[k]==='string') &&
   OID.test(b.commit) && OID.test(b.tree) && HASH.test(b.fixture_proof_sha256) && HASH.test(b.mapping_proof_sha256) &&
   HASH.test(b.stale_input_fingerprint) && /^owner:[A-Za-z0-9:-]{1,100}$/.test(b.principal_ref) && UUID.test(b.price_batch_id) &&
   keys(b.vendors,['southern','western']) && typeof b.vendors.southern==='string' && typeof b.vendors.western==='string' &&
   UUID.test(b.vendors.southern) && UUID.test(b.vendors.western) && b.vendors.southern!==b.vendors.western);
 return b;
}
function header(observed,id,states,fingerprint=null) {
 requireValue(observed?.url===ORIGIN+'/monday-runs/'+id && observed.title==='Monday run '+id && same(observed.states,states) &&
   typeof observed.fingerprint==='string' && HASH.test(observed.fingerprint) && (fingerprint===null || observed.fingerprint===fingerprint) &&
   observed.artifacts===0 && observed.drafts===0 && observed.text.includes('TEST DATA — NOT FOR ORDERING') && observed.text.includes('business date: 2026-10-05'));
}
function scaled(value) {
 requireValue(typeof value==='string' && /^[0-9]+\.[0-9]{4}$/.test(value));
 const [whole,fraction]=value.split('.');const n=Number(whole)*10000+Number(fraction);requireValue(Number.isSafeInteger(n));return n;
}
export function validatePreparationForecast(variant,e,vendors) {
 const expected=expectedQuantities[variant];requireValue(expected && e && e.contract==='BUFFALO_DEVELOPMENT_FORECAST_EVIDENCE_V2' &&
   e.method_version==='DEVELOPMENT_ROLLING_ORIGIN_V2' && e.status==='READY' && e.commercial_authority===false && e.production_activation===false &&
   typeof e.selected_model==='string' && ['X','Y','Z'].includes(e.xyz_class) && e.abc_class==='NOT_CONFIGURED' && e.confidence==='LOW' &&
   e.availability?.proven_in_stock_days===0 && e.availability.unknown_days===138 && e.availability.protection_qualification==='LIMITED' &&
   e.protection?.status==='CALCULATED_LIMITED_AVAILABILITY' && e.protection.availability_qualification==='LIMITED' &&
   Array.isArray(e.reason_codes) && e.reason_codes.includes('AVAILABILITY_COVERAGE_LIMITS_PROTECTION') && typeof e.sha256==='string' && HASH.test(e.sha256) &&
   e.horizon_days===expected[1] && e.history_start==='2026-05-20' && e.history_end==='2026-10-04' &&
   e.policy?.source_sha256==='fee2e91e14565a835f1d69c27ff547ccdda5c22c6bc43a2bb003c2b78f190b52' &&
   e.policy.canonical_sha256==='ff62b1d313a18a907e811e8722431efee2349fc95a31e08ea5843ca2664ca858' &&
   e.confidence_policy?.metric==='EVALUATION_WAPE' && e.confidence_policy.boundary_rule==='UPPER_BOUNDS_INCLUSIVE' &&
   e.confidence_policy.high_max_inclusive==='0.200000' && e.confidence_policy.medium_max_inclusive==='0.500000' &&
   e.protection_calendar?.current_order_receipt_date==='2026-10-08' &&
   e.protection_calendar.vendor_id===(variant==='1001'?vendors.southern:vendors.western) &&
   e.protection_calendar.basis==='ANCHORED_REVIEW_SUBMISSION_DELIVERY_SCHEDULE_V2' && e.protection_calendar.business_date==='2026-10-05' &&
   e.protection_calendar.timezone_name==='America/New_York' && e.protection_calendar.horizon_days===e.horizon_days &&
   e.protection_calendar.next_submission_date===expected[2] && e.protection_calendar.next_order_receipt_date===expected[3] &&
   e.protection_calendar.schedule?.source_sha256==='1cb3edf5e82f2f01cb0a4c972d6140f97e767df67468373b245d6dd414ad8640' &&
   e.protection_calendar.schedule.canonical_sha256==='2c8b09152d3005b227a93ba700a8c08eec68433bf8f39e44c85353af34639c39' &&
   e.protection_calendar.interval_semantics==='HALF_OPEN_[EVALUATION_DATE,NEXT_RECEIPT)_RECEIPT_START_OF_DAY');
 for(const [names,expectedValue] of [[['next_order_date','next_submission_date'],expected[2]],[['next_receipt_date','next_order_receipt_date'],expected[3]]])
   requireValue(names.every(key=>e.protection_calendar[key]===undefined || e.protection_calendar[key]===null || e.protection_calendar[key]===expectedValue));
 requireValue(scaled(e.point_forecast_units)+scaled(e.protection_units)===scaled(e.target_units));
 if(variant==='1001')requireValue(e.point_forecast_units==='34.0000' && e.protection_units==='0.0000' && e.target_units==='34.0000');
 const counts=e.horizon_days===17?[22,22,18]:[36,36,32];
 for(const [index,key] of ['selection','calibration','evaluation'].entries()){
   const w=e.origin_windows?.[key];requireValue(w && w.planned_origin_count===counts[index] && w.usable_origin_count===counts[index] && w.censored_origin_count===0 &&
     w.overlapping_targets===true && w.independent_samples===false && w.target_interval_semantics==='HALF_OPEN_[ORIGIN,ORIGIN_PLUS_HORIZON)');
 }
 return true;
}

export async function runStagingPreparationStep(page,supplied) {
 const b=validate(supplied);
 for(const name of ['navigate','evaluate','submit','mark','responseAfter','redirectAfter','armRequestGuard','readJson'])requireValue(typeof page[name]==='function');
 const mapping=await page.readJson('/supplier-mapping/status');
 requireValue(mapping?.url===ORIGIN+'/supplier-mapping/status' && mapping.status===200 && mapping.body &&
   mapping.body.review_batch_count===3 && mapping.body.candidate_count===7 && mapping.body.decision_count===7 && mapping.body.selection_head_count===5 &&
   mapping.body.selection_event_count===5 && mapping.body.authority==='SHADOW_ONLY' && mapping.body.recommendation_cutover_enabled===false && mapping.body.offer_activation_enabled===false);
 const stale='/monday-runs/'+STALE,retire=stale+'/retire-stale-forecast';
 await page.navigate(ORIGIN+stale);header(await page.evaluate(headerProgram),STALE,['RUNNING','PREPARING','EMERGENCY_TRANSPARENT_V1'],b.stale_input_fingerprint);
 await page.evaluate(mappingFormProgram(retire,stale,['reason'],{},{reason:REASON}));
 let mark=await post(page,retire,stale,{reason:REASON});requireValue(page.responseAfter(retire,'POST',mark)?.status===200);
 const preview=await page.evaluate(`({url:location.href,title:document.querySelector('h1')?.textContent.trim(),entries:[...document.querySelectorAll('dl > dt')].map(n=>[n.textContent.trim(),n.nextElementSibling.textContent.trim()])})`);
 requireValue(preview?.url===ORIGIN+retire && preview.title==='Confirm exact V1 retirement' && Array.isArray(preview.entries) && new Set(preview.entries.map(x=>x[0])).size===preview.entries.length);
 const facts=Object.fromEntries(preview.entries),expected={'Run':STALE,'Business date':'2026-10-05','Frozen fingerprint':b.stale_input_fingerprint,
   'Retired method':'EMERGENCY_TRANSPARENT_V1','Transition':'RUNNING/PREPARING → FAILED/FAILED','Purchase orders / artifacts':'0 / 0',
   'Authenticated actor':b.principal_ref,'Reason':REASON};
 requireValue(keys(facts,[...Object.keys(expected),'Confirmation SHA-256']) && Object.entries(expected).every(([k,v])=>facts[k]===v) && HASH.test(facts['Confirmation SHA-256']));
 const confirmation={reason:REASON,expected_confirmation_sha256:facts['Confirmation SHA-256']};
 redirect(page,retire,stale,await post(page,retire,retire,confirmation));
 header(await page.evaluate(headerProgram),STALE,['FAILED','FAILED','EMERGENCY_TRANSPARENT_V1'],b.stale_input_fingerprint);
 await page.navigate(ORIGIN+'/monday-runs');
 const prepare='/monday-runs/prepare',fields={business_date:'2026-10-05',idempotency_key:'browser-multivendor-20261005-v1',variant_ids:'1001,4001,4002,4003,4004,4005',actor:b.principal_ref};
 await page.evaluate(mappingFormProgram(prepare,'/monday-runs',Object.keys(fields),{},fields));
 mark=await post(page,prepare,'/monday-runs',fields);
 const response=page.redirectAfter(prepare,'POST',mark);requireValue(response?.status===303 && typeof response.location==='string');
 const target=new URL(response.location,ORIGIN+prepare),id=target.pathname.slice('/monday-runs/'.length);
 requireValue(UUID.test(id) && id!==STALE && target.href===ORIGIN+'/monday-runs/'+id);
 const prepared=await page.evaluate(headerProgram);header(prepared,id,['RUNNING','AWAITING_REVIEW','DEVELOPMENT_ROLLING_ORIGIN_V2']);
 const rows=await page.evaluate(`({url:location.href,forecast_count:document.querySelectorAll('pre.development-forecast-evidence-json').length,
   recommendations:[...document.querySelectorAll('form[action*="/recommendations/"][action$="/review"]')].map(f=>{const row=f.closest('tr'),forecasts=row.querySelectorAll('pre.development-forecast-evidence-json'),sections=row.querySelectorAll('.development-forecast-evidence');
     if(forecasts.length!==1||sections.length!==1)throw new Error('preparation forecast inventory refused');
     return {url:new URL(f.getAttribute('action'),location.href).href,
     variant:f.closest('tr').cells[0].childNodes[0].textContent.trim(),cases:f.elements.namedItem('approved_cases').value,loose:f.elements.namedItem('approved_loose_units').value,
     disclosure:sections[0].textContent,forecast:JSON.parse(forecasts[0].textContent)};}),
   blockers:[...document.querySelectorAll('form[action*="/blockers/"][action$="/exclude"]')].map(f=>({url:new URL(f.getAttribute('action'),location.href).href,variant:f.closest('tr').cells[0].textContent.trim(),text:f.closest('tr').cells[2].textContent.trim()}))})`);
 requireValue(rows?.url===target.href && rows.forecast_count===4 && Array.isArray(rows.recommendations) && Array.isArray(rows.blockers) &&
   same(rows.recommendations.map(x=>x.variant).sort(),Object.keys(expectedQuantities)) && same(rows.blockers.map(x=>x.variant).sort(),['4003','4004']));
 const recommendations=[],blockers=[];
 for(const row of rows.recommendations){
   const path='/monday-runs/'+id+'/recommendations/',number=row.url.slice((ORIGIN+path).length).replace(/\/review$/,'');
   requireValue(/^[1-9][0-9]{0,17}$/.test(number) && row.url===ORIGIN+path+number+'/review' && row.cases===expectedQuantities[row.variant][0] && row.loose==='0');
   requireValue(typeof row.disclosure==='string' && ['LOCAL SYNTHETIC DEVELOPMENT ONLY — NO COMMERCIAL AUTHORITY','empirical full-horizon protection',
     'CALCULATED_LIMITED_AVAILABILITY','availability qualification LIMITED','confidence LOW'].every(text=>row.disclosure.includes(text)));
   validatePreparationForecast(row.variant,row.forecast,b.vendors);recommendations.push({variant_id:row.variant,recommendation_id:number});
 }
 for(const row of rows.blockers){
   const path='/monday-runs/'+id+'/blockers/',number=row.url.slice((ORIGIN+path).length).replace(/\/exclude$/,'');
   const reason=row.variant==='4003'?'ROUTINE_SELECTED_OFFER_HEAD_REQUIRED':'LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED';
   requireValue(/^[1-9][0-9]{0,17}$/.test(number) && row.url===ORIGIN+path+number+'/exclude' && row.text.includes(reason));
   blockers.push({variant_id:row.variant,exception_id:number});
 }
 requireValue(new Set(recommendations.map(x=>x.recommendation_id)).size===4 && new Set(blockers.map(x=>x.exception_id)).size===2);
 const observation={contract:'BUFFALO_STAGING_PREPARATION_STEP_OBSERVATION_V1',commit:b.commit,tree:b.tree,fixture_proof_sha256:b.fixture_proof_sha256,
   mapping_proof_sha256:b.mapping_proof_sha256,principal_ref:b.principal_ref,price_batch_id:b.price_batch_id,vendors:b.vendors,
   stale_run_id:STALE,retirement_confirmation_sha256:facts['Confirmation SHA-256'],run_id:id,
   input_fingerprint:prepared.fingerprint,recommendations,blockers,forecasts:rows.recommendations.map(x=>({variant_id:x.variant,sha256:x.forecast.sha256})),
   forecast_evidence_scope:'SERVER_OBSERVED_NOT_INDEPENDENTLY_RECOMPUTED',scope:'PREPARATION_DOM_COMPONENT_ONLY',commercial_authority:false,execution_authority:false};
 const digest=crypto.createHash('sha256').update(JSON.stringify(observation)).digest('hex');
 return {observation,review_binding:{commit:b.commit,tree:b.tree,preparation_proof_sha256:digest,run_id:id,input_fingerprint:prepared.fingerprint,
   principal_ref:b.principal_ref,price_batch_id:b.price_batch_id,recommendations,blockers,vendors:b.vendors}};
}
