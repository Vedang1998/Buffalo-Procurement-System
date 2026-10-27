// Inert staging APPLY component. No CLI, login, network client, credentials,
// database or release grant. A reviewed driver must supply the guarded page and
// trusted expected intent + verified Backup V2 public bindings, never DOM-derived
// expectations. Scripted adapter tests are not actual browser acceptance.
import crypto from 'node:crypto';

export const ORIGIN = 'https://staging.example.test';
const HASH = /^[0-9a-f]{64}$/;
const OID = /^[0-9a-f]{40}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const CSRF = '_buffalo_staging_csrf';
const PREVIEW_FIELDS = [CSRF, 'actor', 'apply_idempotency_key'].sort();
const APPLY_FIELDS = [...PREVIEW_FIELDS, 'confirm', 'expected_preview_sha256'].sort();
const INTENT_FIELDS = [
  'contract', 'price_book_batch_id', 'vendor_id', 'price_scope_key', 'apply_idempotency_key',
  'prior_event_id', 'expected_prior_head_version', 'expected_current_scope_sha256',
  'resulting_current_scope_sha256', 'scope_membership_sha256', 'raw_content_sha256',
  'declaration_sha256', 'policy_ref', 'policy_sha256', 'confirmation_payload_sha256',
  'price_book_promotion_event_id', 'expected_current_row_count', 'resulting_current_row_count',
  'unaffected_price_state_sha256', 'backup_manifest_sha256', 'backup_prechange_scope_sha256',
  'application_at', 'variant_ids', 'principal_ref', 'role_ref', 'commercial_authority',
  'real_price_approval', 'staging_backup_release_sha256', 'staging_runtime_attestation_identity',
].sort();
const BACKUP_FIELDS = ['target_kind','manifest_sha256','prechange_scope_sha256','batch_id',
  'vendor_id','price_scope_key','prior_event_id','prior_head_version','raw_content_sha256',
  'staging_release_sha256','runtime_attestation_identity'].sort();
const sha = (text) => crypto.createHash('sha256').update(text, 'utf8').digest('hex');
const requireValue = (value) => { if (!value) throw new Error('staging APPLY component refused'); };
const fieldsEqual = (actual, expected) => keysEqual(actual, Object.keys(expected).sort()) && Object.entries(expected).every(([k,v]) => actual[k] === v);
const keysEqual = (value, keys) => value !== null && typeof value === 'object' && !Array.isArray(value) &&
  JSON.stringify(Object.keys(value).sort()) === JSON.stringify(keys);

// This finite intent contains only ASCII strings, bounded integers, booleans and
// Variant string arrays. Thus it matches the existing Python UTF8/sortedcompact
// canonical_sha256 without JS float/unicode/key-order ambiguity.
export function canonicalIntent(value) {
  requireValue(keysEqual(value, INTENT_FIELDS));
  for (const item of Object.values(value)) {
    if (typeof item === 'string') requireValue(/^[\x20-\x7e]{1,256}$/.test(item));
    else if (typeof item === 'number') requireValue(Number.isSafeInteger(item) && item >= 0);
    else if (typeof item === 'boolean') continue;
    else requireValue(Array.isArray(item) && item.length > 0 && item.length <= 100 &&
      item.every(v => typeof v === 'string' && /^[1-9][0-9]{0,19}$/.test(v)));
  }
  return JSON.stringify(Object.fromEntries(INTENT_FIELDS.map(key => [key, value[key]])));
}

export function snapshotBinding(value) {
  let entries = 0;
  const copy = (selected, depth = 0) => {
    requireValue(++entries <= 1000 && depth <= 5);
    if (selected === null || ['string','number','boolean'].includes(typeof selected)) return selected;
    requireValue(typeof selected === 'object' && Object.getOwnPropertySymbols(selected).length === 0);
    const array = Array.isArray(selected), prototype = Object.getPrototypeOf(selected);
    requireValue(array ? prototype === Array.prototype : [Object.prototype,null].includes(prototype));
    const descriptors = Object.getOwnPropertyDescriptors(selected);
    const names = Object.keys(descriptors).filter(name => !(array && name === 'length'));
    requireValue(names.every(name => descriptors[name].enumerable && 'value' in descriptors[name]));
    if (array) requireValue(selected.length <= 100 && names.length === selected.length && names.every((name,index) => name === String(index)));
    const copied = names.map(name => [name,copy(descriptors[name].value,depth+1)]);
    return array ? copied.map(([,item]) => item) : Object.fromEntries(copied);
  };
  const cloned = copy(value), raw = JSON.stringify(cloned);
  requireValue(Buffer.byteLength(raw) <= 16384);
  return JSON.parse(raw);
}

