// Dormant owner-review/DRAFT DOM component for the existing synthetic V2 run.
// No runtime, login, DB, download filesystem or release authority. Real packet
// validation/restart/restore remain separate gates, never inferred from this UI.
import {ORIGIN,snapshotBinding} from './hosted_synthetic_apply_step.mjs';
import {mappingFormProgram,post,redirect} from './hosted_synthetic_mapping_step.mjs';
const HASH=/^[0-9a-f]{64}$/,OID=/^[0-9a-f]{40}$/,UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const requireValue=v=>{if(!v)throw new Error('staging review component refused');};
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const keys=(v,names)=>v && !Array.isArray(v) && same(Object.keys(v).sort(),[...names].sort());
const CONTROL={
 '1001':{action:'ACCEPT',cases:'6',comment:'',line:'180.00',price:'30.0000',recommended:'6',units:'36',unitPrice:'5.0000',sku:'SUP-001'},
 '4001':{action:'EDIT_QUANTITY',cases:'2',comment:'Cross Western 18 BT multipack break',line:'84.00',price:'42.0000',recommended:'1',units:'8',unitPrice:'10.5000',sku:'WEST-MULTI-4001'},
 '4002':{action:'ACCEPT',cases:'1',comment:'',line:'18.00',price:'18.0000',recommended:'1',units:'4',unitPrice:'4.5000',sku:'WEST-BTL-4002'},
 '4005':{action:'REJECT',cases:'0',comment:'Allocated item remains excluded from internal DRAFT',recommended:'0'},
};
function bindingValue(supplied) {
 const v=snapshotBinding(supplied);
 requireValue(keys(v,['commit','tree','preparation_proof_sha256','run_id','input_fingerprint','principal_ref','price_batch_id','recommendations','blockers','vendors']) &&
   ['commit','tree','preparation_proof_sha256','run_id','input_fingerprint','principal_ref','price_batch_id'].every(k=>typeof v[k]==='string') &&
   OID.test(v.commit) && OID.test(v.tree) && HASH.test(v.preparation_proof_sha256) && HASH.test(v.input_fingerprint) &&
   UUID.test(v.run_id) && UUID.test(v.price_batch_id) && typeof v.principal_ref==='string' && /^owner:[A-Za-z0-9:-]{1,100}$/.test(v.principal_ref));
 for(const [kind,variants,id] of [['recommendations',['1001','4001','4002','4005'],'recommendation_id'],['blockers',['4003','4004'],'exception_id']]){
   requireValue(Array.isArray(v[kind]) && same(v[kind].map(x=>x.variant_id).sort(),variants));
   requireValue(new Set(v[kind].map(x=>x[id])).size===variants.length);
   for(const item of v[kind])requireValue(keys(item,['variant_id',id]) && typeof item[id]==='string' && /^[1-9][0-9]{0,17}$/.test(item[id]));
 }
 requireValue(keys(v.vendors,['southern','western']) && typeof v.vendors.southern==='string' && typeof v.vendors.western==='string' && UUID.test(v.vendors.southern) && UUID.test(v.vendors.western) && v.vendors.southern!==v.vendors.western);
 return v;
}
const runProgram=`({url:location.href,heading:document.querySelector('h1')?.textContent.trim(),
  states:[...document.querySelector('h1').nextElementSibling.querySelectorAll('b')].map(x=>x.textContent.trim()),
  fingerprint:document.querySelector('h1').nextElementSibling.querySelector('code')?.textContent.trim(),
  text:document.body.innerText,
  reviews:[...document.querySelectorAll('form[action*="/recommendations/"][action$="/review"]')].map(f=>({url:new URL(f.getAttribute('action'),location.href).href,
    variant:f.closest('tr').cells[0].childNodes[0].textContent.trim(),cases:f.elements.namedItem('approved_cases').value,loose:f.elements.namedItem('approved_loose_units').value})),
  blockers:[...document.querySelectorAll('form[action*="/blockers/"][action$="/exclude"]')].map(f=>({url:new URL(f.getAttribute('action'),location.href).href,variant:f.closest('tr').cells[0].textContent.trim()})),
  drafts:[...document.querySelectorAll('h3')].filter(h=>h.textContent.endsWith(' — DRAFT')).map(h=>({title:h.textContent.trim(),summary:h.nextElementSibling.textContent,
    rows:[...h.nextElementSibling.nextElementSibling.querySelectorAll('tbody tr')].map(r=>[...r.cells].map(c=>c.childNodes[0].textContent.trim())),
    tiers:[...h.nextElementSibling.nextElementSibling.querySelectorAll('tbody tr')].map(r=>JSON.parse(r.querySelector('pre.draft-final-price-tier-json')?.textContent||'null'))})),
  artifacts:[...document.querySelectorAll('a[href*="/artifacts/"]')].map(a=>({url:a.href,cells:[...a.closest('tr').cells].map(c=>c.textContent.trim())}))})`;
