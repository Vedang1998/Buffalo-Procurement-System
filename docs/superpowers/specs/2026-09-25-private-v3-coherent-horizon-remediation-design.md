# Private V3 Coherent-Horizon Remediation Design

**Approved architecture:** 2026-09-25
**Risk:** Level 3 — forecasting / procurement logic
**Design-spec writer:** Codex
**Implementation status:** NOT AUTHORIZED / NOT STARTED

## 1. Objective and authority boundary

Create a versioned, additive corrected-V3 research child that:

1. binds the reviewed 43-row exact-Variant-ID creation-evidence delta and
   reclassifies those Variants from `BLOCKED` to `NOT_APPLICABLE` for the full
   138-day research window; and
2. replaces contradictory independently selected H3/H10/H17 displayed results
   with one empirically selected, nonnegative, coherent 17-day research path.

This design does not authorize implementation. A later implementation remains
private development research only. It grants no ABC, inventory-position,
incoming, open-PO, supplier-calendar, lead-time, pack, price, deal, supplier,
purchase, DRAFT, PO, order, Shopify-write, deployment, or production authority.

`target_units` remains a research order-up-to demand/protection target. It is
not an order quantity, case quantity, recommended buy, or selected-supplier
quantity. Inventory subtraction, trusted incoming, open-PO netting, case/pack
rounding, supplier selection, and commercial economics remain downstream gates.

## 2. Exact immutable parent and baseline identities

The corrected child must bind, not mutate, these accepted identities:

- accepted repository commit:
  `008f8b5f22184a2e43247636e1b7cb4ba3523696`;
- accepted repository tree:
  `855e3d73ada509327d7cfa02f50da9260912ad07`;
- runtime/browser baseline commit:
  `a438a69cb05dd4f7dfb990e14de33f208dab7ccf`;
- runtime/browser baseline tree:
  `2dacc029f088a7d44feb537d9784dc9f6a6924dd`;
- validated test-only child:
  `62383f48aae9bbd401754bba543659c2b6543023`;
- validated test-only child tree:
  `686f8e5744f4f40bd916fb8f29c502a36508ca0b`;
- accepted V3 input identity:
  `f4f881df40ef5b7275a3ae2b15f3e7004e08e629916a40f71c651687abebe410`;
- accepted V3 projection identity:
  `b200deb0b6fd0a2f1c133114bf6b3a6db1eccc7d6e44deaf738dcdd864c11ae4`;
- accepted V3 workspace identity:
  `c5a71f4798eb2ed3e948f2bbc20097151ad92199b3b72f30276992e8150f2968`.

The complete immutable parent tuple is:

| Object | Logical ID / key | Bytes | Canonical/file SHA-256 |
|---|---|---:|---|
| Accepted V3 input | `private-research/v3-inputs/f4f881df40ef5b7275a3ae2b15f3e7004e08e629916a40f71c651687abebe410.json` | 43,004,111 | `6aaa17df12634df2532e07b3945680e9d7a8d5ef3b79ab49d6eafb2b3eaf71c0` |
| Parent V2 input | `private-research/v2-inputs/71da1905d6871490b171ae2d5326fa9158c41ee0b9d66b29b47f7397a738dae1.json` | 9,626,244 | `b1a5f9a77efcb704aa0cb51f7417c2f8e6f687c1001a7741df698714f63c4de8` |
| Base intake | `private-research/intakes/9a40f2d661570c27ff3b30eff8d20002e9aa47af6d19d7af9be2133e127f1a6b.json` | 50,387,524 | `712f59fc9ac27b75305db22099457d94195b3e18ae1ee8a11cb544245d257441` |
| Accepted workspace manifest | `private-research/workspaces/c5a71f4798eb2ed3e948f2bbc20097151ad92199b3b72f30276992e8150f2968/manifest.json` | 4,984 | `6c86e26bd49983ceacc4898b98e2180ec63846c7aaf84cb95930d4af328f1a0a` |

The accepted projection has two distinct identities and they must never be
conflated:

- logical projection SHA-256:
  `b200deb0b6fd0a2f1c133114bf6b3a6db1eccc7d6e44deaf738dcdd864c11ae4`;
- serialized `projection.json` artifact SHA-256:
  `f47e81ad868a8dfada60055e3a3ac9b61aebf9a733c28aacea4af910388d727b`.

The accepted raw three-horizon sidecar inventory logical SHA-256 is
`2b4b1c39fec4bd259d8b7ff52be580e4823d9723fbdddd7008fcff786dfed8be`.
The exact accepted parent forecast tuple is evidence contract
`BUFFALO_DEVELOPMENT_FORECAST_EVIDENCE_V2`, policy contract
`BUFFALO_DEVELOPMENT_FORECAST_POLICY_V2`, method
`DEVELOPMENT_ROLLING_ORIGIN_V2`, profile `development-forecast-v2`, source SHA
`fee2e91e14565a835f1d69c27ff547ccdda5c22c6bc43a2bb003c2b78f190b52`, and
canonical SHA
`ff62b1d313a18a907e811e8722431efee2349fc95a31e08ea5843ca2664ca858`.
The parent source identity remains
`bd8d6dfc014b3899366ac1378aeb92b21ac17164b84132a367e7f61a34db39d1`,
with registered source-identity file SHA
`35055e3453367f44781f3929f9532ed3b6fb27e87ac8041c9a78775de079a245`
and source-evidence SHA
`8ad9476283fad379910c4c03f68d5c63179e50128440671410d45cf115de618a`.
Every corrected build rehashes the accepted manifest, all four accepted
artifacts, all three parent input files, and this raw sidecar inventory before
work and again after publication. Any pre/post byte, mode, owner, path, or hash
change aborts the corrected build.

The accepted artifacts remain immutable:

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `coverage.json` | 11,151,899 | `1919e2465f7fe8e559a940eb66f435d18dfcd06537df1accc75ac38098592982` |
| `owner-preview.html` | 21,248,489 | `df8ffb0f4ff635c319eab5d848bb71808b68395e12a113e0ce4252d294bb668d` |
| `owner-worksheet.csv` | 24,717,127 | `0c4adf709cd18b68a64642df5fbaf1b6ab5083a274552a7b42f39164db594b55` |
| `projection.json` | 230,669,882 | `f47e81ad868a8dfada60055e3a3ac9b61aebf9a733c28aacea4af910388d727b` |

No implementation may rewrite, replace, relabel, or silently regenerate those
bytes. The existing V1, V2, and V3 contracts, validators, registered policies,
and byte-golden behavior remain readable and unchanged.

Every corrected input, projection, workspace-ID preimage, and workspace
manifest contains one `implementation_lineage` mapping with exactly these
keys:

```text
accepted_commit, accepted_tree, runtime_commit, runtime_tree,
test_commit, test_tree, implementation_commit, implementation_tree
```

The first six values are the exact lowercase Git object IDs listed above. The
last two are the exact clean implementation commit and tree used to construct
the corrected private output; they are populated only by a future authorized
implementation, must be lowercase Git object IDs, and the implementation
commit must descend from this committed design and the accepted lineage.
Replay verifies all eight objects from the local complete repository history.
Changing any lineage value changes the corrected input and workspace identity;
a caller-supplied or unverifiable lineage is refused.

## 3. Additive successor contracts

The implementation must use new exact contract identities rather than changing
the meaning of an existing contract:

- creation delta:
  `BUFFALO_PRIVATE_VARIANT_CREATION_EVIDENCE_DELTA_V1`;
- joint policy:
  `BUFFALO_DEVELOPMENT_FORECAST_JOINT_HORIZON_POLICY_V1`;
- joint forecast evidence:
  `BUFFALO_DEVELOPMENT_FORECAST_JOINT_HORIZON_EVIDENCE_V1`;
- corrected private input:
  `BUFFALO_PRIVATE_DEVELOPMENT_FORECAST_RESEARCH_INPUT_V3_CORRECTED_V1`;
- corrected projection:
  `BUFFALO_PRIVATE_RESEARCH_PROJECTION_V3_CORRECTED_V1`;
- corrected coverage export:
  `BUFFALO_PRIVATE_RESEARCH_COVERAGE_EXPORT_V3_CORRECTED_V1`;
- corrected workspace:
  `BUFFALO_PRIVATE_REAL_RESEARCH_WORKSPACE_V3_CORRECTED_V1`.

The data mode remains
`PRIVATE_REAL_DATA_DEVELOPMENT_RESEARCH_ONLY`. The joint-policy, corrected
projection, and corrected-sidecar envelopes explicitly set commercial
authority and production activation to false. The delta and corrected-input
envelopes express the same boundary through their exact review-only authority
and integer-zero effects maps; the workspace manifest uses its exact
operational-authority and Boolean-zero maps. No envelope implies commercial or
production authority.

The corrected input binds the accepted V3 input ID and canonical SHA-256. The
corrected projection additionally binds the accepted V3 projection ID and
canonical SHA-256. The corrected workspace binds the corrected input,
corrected projection, accepted base intake, accepted parent identities, and all
rendered artifact hashes. Unknown, partial, mixed, or legacy tuples do not fall
back to another version.

### 3.1 Canonical bytes, self-identities, and storage keys

Corrected source envelopes, corrected inputs, workspace manifests, coverage
JSON, and serialized projection artifacts use exactly this canonical JSON
encoding: recursive string-key sorting, compact separators `,` and `:`,
`ensure_ascii=false`, `allow_nan=false`, UTF-8, and exactly one trailing LF.
The creation envelope ID and corrected input ID are SHA-256 of those canonical
bytes with their own ID field present as JSON `null`.