export function armSinglePost(page, pathname, fields, csrfSha256) {
  let attempted = false, refused = false;
  // Adapter contract: intercept ALL mutations before forwarding. Closing this
  // allowance restores its existing deny policy; it never disables the guard.
  const receipt = page.armRequestGuard(request => {
    if (attempted) { refused = true; return false; }
    attempted = true;
    const matches = applyRequestMatches(request,pathname,fields,csrfSha256);
    refused ||= !matches;
    return matches;
  });
  requireValue(receipt && typeof receipt.matches === 'function' && typeof receipt.close === 'function');
  return {verify() {requireValue(attempted && !refused && JSON.stringify(receipt.matches()) === '[true]');},
    close() {receipt.close();}};
}

function validateBinding(value) {
  requireValue(keysEqual(value, ['backup','commit','confirmation_proof_sha256','intent','operator_proof_sha256','tree']));
  const intent = value.intent, backup = value.backup;
  canonicalIntent(intent);
  requireValue(['commit','tree','operator_proof_sha256','confirmation_proof_sha256'].every(k => typeof value[k] === 'string') &&
    OID.test(value.commit) && OID.test(value.tree) && HASH.test(value.operator_proof_sha256) &&
    HASH.test(value.confirmation_proof_sha256) && keysEqual(backup, BACKUP_FIELDS) && backup.target_kind === 'staging');
  requireValue(intent.contract === 'BUFFALO_SYNTHETIC_PRICE_APPLY_PREVIEW_V1' &&
    UUID.test(intent.price_book_batch_id) && UUID.test(intent.vendor_id) && UUID.test(intent.prior_event_id) &&
    intent.price_scope_key === 'COMPLETE_VENDOR' && intent.expected_current_row_count === 4 &&
    intent.resulting_current_row_count === 4 && Number.isSafeInteger(intent.expected_prior_head_version) && intent.expected_prior_head_version > 0 &&
    Number.isSafeInteger(intent.price_book_promotion_event_id) && intent.price_book_promotion_event_id > 0 && intent.commercial_authority === false && intent.real_price_approval === false &&
    intent.role_ref === 'procurement.price.approve' && /^owner:[A-Za-z0-9:-]{1,100}$/.test(intent.principal_ref) &&
    /^[A-Za-z0-9_-]{1,100}$/.test(intent.apply_idempotency_key) &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$/.test(intent.application_at) &&
    Array.isArray(intent.variant_ids) && new Set(intent.variant_ids).size === intent.variant_ids.length &&
    JSON.stringify([...intent.variant_ids].sort()) === JSON.stringify(intent.variant_ids));
  for (const [key, selected] of Object.entries(intent)) {
    if (key.endsWith('_sha256') || key === 'staging_runtime_attestation_identity') requireValue(HASH.test(selected));
  }
  const pairs = [['manifest_sha256','backup_manifest_sha256'], ['prechange_scope_sha256','backup_prechange_scope_sha256'],
    ['batch_id','price_book_batch_id'], ['vendor_id','vendor_id'], ['price_scope_key','price_scope_key'],
    ['prior_event_id','prior_event_id'], ['prior_head_version','expected_prior_head_version'],
    ['raw_content_sha256','raw_content_sha256'], ['staging_release_sha256','staging_backup_release_sha256'],
    ['runtime_attestation_identity','staging_runtime_attestation_identity']];
  requireValue(pairs.every(([left,right]) => backup[left] === intent[right]));
}