function runState(observed,b,stage) {
 requireValue(observed?.url===ORIGIN+'/monday-runs/'+b.run_id && observed.heading==='Monday run '+b.run_id &&
   same(observed.states,['RUNNING',stage,'DEVELOPMENT_ROLLING_ORIGIN_V2']) && observed.fingerprint===b.input_fingerprint &&
   observed.text.includes('TEST DATA — NOT FOR ORDERING') && observed.text.includes('business date: 2026-10-05'));
}
function dollar(value) {requireValue(typeof value==='string' && /^\$[0-9]+\.[0-9]{2}(?:00)?$/.test(value));return Math.round(Number(value.slice(1))*100);}
function decimalCents(value) {requireValue(typeof value==='string' && /^[0-9]+(?:\.[0-9]{1,4})?$/.test(value));const n=Number(value)*100;requireValue(Number.isSafeInteger(n));return n;}
async function economics(page,control,b) {
 const observed=await page.evaluate(`({url:location.href,title:document.querySelector('h1')?.textContent.trim(),
   entries:[...document.querySelectorAll('dl > dt')].map(dt=>[dt.textContent.trim(),dt.nextElementSibling.textContent.trim()]),
   tiers:[...document.querySelectorAll('pre.final-price-tier-json')].map(n=>JSON.parse(n.textContent))})`);
 const entries=observed.entries;
 requireValue(observed.title==='Confirm recommendation economics' && Array.isArray(entries) && new Set(entries.map(x=>x[0])).size===entries.length);
 const values=Object.fromEntries(entries);
 requireValue(values.Cases===control.cases && values['Loose units']==='0' && dollar(values['Frozen applicable case price'])===dollar('$'+control.price) &&
   values['Ordered units']===control.units && dollar(values['Frozen applicable unit cost'])===dollar('$'+control.unitPrice) &&
   dollar(values['Merchandise total'])===dollar('$'+control.line) && dollar(values['Loose-order fee'])===0 &&
   dollar(values['Recalculated line total'])===dollar('$'+control.line));
 requireValue(Array.isArray(observed.tiers) && observed.tiers.length===1);
 const tier=observed.tiers[0];requireValue(tier.case_price===control.price && tier.unit_price===control.unitPrice);
 if(control===CONTROL['1001'])requireValue(tier.level_type==='BREAK' && tier.break_unit==='CS' && tier.break_qty==='2.0000' && tier.source_price_book_batch_id===b.price_batch_id);
 if(control===CONTROL['4001'])requireValue(tier.level_type==='BREAK' && tier.break_unit==='BT' && tier.break_qty==='18.0000');
 if(control===CONTROL['4002'])requireValue(tier.level_type==='BASE' && tier.break_unit===null && tier.break_qty===null);
 return {amount_cents:dollar(values['Recalculated line total']),tier};
}