Logical projection hashes and corrected joint-sidecar hashes use recursive
string-key sorting, compact separators, `ensure_ascii=true`, `allow_nan=false`,
ASCII bytes, and no trailing LF. The respective
`projection_sha256`/`sidecar_sha256` field is omitted from the hash preimage and
added only after hashing. Serialized `projection.json` remains the LF-terminated
UTF-8 artifact encoding above, so its file hash is intentionally distinct from
its logical projection hash.

Corrected private keys are exact:

- raw delta:
  `private-research/corrected-v3-sources/sha256/<raw_csv_sha256>.csv`;
- normalized delta envelope:
  `private-research/corrected-v3-deltas/<delta_id>.json`;
- corrected input:
  `private-research/corrected-v3-inputs/<input_id>.json`; and
- corrected workspace:
  `private-research/workspaces/<workspace_id>/`.

The corrected input's exact top-level key set is:

```text
contract, data_mode, authority, input_id, parent_v3_input,
parent_v2_input, base_intake, parent_projection,
creation_evidence_delta, joint_policy, coverage_controls,
implementation_lineage, limitations, zero_authority
```

`parent_v3_input` and `parent_v2_input` contain exactly
`contract,input_id,sha256,storage_key`; `base_intake` contains exactly
`contract,intake_id,sha256,storage_key`. `parent_projection` contains exactly
`contract,projection_sha256,artifact_sha256,artifact_path,workspace_id,workspace_manifest_sha256,sidecars_sha256`.
`creation_evidence_delta` contains exactly
`contract,delta_id,raw_csv_sha256,normalized_row_set_sha256,raw_storage_key,envelope_storage_key`.
`joint_policy` contains exactly
`evidence_contract,policy_contract,method_version,policy_source_sha256,policy_canonical_sha256`.
`implementation_lineage` is the exact eight-key mapping in Section 2; the same
canonical mapping is copied into the corrected projection, workspace-ID
preimage, and manifest.
`coverage_controls` contains exactly
`current_catalog_count,eligible_count,not_applicable_count,blocked_count,not_processed_count,prior_blocked_reclassified_count,noneligible_reason_counts,membership_controls`.
It fixes the 2,009 current population, 1,365 eligible population, 644
not-applicable population, zero blocked/not-processed populations, and the
exact noneligible reason partition: 643
`VARIANT_CREATED_AFTER_HISTORY_START_FULL_WINDOW_NOT_SUPPORTED` plus one
`VARIANT_CREATED_DURING_FIRST_HISTORY_DAY_FULL_DAY_ZERO_NOT_SUPPORTED`
conservative no-timezone case. It also records the prior
43-blocked to corrected-43-not-applicable transition.
`noneligible_reason_counts` has exactly those two reason keys and integer
counts. `membership_controls` is the exact six-key count/hash mapping defined
for the corrected projection below. Limitations are sorted unique strings and
`zero_authority` is the exact ten-key integer-zero effects map in Section 3.2.

Counts never substitute for membership. Each membership control is SHA-256 of
the LF-terminated canonical JSON array of canonical Variant-ID strings sorted
numerically, using Section 3.1 encoding:

| Membership | Count | SHA-256 |
|---|---:|---|
| Current catalog / coverage / worksheet | 2,009 | `1b1a308c472ea28f4d675efc8bb3f59090266684747c8a584a24c3ba1ba4bf66` |
| Parent and corrected eligible / `CALCULATED` / sidecar IDs | 1,365 | `efb12d99862910406191237d55b5411c163eb27036f76dd5c39df98ec89eded4` |
| Parent `NOT_APPLICABLE` IDs | 601 | `3a1887f9f90cf31a23af5d5ec10be6f5eadec3f4a4e0568bb9dbe3c82cbf16a4` |
| Reviewed prior `BLOCKED` delta IDs | 43 | `1d862a029f9474466ea9671f4ff1538cfef4b46ec799e0e6965824575eea8c2c` |
| Corrected `NOT_APPLICABLE` IDs | 644 | `596f8242da41ae7b9a068d8957e52f53889becd8ad7e74acfe0357220555e2e7` |
| Corrected `BLOCKED` IDs | 0 | `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` |

The corrected not-applicable set must equal the disjoint union of the exact
parent 601 and exact reviewed 43; the eligible set is unchanged from the
validated parent. These memberships must be identical for H3, H10, and H17.

The four corrected artifacts are rendered first. The corrected workspace ID is
SHA-256 over the LF-terminated canonical JSON object below, with artifact
records sorted by `name`:

```text
{
  "contract": <corrected workspace contract>,
  "implementation_lineage": {
    "accepted_commit": ..., "accepted_tree": ...,
    "runtime_commit": ..., "runtime_tree": ...,
    "test_commit": ..., "test_tree": ...,
    "implementation_commit": ..., "implementation_tree": ...
  },
  "corrected_input": {"input_id": ..., "sha256": ...},
  "parent_v3_input": {"input_id": ..., "sha256": ...},
  "parent_v2_input": {"input_id": ..., "sha256": ...},
  "base_intake": {"intake_id": ..., "sha256": ...},
  "parent_projection": {
    "projection_sha256": ..., "artifact_sha256": ...
  },
  "creation_evidence_delta": {
    "delta_id": ..., "raw_csv_sha256": ...,
    "normalized_row_set_sha256": ...
  },
  "joint_policy": {"policy_canonical_sha256": ...},
  "corrected_projection": {"contract": ..., "projection_sha256": ...},
  "artifacts": [
    {"name": ..., "path": ..., "media_type": ..., "bytes": ..., "sha256": ...}
  ]
}
```

Consequently, any renderer byte change creates a different workspace ID and
cannot collide under an existing content-addressed directory. The manifest
repeats the exact four sorted artifact records. A synthetic known-answer test
freezes this preimage, canonical bytes, workspace ID, and sensitivity to each
artifact hash.

The corrected workspace manifest exact top-level key set is:

```text
contract, data_mode, authority, operational_authority, workspace_id,
corrected_input_id, corrected_input_key, corrected_input_sha256,
parent_v3_input_id, parent_v3_input_key, parent_v3_input_sha256,
parent_v2_input_id, parent_v2_input_key, parent_v2_input_sha256,
base_intake_id, base_intake_key, base_intake_sha256,
parent_projection_sha256, parent_projection_artifact_sha256,
creation_delta_id, creation_delta_raw_sha256,
creation_delta_normalized_row_set_sha256, implementation_lineage,
joint_policy_canonical_sha256, projection_contract, projection_sha256,
artifacts, limitations, zero_authority
```

### 3.2 Exact authority and zero-effect values

The delta envelope and corrected input `authority` value is exactly:

```text
{
  "status":"REVIEW_ONLY", "approval_status":"UNAPPROVED",
  "operational_use":"PROHIBITED", "mapping_authority":false,
  "price_authority":false, "selection_authority":false,
  "inventory_authority":false, "forecast_authority":false,
  "procurement_authority":false, "shopify_write_authority":false,
  "po_authority":false
}
```

The delta, corrected input, corrected projection, and each corrected sidecar
use this exact `zero_authority` value, whose values are JSON integers—not
Booleans:

```text
{
  "database_writes":0, "mapping_approvals":0, "price_approvals":0,
  "selected_offers":0, "activated_prices":0, "inventory_writes":0,
  "forecast_authorizations":0, "shopify_writes":0,
  "supplier_messages":0, "po_actions":0
}
```

The corrected projection and sidecars additionally use authority literal
`ZERO_AUTHORITY_RESEARCH_ONLY`, `research_only:true`,
`commercial_authority:false`, and `production_activation:false`. The joint
policy authority is exactly
`{"commercial_authority":false,"production_activation":false}`.

The workspace manifest uses authority literal
`PRIVATE_REAL_SOURCE_REVIEW_ONLY`, `operational_authority:false`, and this
separate exact Boolean `zero_authority` value:

```text
{
  "commercial_authority":false, "mapping_approval":false,
  "price_approval":false, "forecast_policy_approval":false,
  "draft_or_po_authority":false, "shopify_write_authority":false
}
```

Missing/extra keys, `true`, nonzero integers, or JSON `0` substituted for a
required Boolean `false` fail closed.

## 4. Reviewed 43-row evidence delta

The supplied private CSV is:

- filename: `capture-43-exact-id-evidence-delta.csv`;
- size: 10,777 bytes;
- SHA-256:
  `97e29c2896bc98470e11b99518aee729d0febf751f09d20319ce2362473f9d8e`;
- rows: 43;
- unique exact Shopify Variant IDs: 43.

Its reviewed controls are:

- the exact ID set equals the accepted 43 Variants whose prior reason is
  `EXACT_CURRENT_VARIANT_ABSENT_FROM_REVIEWED_CREATION_EVIDENCE`;
- every prior primary status is `BLOCKED`;
- every reviewed primary status is `NOT_APPLICABLE`;
- every reviewed reason is
  `VARIANT_CREATED_AFTER_HISTORY_START_FULL_WINDOW_NOT_SUPPORTED`;
- every history start is `2026-05-04`;
- every creation timestamp is timezone-aware UTC and later than the history
  start;
- forecast recalculation for a full 138-day window is `NO` for all 43; and
- owner review required is `NO` for all 43.