function exactUrl(raw, pathname) {
  try { const u = new URL(raw); return u.href === ORIGIN + pathname && !u.username && !u.password; }
  catch { return false; }
}

export function applyRequestMatches(request, pathname, expectedFields, csrfSha256) {
  try {
    if (request.method !== 'POST' || !exactUrl(request.url, pathname) || typeof request.postData !== 'string' ||
      Buffer.byteLength(request.postData) > 4096 || !HASH.test(csrfSha256)) return false;
    const form = new URLSearchParams(request.postData);
    const expectedNames = [CSRF, ...Object.keys(expectedFields)].sort();
    return JSON.stringify([...form.keys()].sort()) === JSON.stringify(expectedNames) &&
      expectedNames.every(key => form.getAll(key).length === 1) &&
      /^[A-Za-z0-9_-]{43}$/.test(form.get(CSRF) || '') && sha(form.get(CSRF)) === csrfSha256 &&
      Object.entries(expectedFields).every(([key, val]) => form.get(key) === val);
  } catch { return false; }
}

function formProgram(pathname, fieldNames, expected, submit) {
  return `(async () => {
    const forms = [...document.forms].filter(f => f.getAttribute('action') && new URL(f.action).href === ${JSON.stringify(ORIGIN + pathname)});
    if (forms.length !== 1) throw new Error('APPLY form count refused');
    const form = forms[0];
    const inspect = () => {
      const pairs = [...new FormData(form)];
      if (form.method !== 'post' || form.enctype !== 'application/x-www-form-urlencoded' ||
          !form.isConnected || new URL(form.action).href !== ${JSON.stringify(ORIGIN + pathname)} ||
          location.href !== ${JSON.stringify(ORIGIN + (pathname.endsWith('/apply-preview') ? pathname.slice(0,-14) : pathname+'-preview'))} ||
          !['','_self'].includes(form.target) || pairs.some(([k,v]) => typeof v !== 'string') ||
          JSON.stringify(pairs.map(([k]) => k).sort()) !== ${JSON.stringify(JSON.stringify(fieldNames))})
        throw new Error('APPLY form fields refused');
      return pairs;
    };
    inspect();
    const expected = ${JSON.stringify(expected)};
    const pairs = inspect(), values = Object.fromEntries(pairs);
    if (!/^[A-Za-z0-9_-]{43}$/.test(values[${JSON.stringify(CSRF)}] || '') ||
        Object.entries(expected).some(([k,v]) => values[k] !== v)) throw new Error('APPLY form binding refused');
    const raw = values[${JSON.stringify(CSRF)}];
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(raw));
    const csrfSha256 = [...new Uint8Array(digest)].map(v=>v.toString(16).padStart(2,'0')).join('');
    if (JSON.stringify(inspect()) !== JSON.stringify(pairs)) throw new Error('APPLY form changed');
    ${submit ? `if (csrfSha256 !== ${JSON.stringify(submit.csrfSha256 || '')}) throw new Error('APPLY CSRF changed');
    HTMLFormElement.prototype.requestSubmit.call(form);` : ''}
    return {fields: Object.fromEntries(pairs.filter(([k]) => k !== ${JSON.stringify(CSRF)})), csrfSha256};
  })()`;
}

const detailProgram = `({url:location.href,
  durable:document.querySelector('#price-book-durable-status')?.textContent.trim(),
  operational:document.querySelector('#price-book-operational-status')?.textContent.trim(),
  basis:document.querySelector('#price-book-operational-status')?.dataset.temporalBasis,
  hasLegacy:!!document.querySelector('[name="review_token"],input[type="file"]'),
  hasConfirmation:!!document.querySelector('form[action$="/confirmation-preview"]'),
  hasApply:!!document.querySelector('form[action$="/apply-preview"]')})`;