export async function runStagingReviewDraftStep(page,supplied) {
 const b=bindingValue(supplied),detail='/monday-runs/'+b.run_id;
 for(const name of ['navigate','evaluate','submit','mark','responseAfter','redirectAfter','armRequestGuard'])requireValue(typeof page[name]==='function');
 await page.navigate(ORIGIN+detail);
 const initial=await page.evaluate(runProgram);runState(initial,b,'AWAITING_REVIEW');
 requireValue(Array.isArray(initial.drafts) && initial.drafts.length===0 && initial.artifacts.length===0 && initial.reviews.length===4 && initial.blockers.length===2);
 for(const item of b.recommendations){
   const matches=initial.reviews.filter(r=>r.url===ORIGIN+detail+'/recommendations/'+item.recommendation_id+'/review');
   requireValue(matches.length===1 && matches[0].variant===item.variant_id && matches[0].cases===CONTROL[item.variant_id].recommended && matches[0].loose==='0');
 }
 for(const item of b.blockers){
   const path=detail+'/blockers/'+item.exception_id+'/exclude';
   requireValue(initial.blockers.filter(x=>x.url===ORIGIN+path && x.variant===item.variant_id).length===1);
   const values={expected_input_fingerprint:b.input_fingerprint,reason:'Acknowledged synthetic blocker for Variant '+item.variant_id+'; omit RUN_ONLY',actor:b.principal_ref};
   await page.evaluate(mappingFormProgram(path,detail,Object.keys(values),{expected_input_fingerprint:b.input_fingerprint},{reason:values.reason,actor:values.actor}));
   redirect(page,path,detail,await post(page,path,detail,values));
 }
 const decisions=[];
 for(const variant of Object.keys(CONTROL)){
   const item=b.recommendations.find(x=>x.variant_id===variant),control=CONTROL[variant],path=detail+'/recommendations/'+item.recommendation_id+'/review';
   const values={expected_input_fingerprint:b.input_fingerprint,action:control.action,approved_cases:control.cases,approved_loose_units:'0',comment:control.comment,actor:b.principal_ref};
   await page.evaluate(mappingFormProgram(path,detail,Object.keys(values),{expected_input_fingerprint:b.input_fingerprint},values));
   const mark=await post(page,path,detail,values);
   if(control.action==='REJECT'){
     redirect(page,path,detail,mark);decisions.push({variant_id:variant,action:'REJECT'});continue;
   }
   requireValue(page.responseAfter(path,'POST',mark)?.status===200);
   const calculation=await economics(page,control,b);
   const form=await page.evaluate(mappingFormProgram(path,path,[...Object.keys(values),'review_preview_fingerprint'],values));
   requireValue(HASH.test(form.fields?.review_preview_fingerprint));
   // An unexpected MATERIAL form has additional fields and refuses here; never
   // bypass its distinct risk confirmation to make the canonical case succeed.
   redirect(page,path,detail,await post(page,path,path,form.fields));
   decisions.push({variant_id:variant,action:control.action,calculation,preview_fingerprint:form.fields.review_preview_fingerprint});
 }
 const reviewed=await page.evaluate(runProgram);runState(reviewed,b,'REVIEWED');
 requireValue(reviewed.reviews.length===0 && reviewed.blockers.length===0 && reviewed.drafts.length===0 && reviewed.artifacts.length===0);
 requireValue(decisions.filter(x=>x.calculation).reduce((sum,x)=>sum+x.calculation.amount_cents,0)===28200);
 const build=detail+'/build',start={actor:b.principal_ref};
 await page.evaluate(mappingFormProgram(build,detail,['actor'],{},start));
 const mark=await post(page,build,detail,start);requireValue(page.responseAfter(build,'POST',mark)?.status===200);
 const preview=await page.evaluate(`({url:location.href,title:document.querySelector('h1')?.textContent.trim(),rows:[...document.querySelectorAll('tbody tr')].map(r=>[...r.cells].map(c=>c.childNodes[0].textContent.trim()))})`);
 requireValue(preview.url===ORIGIN+build && preview.title==='Confirm vendor DRAFT economics' && preview.rows.length===2 && preview.rows.every(r=>r.length===9));
 const expected=new Map([[b.vendors.southern,[18000,6,0,0,0,18000]],[b.vendors.western,[10200,3,0,700,700,10900]]]);
 for(const row of preview.rows){
   const amounts=expected.get(row[0]);requireValue(amounts);expected.delete(row[0]);
   const minimum=/^DOLLAR ([0-9]+(?:\.[0-9]{1,4})?)$/.exec(row[3]);
   requireValue(minimum && decimalCents(minimum[1])===(row[0]===b.vendors.southern?2000:12000) &&
     decimalCents(row[4])===(row[0]===b.vendors.southern?0:1800));
   requireValue(same([dollar(row[1]),Number(row[2]),dollar(row[5]),dollar(row[6]),dollar(row[7]),dollar(row[8])],amounts));
 }
 requireValue(expected.size===0);
 const buildForm=await page.evaluate(mappingFormProgram(build,build,['actor','draft_preview_fingerprint','minimum_disposition'],{actor:b.principal_ref,minimum_disposition:'PAY_FEE'}));
 requireValue(HASH.test(buildForm.fields?.draft_preview_fingerprint));
 redirect(page,build,detail,await post(page,build,build,buildForm.fields));
 const built=await page.evaluate(runProgram);runState(built,b,'PACKET_BUILT');
 requireValue(built.drafts.length===2 && built.reviews.length===0 && built.blockers.length===0 && built.artifacts.length===3);
 const titles=built.drafts.map(x=>x.title).sort();
 requireValue(same(titles,['Synthetic Southern — DRAFT','Synthetic Western Acceptance — DRAFT']));
 const draftIds=new Set(),draftBindings=[];
 for(const draft of built.drafts){
   const southern=draft.title==='Synthetic Southern — DRAFT';
   requireValue(same(draft.rows.map(r=>r[0]).sort(),southern?['1001']:['4001','4002']));
   requireValue(Array.isArray(draft.tiers) && draft.tiers.length===draft.rows.length &&
     draft.rows.every((row,index)=>same(draft.tiers[index],decisions.find(x=>x.variant_id===row[0]).calculation.tier)));
   requireValue(typeof draft.summary==='string');
   const summary=/^PO ([a-f0-9-]+); merchandise \$([^;]+); loose-order fees \$([^;]+); below-minimum fee \$([^;]+); total fees \$([^;]+); total \$([^;]+); minimum shortfall ([^;]+); disposition ([^;]+); confirmed by (.+)$/.exec(draft.summary);
   requireValue(summary && UUID.test(summary[1]) && !draftIds.has(summary[1]) && same(summary.slice(2,7).map(x=>dollar('$'+x)),southern?[18000,0,0,0,18000]:[10200,0,700,700,10900]) &&
     decimalCents(summary[7])===(southern?0:1800) && summary[8]===(southern?'NOT_APPLICABLE':'PAY_FEE') && summary[9]===b.principal_ref);
   draftIds.add(summary[1]);
   draftBindings.push({vendor_id:southern?b.vendors.southern:b.vendors.western,draft_po_id:summary[1]});
 }
 const lines=built.drafts.flatMap(d=>d.rows);requireValue(lines.length===3);
 const seen=new Set();
 for(const line of lines){
   requireValue(line.length===11 && ['1001','4001','4002'].includes(line[0]) && !seen.has(line[0]));seen.add(line[0]);
   const c=CONTROL[line[0]];requireValue(line[1]===c.sku && line[2]===c.cases && line[3]==='0' && line[4]===c.units && dollar(line[5])===dollar('$'+c.unitPrice) &&
     line[6]==='0.0000' && line[7]==='2026-10-05 12:00:00+00:00' &&
     dollar(line[8])===dollar('$'+c.line) && dollar(line[9])===0 && dollar(line[10])===dollar('$'+c.line));
 }
 requireValue(built.text.includes('merchandise $282.00') && built.text.includes('fees $7.00') && built.text.includes('internal DRAFT total $289.00'));
 const urls=new Set(),artifactKinds=new Set(['VENDOR_INTERNAL_CSV:'+b.vendors.southern,'VENDOR_INTERNAL_CSV:'+b.vendors.western,'EMERGENCY_REVIEW_PACKET:—']);
 for(const a of built.artifacts){
   requireValue(typeof a.url==='string' && a.url.startsWith(ORIGIN+detail+'/artifacts/') && /^[1-9][0-9]*$/.test(a.url.slice((ORIGIN+detail+'/artifacts/').length)) && !urls.has(a.url));urls.add(a.url);
   requireValue(Array.isArray(a.cells) && a.cells.length===4 && typeof a.cells[2]==='string' && /^[1-9][0-9]{0,9}$/.test(a.cells[2]));
   const kind=a.cells[0]+':'+a.cells[1];requireValue(artifactKinds.has(kind));artifactKinds.delete(kind);
 }
 requireValue(artifactKinds.size===0);
 return {contract:'BUFFALO_STAGING_REVIEW_DRAFT_STEP_OBSERVATION_V1',commit:b.commit,tree:b.tree,run_id:b.run_id,
   preparation_proof_sha256:b.preparation_proof_sha256,decisions,draft_preview_fingerprint:buildForm.fields.draft_preview_fingerprint,
   artifacts:built.artifacts,draft_ids:[...draftIds].sort(),draft_bindings:draftBindings.sort((a,b)=>a.vendor_id.localeCompare(b.vendor_id)),observed_controls:{drafts:2,lines:3,merchandise_cents:28200,fee_cents:700,total_cents:28900},
   packet_members:'NOT_DOWNLOADED_OR_VALIDATED',restart_restore:'NOT_RUN',scope:'REVIEW_DRAFT_DOM_COMPONENT_ONLY',commercial_authority:false,execution_authority:false};
}