Those two `NO` values approve only the reviewed full-window ineligibility
disposition for the 43 identities. They do not approve this new forecast policy,
runtime implementation, purchasing semantics, or production activation.

The exact CSV field order is:

1. `shopify_variant_id`;
2. `product_title`;
3. `variant_title`;
4. `original_primary_status`;
5. `original_reason`;
6. `live_exact_id_created_at`;
7. `live_product_status`;
8. `history_start_date`;
9. `reviewed_primary_status`;
10. `reviewed_reason`;
11. `forecast_recalculation_required_for_138_day_full_window`; and
12. `owner_review_required`.

The raw accepted file starts with exactly one UTF-8 BOM, uses RFC 4180 CSV
quoting, has one exact header plus 43 records, uses CRLF for all 44 line
terminators, and ends with CRLF. Those transport facts are protected by the raw
file hash; a missing/doubled BOM or newline conversion changes the raw identity.
Parsing decodes once with `utf-8-sig`, performs no whitespace trimming or
Unicode normalization, and requires the 12 headers above in that exact order.

Normalized rows contain all 12 fields, including the inert titles and product
status so descriptive drift is detectable. `shopify_variant_id` must match
`[1-9][0-9]*` and is retained as that canonical decimal string; leading zero,
sign, whitespace, exponent, or float syntax is rejected. The creation timestamp
must match exactly `YYYY-MM-DDTHH:MM:SSZ`, parse as a real UTC second, and be
retained in that canonical form. `history_start_date` must be the exact ISO date
`2026-05-04`; the prior/reviewed statuses and reasons and both decision flags
must equal the registered literals stated above. Descriptive fields must be
nonempty, already trimmed, and byte-exact to the supplied reviewed row, but do
not affect eligibility. Rows are sorted by the integer value of
`shopify_variant_id`. The normalized
row-set hash is SHA-256 of the LF-terminated canonical UTF-8 JSON array defined
in Section 3.1. Its expected value is
`8c1057dc35e234608fedd9c77fba3bd5c44389ddab43753d1ec93453b4f60de8`.
The separately canonicalized sorted exact-ID array has SHA-256
`1d862a029f9474466ea9671f4ff1538cfef4b46ec799e0e6965824575eea8c2c`.
A source-row permutation therefore produces the same normalized row-set hash
but a different raw-file hash; both identities are bound.

The delta must be labeled exactly as reviewed owner-supplied evidence derived
from an exact-ID read-only Shopify review. It must not be described as a
retained raw Shopify connector response: the CSV does not contain raw connector
receipts, query hashes, request/response envelopes, or capture timestamps.

The unchanged CSV bytes must be copied into the private content-addressed source
store with file mode `0600`; private ancestor directories remain `0700`. The
attachment itself is not modified. The normalized delta envelope binds the raw
file SHA-256, exact schema, exact row-set hash, accepted parent V3 input ID/hash,
and the reviewed controls above.

The normalized envelope exact key set is:

```text
contract, data_mode, authority, delta_id, raw_csv_key, raw_csv_sha256,
raw_csv_bytes, raw_transport, schema, normalized_rows,
normalized_row_set_sha256, parent_v3_input, controls,
limitations, zero_authority
```

`raw_transport` freezes BOM/newline/record counts; `schema` is the ordered
12-field array. `raw_transport` has exactly
`encoding,utf8_bom_count,newline,header_row_count,data_row_count,line_terminator_count,terminal_line_terminator,rfc4180_quoting`
with respective values `UTF-8`, `1`, `CRLF`, `1`, `43`, `44`, JSON `true`, and
JSON `true`.

`parent_v3_input` has exactly `contract,input_id,sha256,storage_key` and binds
contract `BUFFALO_PRIVATE_DEVELOPMENT_FORECAST_RESEARCH_INPUT_V3` plus the
accepted V3 input ID, canonical file SHA, and key in Section 2.
`controls` has exactly
`row_count,unique_variant_id_count,variant_id_set_sha256,seed_overlap_count,history_start_date,prior_primary_status,prior_reason,reviewed_primary_status,reviewed_reason,forecast_recalculation_required_for_138_day_full_window,owner_review_required,created_after_history_start_count,shop_timezone`.
Its exact values are `43`, `43`, the reviewed-43 membership SHA in Section 3.1,
`0`, `2026-05-04`, `BLOCKED`,
`EXACT_CURRENT_VARIANT_ABSENT_FROM_REVIEWED_CREATION_EVIDENCE`,
`NOT_APPLICABLE`,
`VARIANT_CREATED_AFTER_HISTORY_START_FULL_WINDOW_NOT_SUPPORTED`, `NO`, `NO`,
`43`, and `America/New_York`, respectively.

`limitations` is the following exact lexicographically sorted string array:

```text
DESCRIPTIVE_FIELDS_ARE_NOT_IDENTITY_OR_ELIGIBILITY_AUTHORITY
NO_OPERATIONAL_OR_PURCHASING_AUTHORITY
NO_PRECREATION_ZEROS_OR_FULL_WINDOW_FORECASTS_FOR_DELTA_ROWS
OWNER_SUPPLIED_REVIEWED_EXACT_ID_EVIDENCE_NOT_RAW_CONNECTOR_RESPONSE
```

The envelope repeats no invented query, connector receipt, or capture
timestamp. Missing/extra nested keys, alternate spellings, or type substitutions
change or invalidate its content address rather than producing an equivalent
envelope.

Only `shopify_variant_id` and `live_exact_id_created_at` participate in the
eligibility decision. Product and Variant titles are inert descriptive fields:
they cannot join, disambiguate, or authorize anything. SKU, title, fuzzy, and
alias inference are prohibited.

Validation fails closed on any missing, extra, duplicate, noncanonical, or
parent-mismatched ID; blank or non-UTC timestamp; timestamp on or before the
history start; changed disposition; schema drift; byte/hash drift; or unexpected
flag. Timestamp comparison converts each UTC instant to the authenticated shop
timezone `America/New_York` and requires its local date to be strictly later
than `2026-05-04`. The exact ID set must also retain zero overlap with the
reviewed seed creation rows on which the accepted absent-evidence disposition
was based. The 43 rows:

- remain outside the eligible forecast population;
- receive no new observations or forecast sidecars;
- receive no pre-creation zeros or fabricated sales;
- create no alias or historical identity attribution; and
- change only their full-window primary disposition from `BLOCKED` to
  `NOT_APPLICABLE` with the reviewed post-start reason.

The eligible population remains 1,365. The complete noneligible population is
644: 643 created after the history start and one conservative first-history-day
case whose retained timestamp lacks timezone.

## 5. Joint-horizon policy identity

The implementation adds one canonical joint-policy JSON document. It binds the
exact registered Development Forecast V2 policy source and canonical hashes and
reuses that policy's model parameters, history length, rate-cap multiplier,
availability semantics, candidate ordering, and empirical quantile. It does not
alter either existing policy file.

The new joint policy freezes:

- method version:
  `DEVELOPMENT_ROLLING_ORIGIN_JOINT_HORIZON_V1`;
- history days: 138;
- history interval: `2026-05-04` through `2026-09-18`, inclusive;
- final forecast origin and target start: `2026-09-19`;
- maximum path days: 17;
- displayed cumulative horizons: `[3, 10, 17]`;
- final target intervals: H3 `2026-09-19..2026-09-21`, H10
  `2026-09-19..2026-09-28`, H17 `2026-09-19..2026-10-05`;
- disjoint selection segments: `[1,3]`, `[4,10]`, and `[11,17]`;
- candidate order: `NAIVE`, `SEASONAL_NAIVE`, `DAMPED_ETS`, `TSB`,
  `CATEGORY_SHRINKAGE`;
- simple baseline: `NAIVE`;
- minimum relative joint FVA improvement: `0.02`;
- selection minimum common origins: 8;
- calibration minimum common origins: 8;
- evaluation minimum common origins: 4;
- protection/target quantile: exact JSON integer numerator `9` and denominator
  `10`, using the nearest-rank ceiling rule;
- output units quantum: `0.0001` with `ROUND_HALF_UP`;
- metric quantum: `0.000001` with `ROUND_HALF_UP`; and
- deterministic candidate-order tie breaking.

Its registered source path is
`procurement/config/development_forecast_joint_horizon_policy_v1.json`. The
policy's exact top-level key set is
`contract,evidence_contract,method_version,parent_policy,history,horizons,candidates,windows,scoring,fva,rate_cap,protection,decimal,output,authority`.
Nested values encode only the constants and formulas specified in this design;
`parent_policy` contains the exact accepted V2 policy contract, method, source
SHA, and canonical SHA. The source hash is over exact repository bytes. The
canonical hash uses the LF-terminated canonical UTF-8 JSON encoding in Section
3.1. Either hash drifting or the registered tuple becoming mixed/partial is a
hard refusal with no V2 fallback.

The policy source bytes and canonical bytes are independently hashed and appear
in every corrected input, sidecar, projection, and replay result. A caller
cannot choose or partially override this policy.

Every joint-policy model recurrence, transformation, mean, cap, cumulative
path, target sample, and diagnostic division executes inside an explicitly
created local Decimal context with precision 50, rounding `ROUND_HALF_EVEN`,
`Emin=-999999`, `Emax=999999`, and traps enabled for invalid operation,
division by zero, and overflow. It never inherits the caller's ambient Decimal
context. Explicit output and metric quantization continues to use
`ROUND_HALF_UP` as stated above.