export async function runStagingApplyStep(page, supplied) {
  // Freeze a bounded value copy before the first await; caller mutation cannot
  // change the reviewed expectation between preview and APPLY.
  const binding = snapshotBinding(supplied);
  validateBinding(binding);
  const intent = binding.intent, batch = intent.price_book_batch_id;
  const detail = `/price-books/${batch}`, previewPath = detail + '/apply-preview', applyPath = detail + '/apply';
  for (const name of ['evaluate','submit','mark','responseAfter','redirectAfter','armRequestGuard']) requireValue(typeof page[name] === 'function');
  const before = await page.evaluate(detailProgram);
  requireValue(exactUrl(before.url, detail) && before.durable === 'VERIFIED_FUTURE' && before.operational === 'VERIFIED_FUTURE' &&
    before.basis === 'REGISTERED_OBSERVATION' && !before.hasLegacy && !before.hasConfirmation && before.hasApply);
  const previewValues = {actor:intent.principal_ref, apply_idempotency_key:intent.apply_idempotency_key};
  // Fill only these two ordinary fields, then inspect gateway-owned CSRF.
  const fill = `(async () => { const f=document.querySelector('form[action$="/apply-preview"]');
    if (!f) throw new Error('APPLY preview absent');
    for (const [k,v] of Object.entries(${JSON.stringify(previewValues)})) {const x=f.elements.namedItem(k);if(!x)throw new Error('APPLY preview field absent');x.value=v;}
    return true; })()`;
  await page.evaluate(fill);
  const previewForm = await page.evaluate(formProgram(previewPath, PREVIEW_FIELDS, previewValues, false));
  requireValue(HASH.test(previewForm?.csrfSha256) && fieldsEqual(previewForm.fields, previewValues));
  let ledger = page.mark();
  let receipt = armSinglePost(page,previewPath,previewValues,previewForm.csrfSha256);
  try { await page.submit(formProgram(previewPath,PREVIEW_FIELDS,previewValues,{csrfSha256:previewForm.csrfSha256}));
    receipt.verify();
  } finally { receipt.close(); }
  requireValue(page.responseAfter(previewPath,'POST',ledger)?.status === 200);
  const preview = await page.evaluate(`JSON.parse(document.querySelector('#synthetic-price-apply-json').textContent)`);
  requireValue(keysEqual(preview,[...INTENT_FIELDS,'preview_sha256'].sort()));
  const actual = {...preview}; delete actual.preview_sha256;
  const previewSha = sha(canonicalIntent(actual));
  requireValue(HASH.test(preview.preview_sha256) && preview.preview_sha256 === previewSha && canonicalIntent(actual) === canonicalIntent(intent));
  const applyValues = {actor:intent.principal_ref,apply_idempotency_key:intent.apply_idempotency_key,
    confirm:'CONFIRM',expected_preview_sha256:previewSha};
  const applyForm = await page.evaluate(formProgram(applyPath,APPLY_FIELDS,applyValues,false));
  requireValue(HASH.test(applyForm?.csrfSha256) && fieldsEqual(applyForm.fields, applyValues));
  ledger = page.mark();
  receipt = armSinglePost(page,applyPath,applyValues,applyForm.csrfSha256);
  try { await page.submit(formProgram(applyPath,APPLY_FIELDS,applyValues,{csrfSha256:applyForm.csrfSha256}));
    receipt.verify();
  } finally { receipt.close(); }
  const redirect = page.redirectAfter(applyPath,'POST',ledger);
  requireValue(redirect?.status === 303 && typeof redirect.location === 'string' && exactUrl(new URL(redirect.location,ORIGIN).href,detail));
  const after = await page.evaluate(detailProgram);
  requireValue(exactUrl(after.url,detail) && after.durable === 'APPLIED_CURRENT' && after.operational === 'APPLIED_CURRENT' &&
    after.basis === 'REGISTERED_OBSERVATION' && !after.hasLegacy && !after.hasConfirmation && !after.hasApply);
  return {contract:'BUFFALO_STAGING_APPLY_STEP_OBSERVATION_V1',commit:binding.commit,tree:binding.tree,
    batch_id:batch,preview_sha256:previewSha,backup_manifest_sha256:intent.backup_manifest_sha256,
    operator_proof_sha256:binding.operator_proof_sha256,confirmation_proof_sha256:binding.confirmation_proof_sha256,
    observed_status:'APPLIED_CURRENT',scope:'APPLY_STEP_ONLY',commercial_authority:false,execution_authority:false};
}