All hash-bearing internal finite Decimals use one canonical string encoding:
no exponent or leading plus, no leading integer zeros except the single zero,
trailing fractional zeros and a trailing decimal point removed, and every
negative zero normalized to `"0"`. Published unit fields instead use exactly
four fractional digits; published metric fields use exactly six. After
quantization, any signed zero is emitted as positive `"0.0000"` for units or
`"0.000000"` for metrics, including signed bias. NaN and infinities are
forbidden. Replay under any hostile ambient Decimal context must produce the
same winner, paths, evidence bytes, and identities.

## 6. Common rolling-origin populations

All candidate models compete on the same precomputed H17-capable origin set.
Origins are determined before candidate execution using only chronological
partition boundaries and target-window availability evidence; model
applicability cannot remove an origin from one candidate's score.

At each origin, model fitting, transformations, state estimation, and the
origin-specific cap `C_i` use only observations strictly before that origin.
No actual or availability evidence from the origin's 17-day target interval or
from a later partition may affect its path. A perturbation after an origin must
therefore leave that origin's fitted model, cap, and forecast bytes unchanged.

With 138 observations and the accepted V2 partition lengths, the planned common
origin populations are:

- selection: 22 origins;
- calibration: 22 origins;
- evaluation: 18 origins.

The partitions are chronological and nonoverlapping by target interval:
selection target windows end before the calibration partition begins,
calibration target windows end before the evaluation partition begins, and
evaluation target windows end on or before the history endpoint. The final
forecast origin is the day immediately after that endpoint. Partition indices
and all derived origin dates are registered policy values, not caller input.

An origin is usable only when its complete 17-day target interval is available
and is not excluded by the existing proven-stockout censoring rule. The exact
ordered planned, usable, and censored origin dates are frozen in evidence.

If fewer than eight common selection origins remain, the whole joint bundle is
blocked. If fewer than eight common calibration origins remain, coherent target
calculation is blocked. Fewer than four evaluation origins blocks readiness.

A candidate must return a valid 17-day path at every usable common selection
origin. A candidate that is inapplicable or fails at any such origin is
`INELIGIBLE` and receives no score. It is never evaluated on a smaller favorable
subset. The baseline must be eligible or the entire bundle blocks.

Selection is the only stage that may choose the model. Once the winner is
frozen, that same model must return a valid 17-day path at every common
calibration origin, every common evaluation origin, and the final full-history
origin. Any failure blocks the entire joint bundle; the implementation may not
drop an origin, substitute another model, or fall back to an independently
selected horizon. Calibration may estimate only the target/protection evidence
specified in Section 9. Evaluation may report held-out results only: it cannot
change the selected model, cap rule, quantile, target construction, or final
fitting algorithm. Observations in the evaluation partition still become part
of the final origin's 138-day training history; that causal data reuse is not
evaluation-metric feedback.

## 7. Exact joint candidate scoring and FVA

For candidate `m`, common selection origin `i`, and segment `s`, let:

- `F[m,i,s]` be the candidate's forecast total in that disjoint segment;
- `A[i,s]` be the observed total in that disjoint segment;
- `L[s]` be the segment length: 3, 7, or 7 days; and
- `N` be the common usable selection-origin count.

At every origin, `F` comes from the same 17-day nonnegative prefix-capped path
defined in Section 8. Segment totals are differences between its cumulative
boundaries: `point[3]`, `point[10]-point[3]`, and
`point[17]-point[10]`. Candidate scoring therefore cannot use a different
postprocessor from final-path generation.

For each segment:

```text
segment_mae_daily[m,s]
  = (1 / N) * sum_i(abs(F[m,i,s] - A[i,s]) / L[s])

segment_actual_daily[s]
  = (1 / N) * sum_i(abs(A[i,s]) / L[s])
```

The exact segment weights are proportional to distinct forecast days:

```text
weight[A] = 3/17
weight[B] = 7/17
weight[C] = 7/17
```

Aggregate values are:

```text
joint_mae_daily[m]
  = sum_s(weight[s] * segment_mae_daily[m,s])

joint_actual_daily
  = sum_s(weight[s] * segment_actual_daily[s])

joint_score[m]
  = joint_mae_daily[m] / joint_actual_daily,
    when joint_actual_daily > 0
  = joint_mae_daily[m],
    when joint_actual_daily = 0
```

For deterministic selection, the weighted formula is reduced algebraically to
canonical Decimal totals before any division or display quantization:

```text
joint_absolute_error_total[m]
  = sum_s sum_i abs(F[m,i,s] - A[i,s])

joint_actual_total
  = sum_s sum_i abs(A[i,s])
```

Because `weight[s] = L[s]/17`, the normal score is exactly
`joint_absolute_error_total / joint_actual_total`; the all-zero fallback is
exactly `joint_absolute_error_total / (17*N)`. All candidates share the same
denominator in either case. Candidate ordering therefore compares the exact
canonical Decimal `joint_absolute_error_total` values directly; it never
depends on a rounded quotient or an ambient Decimal context. Score quotients
are computed only for evidence/display and quantized once to `0.000001` with
`ROUND_HALF_UP`.

Thus the normal score is WAPE over the three nonoverlapping incremental segment
totals; the all-zero-actual fallback is segment-total daily-normalized MAE. No
early day is counted twice. The three segment totals reconstruct the H3/H10/H17
cumulative boundaries, but boundary-error sums are deliberately *not* the
selection loss. Opposing daily errors inside one segment can cancel. That
limitation is accepted for selection. The diagnostics independently retain:

```text
daily_mae[m]
  = sum_i sum_d abs(forecast_daily[m,i,d] - actual_daily[i,d]) / (17*N)

maximum_prefix_absolute_error_mean[m]
  = sum_i max_d abs(forecast_cumulative[m,i,d] - actual_cumulative[i,d]) / N
```

They also retain every segment total, segment daily-normalized MAE, actual
scale, contribution, and full-precision aggregate. No per-segment
noninferiority gate is introduced by this design; an aggregate gain may coexist
with degradation in one disclosed segment.

The best eligible complex candidate has the lowest exact
`joint_absolute_error_total` among eligible non-`NAIVE` candidates; exact ties
use the frozen candidate order. `NAIVE` remains selected unless that best
complex candidate clears the complete-score gate. The reported formula is:

```text
relative_improvement
  = max((naive_score - complex_score) / naive_score, 0)
```

The gate itself is evaluated without division: if the NAIVE absolute-error
total is zero, relative improvement is zero and `NAIVE` wins; otherwise the
complex candidate passes exactly when
`100 * complex_error_total <= 98 * naive_error_total`. This is equivalent to a
full-precision relative improvement of at least exact Decimal `0.02` and makes
the inclusive threshold unambiguous. The six-decimal relative-improvement and
score values are evidence/display values only; rounding cannot manufacture a
pass at the boundary. A candidate cannot clear FVA through one favorable
segment when its complete joint objective fails the gate.

The FVA result is total and deterministic. If no complex candidate is
eligible, `best_complex_model` and `best_complex_error_total` are JSON `null`,
`relative_improvement` is `"0.000000"`, the gate is false, and reason is
`NO_ELIGIBLE_COMPLEX_CANDIDATE`. If the baseline error is zero, relative
improvement is `"0.000000"`, the gate is false, and reason is
`SIMPLE_BASELINE_SELECTED_ZERO_ERROR`. Otherwise the six-decimal display value
is derived from the full-precision formula above after the exact gate decision;
a failed gate uses
`SIMPLE_BASELINE_SELECTED_COMPLEX_DID_NOT_CLEAR_FVA_GATE`, while a passing gate
uses `JOINT_COMPLEX_MODEL_CLEARED_FVA_GATE`. `selected_model` is `NAIVE` in
every nonpassing case and the named best complex model only in the passing case.

The same common-origin and disjoint-segment formulas produce held-out evaluation
metrics. The evaluation record exposes H3, H10, H17, and maximum-prefix point
error plus target shortfall/service coverage under the already frozen model and
calibration policy. The evaluation *computation and derived metrics* are never
fed back into selection, calibration, confidence-threshold definitions, or the
final fitting algorithm. The underlying evaluation-period observations remain
part of the immutable 138-day history and therefore legitimately participate
when the already selected model is refit at the final origin. Confidence derives from the
registered joint evaluation objective and retains the parent's availability
limitations; UNKNOWN availability cannot be recast as proven in-stock evidence.

More precisely, for each common evaluation origin `i` and prefix day `d`, let
`P[i,d]` be its coherent cumulative point, `T[i,d]` its target constructed from
the frozen calibration sample, and `A[i,d]` actual cumulative normalized demand.
For `h` in `[3,10,17]` the evidence records:

```text
signed_point_error[i,h] = P[i,h] - A[i,h]
absolute_point_error[i,h] = abs(signed_point_error[i,h])
point_bias[h] = sum_i(signed_point_error[i,h]) / N_eval
point_mae[h] = sum_i(absolute_point_error[i,h]) / N_eval
point_wape[h] = sum_i(absolute_point_error[i,h]) / sum_i(abs(A[i,h]))
```

`point_wape[h]` is JSON `null` when its actual denominator is zero; its exact
numerator and denominator are still retained. For every origin:

```text
max_prefix_absolute_point_error[i]
  = max over d=1..17 of abs(P[i,d] - A[i,d])
target_shortfall[i,d] = max(A[i,d] - T[i,d], 0)
target_covered[i,d] = 1 if A[i,d] <= T[i,d] else 0
max_prefix_target_shortfall[i]
  = max over d=1..17 of target_shortfall[i,d]
```

The evidence records the mean of each maximum-prefix measure across origins.
At H3/H10/H17 it records mean target shortfall and service coverage
`sum_i(target_covered[i,h]) / N_eval`. `N_eval` is the one fixed evaluation
inventory and is never prefix-specific. The held-out joint objective reuses the
three disjoint segment totals from Section 7, reporting exact absolute-error and
actual totals, six-decimal joint WAPE when actual total is positive, and JSON
`null` WAPE plus six-decimal daily MAE when actual total is zero. All means and
ratios use the frozen Decimal context and are quantized once to six decimals;
exact numerators and denominators remain in evidence.

One value named `joint_confidence` is copied unchanged to H3, H10, and H17. It
is explicitly confidence in the combined disjoint-segment objective, not in an
individual horizon. Each summary separately exposes that horizon's
`horizon_evaluation_wape`; HTML and CSV label the shared field “Joint
H3/H10/H17 confidence,” never simply “H3 confidence.” With sufficient
evaluation origins and no availability limitation, joint confidence is `HIGH`
when held-out joint WAPE is non-null and its full-precision value is at most
exact Decimal `0.2`, `MEDIUM` when it is non-null and at most exact Decimal
`0.5`, and `LOW` otherwise. Classification occurs before six-decimal display
quantization and uses exact totals without division: `HIGH` exactly when
`5 * absolute_error_total <= actual_total`; otherwise `MEDIUM` exactly when
`2 * absolute_error_total <= actual_total`. A displayed rounded value cannot
upgrade confidence. A null all-zero-actual WAPE is `LOW` with
`JOINT_EVALUATION_ACTUAL_SCALE_ZERO`. Any UNKNOWN-availability limitation
forces `LOW` and retains
`UNKNOWN_AVAILABILITY_NOT_ASSUMED_IN_STOCK` and
`AVAILABILITY_COVERAGE_LIMITS_PROTECTION`. A calculated bundle includes the
exact FVA reason above; negative-day flooring and a final selected-path cap hit add
`NEGATIVE_NET_DAYS_FLOORED_FOR_DEMAND_ONLY` and
`DEVELOPMENT_RATE_CAP_APPLIED` respectively. Insufficient selection,
calibration, or evaluation inventories and baseline/selected-model failure use
the exact blocking codes `JOINT_SELECTION_ORIGINS_INSUFFICIENT`,
`JOINT_CALIBRATION_ORIGINS_INSUFFICIENT`,
`JOINT_EVALUATION_ORIGINS_INSUFFICIENT`, `JOINT_BASELINE_INELIGIBLE`, or
`JOINT_SELECTED_MODEL_PATH_UNAVAILABLE` and produce no calculated sidecar.

## 8. One winner and one nonnegative 17-day point path

After selection, exactly one model generates one 17-day daily path from the
full 138-day history. No horizon-specific model selection or recalibration
occurs afterward.

Raw daily predictions are floored at zero. Let `S[d]` be their cumulative sum
through day `d`, and let `C` be the parent's calendar-mean rate cap derived only
from the observations available at that origin. For the final origin, those are
the complete 138 history days; for a rolling origin they are only the preceding
training observations. The coherent capped cumulative path is:

```text
point[d] = min(S[d], d * C), for d = 1..17
```

A cap hit occurs at day `d` exactly when `S[d] > d*C`; equality is not a hit.
Every selection, calibration, evaluation, and final-origin diagnostic retains
its exact ordered strict-hit day list. The summary reason
`DEVELOPMENT_RATE_CAP_APPLIED` appears only when the selected model's final
full-history path has at least one strict hit. Hits at rolling origins remain
stage diagnostics and do not add that final-summary reason.

Both operands are nonnegative and nondecreasing, so `point[d]` is nonnegative
and nondecreasing. Daily postprocessed units are the nonnegative increments
`point[d] - point[d-1]`, with `point[0] = 0`. The `d*C` ceiling is a cumulative
calendar-mean cap; it does not assert that each individual daily increment is
at most `C`.

Internal calculations retain Decimal precision. Published point and target
values are rounded only at the output boundary to `0.0001` with
`ROUND_HALF_UP`; published protection is then derived by exact subtraction:

```text
published_point[d]      = quantize(point[d], 0.0001, ROUND_HALF_UP)
published_target[d]     = quantize(target[d], 0.0001, ROUND_HALF_UP)
published_protection[d] = published_target[d] - published_point[d]
published_daily[1]      = published_point[1]
published_daily[d]      = published_point[d] - published_point[d-1], d=2..17
```

Protection is not independently rounded. This preserves the published identity
`target = point + protection` exactly. `published_daily` is never produced by
independently quantizing internal daily increments; differencing the published
cumulative path preserves exact summation and deterministic bytes. Displayed
point forecasts are exact prefixes:

```text
H3.point_forecast_units  = published_point[3]
H10.point_forecast_units = published_point[10]
H17.point_forecast_units = published_point[17]
```

The evidence retains the complete 17-day raw, capped daily, and cumulative
paths, their hashes, cap evidence, selected model, candidate records, FVA
decision, and common-origin partitions.

## 9. Coherent cumulative target and protection path

Target construction uses the selected model, the same 17-day path semantics,
and the common calibration origins. It does not clamp or isotonic-repair three
independently calculated horizon totals.

The ordered common calibration-origin inventory is identical for every day of
the target path. An origin is never omitted for one prefix because its result is
unfavorable. For common calibration origin `i` and day `k`, use daily—not
cumulative—demand and the selected model's coherent daily increment:

```text
calibration_daily_point[i,k]
  = calibration_point[i,k] - calibration_point[i,k-1]
actual_daily[i,k]
  = actual[i,k] - actual[i,k-1]
daily_shortfall[i,k]
  = max(actual_daily[i,k] - calibration_daily_point[i,k], 0)
```

Both day-zero cumulative values are zero. Each calibration origin defines one
cumulative shortfall path and one demand-plus-protection scenario. Evaluation
order is normative under the frozen local Decimal context:

```text
cumulative_daily_shortfall[i,0] = 0
cumulative_daily_shortfall[i,d]
  = cumulative_daily_shortfall[i,d-1] + daily_shortfall[i,d]
    evaluated left-to-right for d=1..17

target_sample[i,d] = point[d] + cumulative_daily_shortfall[i,d]
```

The final target path is the empirical 0.90 nearest-rank quantile across the
same common calibration origins at every prefix:

```text
target[d] = q90_i(target_sample[i,d])
protection[d] = target[d] - point[d]
```

For `N` common calibration origins, nearest rank is `ceil(0.90*N)` in the
ascending ordered sample, using one-based rank. The planned 22-origin inventory
therefore uses rank 20. The complete per-day sample count and rank are frozen in
evidence.

Both `point[d]` and each cumulative-shortfall path are nonnegative and
nondecreasing, so `target_sample[i,d]` is nondecreasing by construction. The
fixed-sample nearest-rank quantile therefore produces a nondecreasing target
path and `target[d] >= point[d]`; protection is nonnegative. No monotonicity is
asserted for either the internal or published protection path. The separately
rounded published point and target paths can make their exact published
difference decline by one output quantum, and neither representation is
postprocessed to force a direction.

Held-out target evaluation uses the frozen calibration daily-shortfall paths
without refitting them. For each evaluation origin, its coherent point path replaces
the final point path in `target_sample`, the same frozen calibration-origin
inventory and nearest-rank rule produce its target path, and that path is
compared with the evaluation origin's actual cumulative demand. Evaluation
cannot add residuals to the calibration sample or change the final policy.

Displayed targets and protection are exact prefixes of the published paths:

```text
Hh.target_units     = published_target[h]
Hh.protection_units = published_target[h] - published_point[h]
```

This cumulative daily-scenario construction is the primary target design. It
never applies `max(previous_target,current_target)`, a running maximum,
isotonic post-correction, arbitrary clipping, or replacement with an earlier
horizon value. Monotonic comparisons are assertions only; a violation is a
defect and fails closed.

All-zero history produces an all-zero point, target, and protection path.
Negative net-sales days retain the existing demand-only normalization and
reason evidence. No availability, lost-sales, incoming, or order fact is
invented.

## 10. Raw diagnostic retention and corrected output binding

The accepted independent H3/H10/H17 V2/V3 sidecars remain immutable diagnostics
and retain their original selected models, point forecasts, protection, targets,
hashes, and known violations. They are never rewritten to look coherent.

There is exactly one corrected joint sidecar for each of the 1,365 eligible
Variant IDs and none for the 644 not-applicable IDs. Its projection-map key is:

```text
joint17:<canonical_variant_id>:<corrected_input_id>:<joint_policy_canonical_sha256>
```

Keys sort lexicographically in the sidecar map; the Variant ID is also stored
inside the sidecar and must agree with the key. Missing, extra, duplicate,
mis-keyed, or cross-input sidecars fail closed. H3, H10, and H17 summaries for
one Variant all reference that one key and its one sidecar SHA; no horizon gets
a separately selected or calibrated sidecar.

Each sidecar has exactly these top-level keys and types:

| Key | Type / exact contents |
|---|---|
| `contract` | exact joint-evidence contract string |
| `authority` | exact `ZERO_AUTHORITY_RESEARCH_ONLY` literal |
| `research_only` | exact JSON `true` |
| `commercial_authority` | exact JSON `false` |
| `production_activation` | exact JSON `false` |
| `variant_id` | canonical decimal string |
| `corrected_input_id` | 64-lowercase-hex string |
| `parent_projection` | exact logical/artifact/workspace descriptor |
| `creation_evidence_delta` | exact delta ID, raw SHA, and row-set SHA descriptor |
| `joint_policy` | exact contract, method, source SHA, and canonical SHA descriptor |
| `history` | exact keys `start_date,end_date,day_count,observations_sha256,availability_basis` |
| `origin_partitions` | exact `selection,calibration,evaluation` mappings |
| `candidate_records` | array in registered candidate order |
| `selected_model` | registered model name |
| `fva` | exact baseline/error totals/2%-gate result and reason |
| `final_path` | exact cap and 17-value raw/internal/published path arrays |
| `calibration` | fixed-origin shortfall, nested target-sample, rank, and path evidence |
| `evaluation` | exact point/target metrics, joint confidence, and reason codes |
| `summaries` | exact `H3,H10,H17` mappings |
| `limitations` | sorted unique string array |
| `zero_authority` | exact ten-key integer-zero effects map in Section 3.2 |
| `sidecar_sha256` | logical hash defined in Section 3.1 |

The sidecar `parent_projection`, `creation_evidence_delta`, and `joint_policy`
descriptors use the identical exact key sets defined for the corrected input;
no abbreviated or extended descriptor is accepted.

Each origin-partition mapping has exactly
`planned_dates,usable_dates,censored_dates,minimum_count`; date arrays are
strictly ascending, unique ISO dates and reconcile to the planned inventory.
Each candidate record has exactly
`model,status,reason_codes,selection_origin_dates,origin_paths,segments,daily_mae,maximum_prefix_absolute_error_mean,joint_absolute_error_total,joint_actual_total,joint_score,selected`.
For an eligible record, `origin_paths` follows the exact selection-origin order
and each item has exactly
`origin_date,calendar_mean_cap,cap_hit_days,forecast_daily,forecast_cumulative,actual_daily,actual_cumulative`;
all four arrays have length 17. `daily_mae` is the mean of all 17*N absolute
daily errors. `maximum_prefix_absolute_error_mean` is the mean across origins
of each origin's maximum absolute cumulative-prefix error. Eligible `segments`
has exact `A,B,C` keys; each segment has exact
`start_day,end_day,length,forecast_totals,actual_totals,absolute_errors,mae_daily,actual_daily,contribution`.
For segment `s`, `contribution = weight[s] * mae_daily[s]` under the frozen
Decimal context. An ineligible record sets `selection_origin_dates`,
`origin_paths`, `segments`, and all metric fields to JSON `null`, states one
exact ineligibility code, and cannot be selected.

Candidate `status` is exactly `ELIGIBLE` or `INELIGIBLE`. An eligible candidate
has `reason_codes:[]`; an ineligible candidate has
`reason_codes:["JOINT_CANDIDATE_INELIGIBLE_AT_COMMON_SELECTION_ORIGIN"]` and
`selected:false`. A registered applicability precondition failure, a typed
forecast-calculation refusal at any common selection origin, a wrong-length
path, or a nonfinite path all map to that one output code. An unexpected
exception aborts the entire build and emits no candidate record. No raw
exception text, origin deletion, model-specific spelling, or fallback code may
enter hash-bearing evidence. An eligible candidate's `selected` value is true
only for the one FVA-selected model and false otherwise.

`fva` contains exactly
`baseline_model,best_complex_model,selected_model,baseline_error_total,best_complex_error_total,relative_improvement,threshold_numerator,threshold_denominator,complex_cleared_gate,reason`.
The threshold fields are exact JSON integers `2` and `100`; the Boolean gate and
reason must replay from the exact error-total inequality and null/zero cases in
Section 7. `relative_improvement` is always a nonnegative six-decimal string.

`final_path` has exactly
`forecast_origin,target_start,target_end,calendar_mean_cap,cap_hit_days,raw_daily,internal_capped_daily,internal_cumulative,published_cumulative,published_daily,path_sha256`.
All path arrays have length 17; internal arrays use canonical Decimal strings,
published arrays use four-decimal strings, and the path hash uses the logical
sidecar canonicalization over the path mapping without `path_sha256`.
`calibration` has exactly
`origin_dates,minimum_count,quantile_numerator,quantile_denominator,nearest_rank,cap_hit_days_by_origin,daily_shortfall_paths,cumulative_daily_shortfall_paths,target_sample_paths,internal_target_path,published_target_path,published_protection_path,calibration_sha256`.
Its origin-indexed arrays follow the exact ascending origin order and every
inner path has length 17. `quantile_numerator` and `quantile_denominator` are
exact JSON integers `9` and `10`. `evaluation` has exactly
`origin_dates,minimum_count,cap_hit_days_by_origin,point_metrics,target_metrics,joint_objective,joint_confidence,reason_codes,evaluation_sha256`
and uses the formulas in Section 7.
`point_metrics` contains exactly `H3,H10,H17,daily_mae,maximum_prefix_absolute_error_mean`;
each horizon mapping contains exactly
`signed_error_sum,absolute_error_sum,actual_absolute_sum,bias,mae,wape`.
`target_metrics` contains exactly
`H3,H10,H17,maximum_prefix_shortfall_mean`; each horizon mapping contains
exactly
`shortfall_sum,mean_shortfall,covered_count,total_count,service_coverage`.
`joint_objective` contains exactly
`absolute_error_total,actual_total,wape,daily_mae`. Exact totals/counts use
canonical internal Decimal strings or JSON integers; derived metrics are a
six-decimal string or the specifically allowed JSON `null` WAPE.
Each nested `*_sha256` is the logical sidecar canonical hash of that mapping
with only its own hash field omitted.

Each `summaries[Hh]` mapping inside the sidecar has exactly
`horizon_days,target_start,target_end,status,primary_status,selected_model,point_forecast_units,protection_units,target_units,joint_confidence,horizon_evaluation_wape,reason_codes,joint_sidecar_key`.
For calculated sidecars, `status` is exactly `CALCULATED_RESEARCH_ONLY` and
`primary_status` is exactly `CALCULATED`. The corresponding calculated
projection owner summary has the same fields plus the completed
`joint_sidecar_sha256`; the in-sidecar summary omits that one field to avoid a
circular hash. Values must be rederived from the corresponding published path
prefix and the one sidecar identity; summary-only mutation cannot substitute
for a valid sidecar.

The corrected projection is a compact wrapper, not a rewritten copy of the
230-MB accepted projection. Its exact top-level keys are:

```text
contract, data_mode, status, authority, research_only, commercial_authority,
production_activation, zero_authority,
implementation_lineage, corrected_input, parent_projection, declared_coverage, coverage_summary,
coverage_rows, owner_worksheet, joint_forecast_research,
forecast_sidecars, grouped_decision_queue, limitations, projection_sha256
```

`status` is exactly `RESEARCH_ONLY`. `implementation_lineage` is the exact
eight-key mapping in Section 2. `declared_coverage` is an exact canonical deep
copy of the accepted, fully validated parent projection's `declared_coverage`;
no count, label, or field is recomputed or omitted.
`corrected_input` contains exactly `contract,input_id,sha256,storage_key`.
`joint_forecast_research` contains exactly
`input_id,policy,parent_projection,creation_evidence_delta,source_identity,history_controls,membership_controls,scenario_dates,sidecars_sha256,primary_status_counts,violation_counts`.
Its policy and parent/delta descriptors reuse the exact schemas above.
`source_identity` is an exact canonical deep copy of the accepted validated
parent source-identity mapping and must replay to source-identity ID
`bd8d6dfc014b3899366ac1378aeb92b21ac17164b84132a367e7f61a34db39d1`,
registered file SHA
`35055e3453367f44781f3929f9532ed3b6fb27e87ac8041c9a78775de079a245`,
and evidence SHA
`8ad9476283fad379910c4c03f68d5c63179e50128440671410d45cf115de618a`.

`history_controls` has exactly these keys:

```text
parent_history_contract, parent_composite_id, start_date, end_date,
complete_day_count, parent_allocation_controls, parent_eligibility_controls,
parent_allocation_ledger_sha256, parent_eligibility_ledger_sha256,
parent_variant_observations_sha256, parent_recent_observed_sales_sha256,
corrected_disposition_ledger_sha256
```

The parent mappings are exact canonical deep copies. A digest already exposed
by the validated parent must equal that registered parent digest; a wrapper-only
digest is SHA-256 under the logical projection/sidecar canonicalization in
Section 3.1 (recursive sort, ASCII, no trailing LF). Together they preserve
the 138 ordered dates, signed raw/allocated totals, quarantine controls,
allocation dispositions, eligibility decisions, observation series, and recent
recorded-sales evidence without copying those large values into the wrapper.

`corrected_disposition_ledger_sha256` hashes one exact 2,009-row array. Rows are
sorted by integer `shopify_variant_id` and each has exactly:

```text
shopify_variant_id, parent_primary_status, parent_full_window_reason,
corrected_primary_status, corrected_full_window_reason, existence_basis,
disposition_source
```

For the 1,365 eligible IDs, both statuses are `CALCULATED`, both reasons are
JSON `null`, existence basis is the exact validated parent basis, and source is
`ACCEPTED_V3`. For the parent 601 N/A IDs, both statuses are
`NOT_APPLICABLE`, both reasons and existence basis are exact parent values, and
source is `ACCEPTED_V3`. For the reviewed 43, the parent status/reason are
`BLOCKED` and
`EXACT_CURRENT_VARIANT_ABSENT_FROM_REVIEWED_CREATION_EVIDENCE`; the corrected
status/reason are `NOT_APPLICABLE` and
`VARIANT_CREATED_AFTER_HISTORY_START_FULL_WINDOW_NOT_SUPPORTED`; existence
basis is the reviewed literal below; and source is
`REVIEWED_43_ROW_CREATION_DELTA`. No other row or value is permitted. The hash
uses the logical projection/sidecar canonicalization in Section 3.1. Replay
rederives this array from the validated parent plus normalized delta before
accepting its digest.

`membership_controls` has exactly the keys
`current_catalog,eligible,parent_not_applicable,reviewed_prior_blocked,corrected_not_applicable,corrected_blocked`.
Each value has exactly `count,sha256` and equals the corresponding registered
membership in Section 3.1. `scenario_dates` has exactly
`history_start,history_end,forecast_origin,H3,H10,H17`; each horizon mapping has
exactly `horizon_days,target_start,target_end` and equals the inclusive dates in
Section 5. `primary_status_counts` has exact
`H3,H10,H17` keys and, under each, exactly
`CALCULATED,NOT_APPLICABLE,BLOCKED,NOT_PROCESSED,numerical_zero` integer fields.
`violation_counts` contains exact `raw_point,raw_target,raw_unique_variants,corrected_point,corrected_target,corrected_unique_variants` integers.
`coverage_summary` contains exactly
`variant_count,sidecar_count,calculated_variant_count,not_applicable_variant_count,blocked_variant_count,not_processed_variant_count,raw_point_violation_count,raw_target_violation_count,corrected_point_violation_count,corrected_target_violation_count`.

It has exactly 2,009 unique canonical IDs in both `coverage_rows` and
`owner_worksheet`, in current-catalog order, and exactly 1,365 entries in
`forecast_sidecars`. `joint_forecast_research` binds policy, parent/delta/source
identities, history and allocation controls, per-horizon primary counts,
violation counts, and the logical SHA of the complete sidecar map.
Owner-facing summaries use only corrected joint evidence. Supplier hypotheses
and descriptive fields are copied only from the already validated parent owner
row and retain their unapproved/research-only labels.

Each corrected `coverage_rows` item starts as the exact validated parent row.
Only these fields may differ: `primary_forecast_status`,
`forecast_sidecar_keys`, and `missing_data_reasons`. Eligible rows keep
`CALCULATED`, replace the three raw sidecar keys with their one joint key, and
replace only forecast-specific reason codes with the rederived joint reasons.
The parent 601 N/A rows remain byte-semantically unchanged. The reviewed 43
replace `BLOCKED` with `NOT_APPLICABLE`, replace the absent-evidence reason with
the reviewed post-start reason, and retain an empty sidecar list. Every other
field, including historical sales and captured-stock provenance, must be equal
to the validated parent value.

Each corrected owner row starts as the exact validated parent owner row. Only
`existence_basis,scenario_results,sidecar_keys,reason_codes,next_missing_stage,stage_status`
may differ. Eligible rows receive the joint summaries/key and retain ABC as the
next downstream gate. The parent 601 N/A rows remain unchanged. The reviewed
43 receive exact existence basis
`REVIEWED_OWNER_SUPPLIED_EXACT_ID_SHOPIFY_CREATION_TIMESTAMP_AFTER_HISTORY_START`,
top-level `reason_codes` equal to the one-item reviewed-reason array, and
`sidecar_keys:[]`. Their `scenario_results` has exactly `H3,H10,H17`. Each
horizon mapping has exactly
`horizon_days,target_start,target_end,status,primary_status,selected_model,point_forecast_units,protection_units,target_units,joint_confidence,horizon_evaluation_wape,reason_codes,joint_sidecar_key,joint_sidecar_sha256`.
The horizon days and inclusive dates are the registered values in Section 5;
`status` is `REAL_NUMERICAL_EVALUATION_NOT_RUN`, `primary_status` is
`NOT_APPLICABLE`, the six model/numeric/confidence/evaluation fields are JSON
`null`, `reason_codes` is exactly
`["VARIANT_CREATED_AFTER_HISTORY_START_FULL_WINDOW_NOT_SUPPORTED"]`, and both
sidecar fields are JSON `null`. For those 43 only,
`stage_status.CAPTURE` is exactly
`SUPPORTED_REVIEWED_EXACT_ID_CREATION_EVIDENCE`,
`stage_status.FORECAST` is exactly
`NOT_APPLICABLE:FULL_138_DAY_WINDOW`, and `next_missing_stage` is exactly
`{"stage":"FORECAST","reason":"VARIANT_CREATED_AFTER_HISTORY_START_FULL_WINDOW_NOT_SUPPORTED"}`.
Their `IDENTITY` and five downstream stage-status values remain exact parent
copies. This FORECAST marker records temporal inapplicability, not a missing
source or owner request, and therefore produces no grouped decision-queue item.
The parent 601 N/A rows remain exact parent copies, including their legacy stage
diagnostics; those diagnostics likewise do not create a corrected retrievable-
source queue item. All supplier hypotheses, titles, inventory provenance,
recent recorded sales, owner-response fields, and downstream blocker references
remain parent-equal.

The grouped queue has exact contract
`BUFFALO_PRIVATE_RESEARCH_DECISION_QUEUE_V1`, deduplication basis
`SHARED_STAGE_OR_EXACT_ELIGIBILITY_REASON`, and four categories in this order:
`MACHINE_FIXABLE_DEFECTS` (zero items), `RETRIEVABLE_MISSING_SOURCES`
(the shared ABC and NET_NEED items only),
`GENUINELY_OWNER_SPECIFIC_UNANSWERED_FACTS` (shared CASE_QUANTITY and ECONOMICS
items), and `FUTURE_RELEASE_AUTHORITY` (the shared ORDER item). Each category
has exactly `category,item_count,items`; each item has exactly
`stage,reason,affected_variant_count,scope`. The resolved 43 create no owner
question or retrievable-source item.

The five exact nonempty queue items are:

| Stage | Reason | Count | Scope |
|---|---|---:|---|
| `ABC` | `EXACT_COHORT_AND_COST_AUTHORITY_REMAINS_INCOMPLETE` | `null` | `SHARED_GATE_AFTER_SUPPORTED_FORECAST` |
| `NET_NEED` | `TRUSTED_INCOMING_OPEN_ORDERS_AND_OPERATIONAL_POLICY_NOT_SUPPLIED` | `null` | `SHARED_GATE_AFTER_SUPPORTED_FORECAST` |
| `CASE_QUANTITY` | `APPROVED_PACK_NOT_SUPPLIED` | `null` | `SHARED_GATE_REQUIRING_APPLICABLE_APPROVED_EVIDENCE` |
| `ECONOMICS` | `APPROVED_SELECTED_PRICE_MARGIN_AND_FEES_NOT_SUPPLIED` | `null` | `SHARED_GATE_REQUIRING_APPLICABLE_APPROVED_EVIDENCE` |
| `ORDER` | `PRODUCTION_AND_ORDER_AUTHORITY_NOT_GRANTED` | `null` | `FUTURE_RELEASE_AUTHORITY_ONLY` |

The corrected coverage export has exactly
`contract,authority,operational_authority,projection_sha256,coverage`.
Its contract is the corrected coverage contract, authority is
`ZERO_AUTHORITY_RESEARCH_ONLY`, operational authority is JSON `false`, and
`coverage` is the exact 2,009-item corrected coverage array. HTML and CSV are
pure deterministic renderings of the validated corrected projection; they do
not load mutable external business data or independently recalculate forecasts.

Before validating or rendering the corrected wrapper, the reader independently
loads the accepted `projection.json` by exact workspace path and artifact hash,
runs the unchanged legacy V3 validator, rederives every raw H3/H10/H17 summary
from its validated legacy sidecars, and proves those derived summaries equal the
accepted raw owner summaries. It never trusts a duplicate raw summary in the
corrected wrapper. The accepted raw sidecars remain accessible only through
that immutable parent workspace and are not rewritten or relabeled.

## 11. Validation and fail-closed invariants

Planner, corrected-input, corrected-projection, workspace, and browser preflight
boundaries independently enforce for every `CALCULATED` Variant:

```text
0 <= H3.point_forecast_units
  <= H10.point_forecast_units
  <= H17.point_forecast_units

0 <= H3.target_units
  <= H10.target_units
  <= H17.target_units

target_units = point_forecast_units + protection_units
protection_units >= 0
```

They also rederive candidate eligibility, common-origin sets, segment metrics,
joint scores, FVA selection, final paths, calibration shortfalls, quantiles,
summary fields, sidecar hashes, count controls, and artifact identities. A
recomputed outer hash never substitutes for semantic replay.

Expected primary counts for each of H3, H10, and H17 are exactly:

- `CALCULATED = 1,365`;
- `NOT_APPLICABLE = 644`;
- `BLOCKED = 0`;
- `NOT_PROCESSED = 0`.

`numerical_zero` remains a derived subset of `CALCULATED`, never a separate
denominator bucket. Its final counts must be measured from corrected results,
not copied from the accepted independent-horizon output.

The corrected build additionally reports before/after point and target
violation counts. The acceptance target is zero for both corrected ladders;
the immutable raw diagnostics continue to record 38 point violations, 136
target violations, and 151 unique affected Variants.

## 12. Deterministic replay, outputs, and privacy

Identical source bytes, parent identities, policy bytes, and code must produce
byte-identical corrected input, projection, sidecars, coverage, HTML, CSV,
manifest, and workspace identity across independent builds and restart.

Corrected outputs use a new content-addressed workspace and the existing four
artifact names:

- `coverage.json`;
- `owner-preview.html`;
- `owner-worksheet.csv`;
- `projection.json`.

They never overwrite the accepted workspace. Private directories are `0700`
and files are `0600`; traversal, symlinks, ownership/mode drift, unexpected
inventory, and byte/hash drift fail closed. Private rows and artifact bodies
remain outside Git and ordinary logs.

The compact owner output remains one row per current Variant. It distinguishes
recorded sales, research point forecast, empirical protection, research target,
and downstream blocked stages. It never labels target units as a buy quantity.

### 12.1 Corrected-only dispatch

The new exact contract/data-mode tuple must be added explicitly to every
input/projection/workspace reader, coverage/HTML/CSV renderer, app cache and
filter path, artifact route, structural preflight, launcher, and browser audit
allowlist. Dispatch is exact-contract-first; the corrected tuple cannot enter a
V3/V2 branch, and unknown, mixed, or partial tuples fail without fallback.
Warm viewer filtering may consume only the identity-guarded, startup-validated
corrected cache capability; public filtering continues to validate its input.

Legacy dispatch remains byte- and behavior-identical. Compatibility evidence
must include full semantic readback and startup of the accepted V3 workspace,
not only hash comparison, plus the existing V1/V2/V3 golden export tests. The
corrected browser contract still exposes exactly the four registered artifacts
and must prove initial and restart source identity, authenticated loopback-only
network confinement, endpoint/artifact equality, and owned-resource cleanup.

## 13. Required deterministic tests

Implementation authorization must require tests covering at least:

1. exact 43-row count, uniqueness, accepted-set equality, timestamp and
   disposition controls;
2. duplicate, missing, extra, unknown, seed-conflicting, non-UTC, pre-start,
   malformed, schema-drifted, nested-key/type/literal-drifted, and hash-drifted
   delta refusal;
3. raw BOM/CRLF identity, source-row permutation, equivalent-but-noncanonical
   UTC syntax, leading-zero ID, normalized sorting, and exact row-set-hash
   behavior;
4. 43 post-start Variants remain `NOT_APPLICABLE`, with no observations,
   sidecars, aliases, or pre-creation zeros, and their exact CAPTURE, FORECAST,
   H3/H10/H17 null-result summaries, next-stage, and no-owner-queue semantics
   replay;
5. exact ID-set equality for 2,009 coverage/worksheet rows, unchanged 1,365
   calculated/sidecar IDs, parent-601 plus delta-43 corrected N/A IDs, empty
   blocked IDs, and identical H3/H10/H17 memberships;
6. accepted parent replay preserves the exact 138 ordered dates, signed
   raw/source and allocated control totals, disposition counts, quarantined-row
   controls, source identity, alias/identity evidence, and allocation hashes;
7. all candidates use identical common origins and candidates cannot benefit
   from reduced origin sets;
8. minimum-origin refusal for selection, calibration, and evaluation;
9. different models winning the old independent horizons;
10. raw H10 below H3 and raw H17 below H10;
11. zero and intermittent demand;
12. actual TSB, seasonal-naive, damped-ETS, and naive winners;
13. joint NAIVE fallback when complexity misses the 2% complete-score gate;
14. full-precision relative improvements immediately below, exactly at, and
    immediately above `0.02`, proving display rounding cannot change selection;
15. exact candidate-score equality and candidate-order tie breaking;
    eligible/ineligible status, reason-array, typed-refusal, malformed-path, and
    unexpected-exception mappings must also match the registered vocabulary;
16. one hard-coded irregular 17-day raw path where `min(S[d],d*C)` differs from
    the legacy whole-path scaler, asserting exact cap, daily increments, and
    cumulative bytes at selection, calibration, evaluation, and final origins,
    plus strict-greater-than versus equality cap-hit semantics and final-only
    summary reason emission;
17. each displayed H3/H10/H17 point, target, and protection value exactly
    equals the corresponding published prefix of its one coherent path;
18. an adversarial rounding case that preserves published
    `target = point + protection`, distinguishes published-cumulative
    differencing from independent daily rounding, permits a published protection
    decline, and uses positive fixed-width signed-zero output; plus cumulative
    daily-shortfall target fixtures proving monotonicity without a running
    maximum or post-hoc repair;
19. no future leakage and causal response to a supported history change,
    including proof that a later perturbation cannot change an earlier origin's
    fit, cap, or path;
20. selected-model failure at a calibration, evaluation, or final origin blocks
    the joint bundle without origin deletion, model switching, or fallback;
21. nearest-rank `q90` uses rank 20 for 22 samples and one common sample
    inventory at every prefix; changing only derived evaluation metrics cannot
    change model/path bytes, while changing an underlying historical observation
    is permitted to affect the later final-origin refit but never an earlier
    origin;
22. hard-coded held-out evaluation known answers for H3/H10/H17 error,
    max-prefix error, target shortfall, service coverage, and all-zero actuals,
    including a high joint-confidence/poor-H3 case that verifies explicit joint
    labeling and the separate horizon WAPE, plus full-precision values
    immediately below, exactly at, and immediately above the `0.2` and `0.5`
    confidence thresholds proving display rounding cannot upgrade a class;
23. hostile ambient Decimal contexts produce byte-identical winner, paths,
    evidence, hashes, and replay outputs;
24. missing, extra, duplicate, mis-keyed, wrong-cardinality, or self-hash-drifted
    corrected joint sidecars refuse;
25. tampered origin, candidate, score, selected-model, path, quantile, summary,
    sidecar, count, parent identity, implementation-lineage, source-identity,
    declared-coverage, history-control, membership-control, or scenario-date
    refusal, including independent recomputation of the corrected disposition
    ledger digest;
26. legacy V1/V2/V3 semantic and byte compatibility, including unchanged
    accepted workspace hashes;
27. accepted V3 full semantic readback and startup plus two independent corrected
    builds and restart producing byte-identical outputs;
28. one owner row per current Variant, safe HTML/CSV rendering, and exact
    corrected coverage counts; and
29. a known-answer corrected workspace-ID preimage, including proof that any of
    the four artifact hashes changes the workspace ID;
30. missing/extra/true/nonzero/Boolean-versus-integer authority tampering at
    delta, input, sidecar, projection, policy, and manifest boundaries;
31. parent-copy-field, corrected coverage/owner schema, grouped-queue, coverage
    export, FVA null/zero/reason/relative-improvement, and evaluation-schema
    drift refusal;
32. unknown, mixed, or partial corrected tuples refuse independently at planner,
    input, projection, workspace, export, app, launcher, and browser boundaries,
    with no legacy fallback; and
33. explicit absence of operational database, Shopify write, supplier approval,
    DRAFT, PO, order, and production paths.

All committed fixtures are synthetic and fabricated; no real Variant IDs,
titles, rows, histories, or private artifact bodies enter Git or ordinary logs.

Focused forecast, corrected-V3, legacy V2/V3, projection, export, workspace,
app, launcher/tool, and browser-harness tests precede the registered authoritative
suite. Every new named test method raises its exact module floor and the global
sum-derived floor; the current predecessor floor is 947 and must not be forced
after new discovery. The independent exact-population assertions in
`test_persistent_mapping_foundation_contract.py` and
`test_supplier_format_conformance.py` must be updated to the same newly
discovered total rather than left at 947.

After implementation and independent review, final evidence requires:

- the authoritative full suite with every discovered test executed and every
  abnormal counter zero;
- startup validation;
- static, secret, private-data, permission, and cleanup checks;
- full semantic readback;
- deterministic replay; and
- fresh authenticated loopback Chromium initial and restart acceptance against
  the corrected workspace, including exact four-artifact equality, confined
  network activity, source identity, and complete cleanup.

Prior browser or suite evidence cannot be relabeled as corrected execution.

## 14. Documentation, review, and stop boundary

The implementation candidate must prepend an exact entry to
`docs/CODEX_HANDOFF.md` with commit/tree, changed files, policy and contract
identities, source and artifact hashes, durations, coverage, violation counts,
control totals, deterministic replay, suite/startup/browser results, cleanup,
limitations, and the next authority boundary.

`procurement/docs/PHASE_STATUS.md` remains unchanged unless the canonical
program milestone actually changes. This research remediation does not itself
change that milestone.

The design-only commit receives lightweight deterministic/static validation.
Claude Code is the required independent adversarial design reviewer. Cursor may
perform a targeted read-only forecasting/statistics review when available.
ChatGPT performs final business-rule review. Codex remains the sole writer; any
material finding returns to Codex for remediation.

This design stops before implementation. A separate explicit authorization is
required to write runtime code, ingest the private delta into the content-addressed
store, generate corrected forecasts or artifacts, run private browser acceptance,
or advance any downstream procurement stage.
