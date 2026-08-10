"""Phase 3 identity investigation — diagnostic layer only.

Classifies each MISSING historical Variant ID by direct Shopify node lookup and
deterministically compares truly-deleted IDs against the NEW live variants.
Never approves, retires, rejects, or writes anything to Shopify — Shopify access
is strictly read-only (`node` lookups only). All results are evidence persisted
for human review; no readiness gate or catalog state is modified here.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from procurement_os.shopify.queries import VARIANT_NODE_LOOKUP_QUERY

# --- Step 1: direct existence classification -------------------------------

MISSING_EXISTENCE = (
    "STILL_EXISTS_ACTIVE",
    "STILL_EXISTS_DRAFT",
    "STILL_EXISTS_ARCHIVED",
    "STILL_EXISTS_OTHER_NONACTIVE",
    "DELETED_OR_NO_LONGER_RESOLVABLE",
)


def variant_gid(variant_id: str) -> str:
    v = str(variant_id)
    return v if v.startswith("gid://") else f"gid://shopify/ProductVariant/{v}"


def lookup_variant_node(client: Any, variant_id: str) -> dict | None:
    """Exact node lookup by GID. Returns the ProductVariant dict or None."""
    data = client.query(VARIANT_NODE_LOOKUP_QUERY, {"id": variant_gid(variant_id)})
    node = data.get("node")
    if not node or node.get("__typename") != "ProductVariant":
        return None
    return node


def classify_existence(node: dict | None) -> str:
    if node is None:
        return "DELETED_OR_NO_LONGER_RESOLVABLE"
    status = ((node.get("product") or {}).get("status") or "").upper()
    if status == "ACTIVE":
        return "STILL_EXISTS_ACTIVE"
    if status == "DRAFT":
        return "STILL_EXISTS_DRAFT"
    if status == "ARCHIVED":
        return "STILL_EXISTS_ARCHIVED"
    return "STILL_EXISTS_OTHER_NONACTIVE"


# --- Step 2: deterministic comparison of deleted IDs vs NEW live variants ---

_SIZE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(ML|L|OZ|LITER|LTR)\b|(\d+)\s*PK|(\d+)\s*PACK", re.IGNORECASE)


def normalize_size(text: str | None) -> str | None:
    """Extract a canonical size token (e.g. '750ML', '1.75L', '12PK') from a title.
    Sizes are identity-critical: 1.5L vs 1.75L must never be conflated."""
    if not text:
        return None
    tokens = []
    for m in _SIZE_RE.finditer(text):
        if m.group(1):
            num = m.group(1)
            unit = m.group(2).upper().replace("LITER", "L").replace("LTR", "L")
            num = num[:-2] if num.endswith(".0") else num
            tokens.append(f"{num}{unit}")
        elif m.group(3):
            tokens.append(f"{m.group(3)}PK")
        elif m.group(4):
            tokens.append(f"{m.group(4)}PACK".replace("PACK", "PK"))
    return "+".join(tokens) or None


_PACKISH_RE = re.compile(r"\b(GIFT|COMBO|SET|SAMPLER|VARIETY|W/|WITH\s+GLASS)\b", re.IGNORECASE)


def is_packish(title: str | None) -> bool:
    return bool(title and _PACKISH_RE.search(title))


def normalize_title(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def compare_pair(seed: dict, new: dict, dup_skus: set[str], dup_barcodes: set[str]) -> dict:
    """Deterministic side-by-side evidence for one (deleted seed, new live) pair.
    Returns supporting/conflicting field lists — never a merge decision."""
    supporting, conflicting, cautions = [], [], []

    def field(name, a, b, *, unique_evidence=True, dup_group: set[str] | None = None):
        a_n = (a or "").strip()
        b_n = (b or "").strip()
        if not a_n or not b_n:
            return
        if a_n == b_n:
            if dup_group is not None and a_n in dup_group:
                cautions.append(f"{name} matches but value '{a_n}' is duplicated in the live catalog — not unique identity evidence")
            elif unique_evidence:
                supporting.append(f"{name} exact match: {a_n}")
            else:
                supporting.append(f"{name} match (weak): {a_n}")
        else:
            conflicting.append(f"{name} differs: seed '{a_n}' vs new '{b_n}'")

    field("sku", seed.get("sku"), new.get("sku"), dup_group=dup_skus)
    field("barcode", seed.get("barcode"), new.get("barcode"), dup_group=dup_barcodes)
    field("vendor", seed.get("vendor"), new.get("vendor"), unique_evidence=False)
    field("product_type", seed.get("product_type"), new.get("product_type"), unique_evidence=False)

    # Titles: normalized comparison is context only, never identity by itself.
    st, nt = normalize_title(seed.get("product_title")), normalize_title(new.get("product_title"))
    if st and nt:
        if st == nt:
            supporting.append(f"normalized product title match (context only): '{st}'")
        else:
            conflicting.append(f"product title differs: '{seed.get('product_title')}' vs '{new.get('product_title')}'")

    # Size is identity-critical.
    s_size = normalize_size(seed.get("variant_title")) or normalize_size(seed.get("product_title"))
    n_size = normalize_size(new.get("variant_title")) or normalize_size(new.get("product_title"))
    if s_size and n_size:
        if s_size == n_size:
            supporting.append(f"size match: {s_size}")
        else:
            conflicting.append(f"SIZE DIFFERS: seed {s_size} vs new {n_size} — never merge across sizes")
    elif s_size or n_size:
        cautions.append(f"size only determinable on one side (seed={s_size}, new={n_size})")

    if is_packish(seed.get("product_title")) != is_packish(new.get("product_title")):
        conflicting.append("gift/combo/variety-pack indicator present on only one side")

    if seed.get("retail_price") and new.get("retail_price"):
        if str(seed["retail_price"]) == str(new["retail_price"]):
            supporting.append(f"retail price match (weak): {new['retail_price']}")
        else:
            cautions.append(f"retail price differs: {seed['retail_price']} vs {new['retail_price']} (not identity evidence)")

    strong_ids = [s for s in supporting if s.startswith(("sku exact", "barcode exact"))]
    return {
        "new_variant_id": new.get("variant_id"),
        "supporting": supporting,
        "conflicting": conflicting,
        "cautions": cautions,
        "has_unique_identifier_match": bool(strong_ids),
        "heightened_review": bool(cautions) or _touches_dup(seed, dup_skus, dup_barcodes) or _touches_dup(new, dup_skus, dup_barcodes),
    }


def _touches_dup(row: dict, dup_skus: set[str], dup_barcodes: set[str]) -> bool:
    return ((row.get("sku") or "").strip() in dup_skus) or ((row.get("barcode") or "").strip() in dup_barcodes)


def classify_deleted_vs_new(seed: dict, new_rows: list[dict], dup_skus: set[str], dup_barcodes: set[str]) -> dict:
    """Classify one truly-deleted historical identity against all NEW live variants.
    Deterministic evidence only; fuzzy title similarity alone never nominates a candidate."""
    comparisons = []
    for new in new_rows:
        cmp_ = compare_pair(seed, new, dup_skus, dup_barcodes)
        # A pair is only a candidate if a unique identifier (non-duplicated SKU or
        # barcode) matches exactly. Title/vendor/size agreement alone is not identity.
        if cmp_["has_unique_identifier_match"]:
            comparisons.append(cmp_)
    heightened = _touches_dup(seed, dup_skus, dup_barcodes)
    if not comparisons:
        return {"classification": "NO_RECREATION_CANDIDATE", "candidates": [],
                "heightened_review": heightened,
                "reason": "no NEW live variant shares a unique SKU/barcode with this historical identity"}
    if len(comparisons) > 1:
        return {"classification": "AMBIGUOUS", "candidates": comparisons, "heightened_review": True,
                "reason": f"{len(comparisons)} NEW variants share unique identifiers — human must disambiguate"}
    only = comparisons[0]
    if only["conflicting"] or only["heightened_review"] or heightened:
        return {"classification": "POSSIBLE_RECREATION_CANDIDATE", "candidates": [only],
                "heightened_review": True,
                "reason": "identifier matches but conflicting/cautionary evidence present — not safe for approval without scrutiny"}
    return {"classification": "STRONG_RECREATION_CANDIDATE", "candidates": [only],
            "heightened_review": False,
            "reason": "exactly one NEW variant with exact unique-identifier match and no conflicting evidence — still requires human approval"}


def classify_new_variant(new: dict, deleted_seeds: list[dict], dup_skus: set[str], dup_barcodes: set[str]) -> dict:
    """Reverse view: does this NEW live variant have a plausible historical predecessor?"""
    matches = []
    for seed in deleted_seeds:
        cmp_ = compare_pair(seed, new, dup_skus, dup_barcodes)
        if cmp_["has_unique_identifier_match"]:
            matches.append({"seed_variant_id": seed.get("variant_id"), **cmp_})
    heightened = _touches_dup(new, dup_skus, dup_barcodes)
    if not matches:
        return {"classification": "GENUINELY_NEW", "predecessors": [], "heightened_review": heightened,
                "reason": "no deleted historical identity shares a unique SKU/barcode"}
    if len(matches) > 1:
        return {"classification": "AMBIGUOUS", "predecessors": matches, "heightened_review": True,
                "reason": "multiple deleted historical identities share unique identifiers"}
    only = matches[0]
    if only["conflicting"] or only["heightened_review"] or heightened:
        return {"classification": "POSSIBLE_RECREATION", "predecessors": [only], "heightened_review": True,
                "reason": "identifier matches a deleted historical identity but with conflicts/cautions"}
    return {"classification": "LIKELY_RECREATION", "predecessors": [only], "heightened_review": False,
            "reason": "single clean unique-identifier match to a deleted historical identity — approval still required"}


# --- Extended continuity sweep (Canonical Spec full identity evidence) -------
#
# Candidate generation for human review ONLY. Never creates aliases, never
# approves, never retires, never writes to Shopify. SKU/barcode remain strong
# evidence but their absence or change does not by itself force NO candidate.

_PROOF_RE = re.compile(r"(\d{2,3}(?:\.\d)?)\s*(?:PROOF|PF)\b", re.IGNORECASE)
_ABV_RE = re.compile(r"(\d{1,2}(?:\.\d)?)\s*%", re.IGNORECASE)
_VINTAGE_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


def extract_proof(text: str | None) -> str | None:
    m = _PROOF_RE.search(text or "")
    return m.group(1) if m else None


def extract_abv(text: str | None) -> str | None:
    m = _ABV_RE.search(text or "")
    return m.group(1) if m else None


def extract_vintage(text: str | None) -> str | None:
    m = _VINTAGE_RE.search(text or "")
    return m.group(1) if m else None


def compare_identity(seed: dict, new: dict, dup_skus: set[str], dup_barcodes: set[str]) -> dict:
    """Full deterministic identity comparison per the Canonical Spec evidence list.
    Returns matched/conflicting identity components; never a decision."""
    matched, conflicting, cautions = [], [], []
    s_full = f"{seed.get('product_title') or ''} {seed.get('variant_title') or ''}"
    n_full = f"{new.get('product_title') or ''} {new.get('variant_title') or ''}"

    # Product identity: exact normalized title (brand + expression together).
    st, nt = normalize_title(seed.get("product_title")), normalize_title(new.get("product_title"))
    title_exact = bool(st) and st == nt
    s_tokens, n_tokens = set(st.split()), set(nt.split())
    title_subset = bool(st and nt) and not title_exact and (s_tokens <= n_tokens or n_tokens <= s_tokens)
    if title_exact:
        matched.append("product identity exact (normalized brand+expression)")
    elif title_subset:
        cautions.append(f"product titles related but not identical: '{seed.get('product_title')}' vs '{new.get('product_title')}'")
    elif st and nt:
        conflicting.append(f"product identity differs: '{seed.get('product_title')}' vs '{new.get('product_title')}'")

    # Size/volume/pack — identity-critical, exact normalized comparison.
    s_size = normalize_size(seed.get("variant_title")) or normalize_size(seed.get("product_title"))
    n_size = normalize_size(new.get("variant_title")) or normalize_size(new.get("product_title"))
    size_match = False
    if s_size and n_size:
        if s_size == n_size:
            size_match = True
            matched.append(f"exact size/pack: {s_size}")
        else:
            conflicting.append(f"SIZE/PACK CONFLICT: {s_size} vs {n_size}")
    elif not s_size and not n_size:
        svt, nvt = normalize_title(seed.get("variant_title")), normalize_title(new.get("variant_title"))
        if svt and svt == nvt:
            size_match = True
            matched.append(f"variant title exact (no size token): '{svt}'")
        elif svt and nvt and svt != nvt:
            conflicting.append(f"variant title differs: '{seed.get('variant_title')}' vs '{new.get('variant_title')}'")
    else:
        cautions.append(f"size determinable on only one side (seed={s_size}, new={n_size})")

    # Gift pack / combo / alternate pack.
    if is_packish(s_full) != is_packish(n_full):
        conflicting.append("gift/combo/alternate-pack indicator on only one side — not the same sellable identity by default")

    # Proof / ABV / vintage — material when present on both sides.
    for name, fn in (("proof", extract_proof), ("ABV", extract_abv), ("vintage", extract_vintage)):
        a, b = fn(s_full), fn(n_full)
        if a and b:
            if a == b:
                matched.append(f"{name} match: {a}")
            else:
                conflicting.append(f"{name.upper()} CONFLICT: {a} vs {b}")

    # Vendor / product type — supporting, not identity by themselves.
    for name in ("vendor", "product_type"):
        a, b = (seed.get(name) or "").strip(), (new.get(name) or "").strip()
        if a and b:
            if a.lower() == b.lower():
                matched.append(f"{name} match: {a}")
            else:
                cautions.append(f"{name} differs: '{a}' vs '{b}' (suppliers change; not identity-conclusive)")

    # SKU / barcode — strong when unique-exact; a change is noted, never disqualifying alone.
    sku_exact = barcode_exact = False
    a, b = (seed.get("sku") or "").strip(), (new.get("sku") or "").strip()
    if a and b:
        if a == b:
            if a in dup_skus:
                cautions.append(f"sku matches but '{a}' is duplicated in live catalog — not unique evidence")
            else:
                sku_exact = True
                matched.append(f"sku exact: {a}")
        else:
            cautions.append(f"sku changed: '{a}' -> '{b}' (supplier SKUs may change)")
    a, b = (seed.get("barcode") or "").strip(), (new.get("barcode") or "").strip()
    if a and b:
        if a == b:
            if a in dup_barcodes:
                cautions.append(f"barcode matches but '{a}' is duplicated in live catalog — not unique evidence")
            else:
                barcode_exact = True
                matched.append(f"barcode exact: {a}")
        else:
            cautions.append(f"barcode differs: '{a}' vs '{b}' — different UPC usually means different physical product")

    # Retail price — weak supporting evidence only.
    if seed.get("retail_price") and new.get("retail_price"):
        if str(seed["retail_price"]) == str(new["retail_price"]):
            matched.append(f"retail price match (weak): {new['retail_price']}")
        else:
            cautions.append(f"retail price differs: {seed['retail_price']} vs {new['retail_price']} (weak evidence only)")

    barcode_changed = any(c.startswith("barcode differs") for c in cautions)
    return {
        "new_variant_id": new.get("variant_id"),
        "seed_variant_id": seed.get("variant_id"),
        "matched": matched, "conflicting": conflicting, "cautions": cautions,
        "title_exact": title_exact, "title_subset": title_subset, "size_match": size_match,
        "sku_exact": sku_exact, "barcode_exact": barcode_exact, "barcode_changed": barcode_changed,
        "is_candidate": (sku_exact or barcode_exact
                         or (title_exact and size_match)
                         or (title_subset and size_match)),
    }


def continuity_sweep_deleted(seed: dict, new_rows: list[dict], dup_skus: set[str], dup_barcodes: set[str]) -> dict:
    """Classify one deleted historical identity using full identity evidence.
    Categories: HIGH_EVIDENCE_RECREATION_REVIEW / POSSIBLE_RECREATION_REVIEW /
    CONFLICT_AMBIGUOUS / NO_CREDIBLE_CURRENT_COUNTERPART. Never merges."""
    comparisons = [compare_identity(seed, n, dup_skus, dup_barcodes) for n in new_rows]
    candidates = [c for c in comparisons if c["is_candidate"]]
    if not candidates:
        return {"classification": "NO_CREDIBLE_CURRENT_COUNTERPART", "candidates": [],
                "reason": "after full deterministic comparison (title, size, pack, proof/ABV/vintage, vendor, type, SKU, barcode, price), none of the NEW variants credibly represents this historical product",
                "recommended_action": "Human may authorize retirement (explicit approval required)"}
    if len(candidates) > 1:
        return {"classification": "CONFLICT_AMBIGUOUS", "candidates": candidates,
                "reason": f"{len(candidates)} NEW variants present credible identity evidence — human must disambiguate",
                "recommended_action": "Leave unresolved; investigate manually"}
    only = candidates[0]
    if only["conflicting"]:
        return {"classification": "CONFLICT_AMBIGUOUS", "candidates": [only],
                "reason": "material identity conflict present: " + "; ".join(only["conflicting"]),
                "recommended_action": "Leave unresolved; do not approve with conflicting identity fields"}
    strong = (only["sku_exact"] or only["barcode_exact"] or (only["title_exact"] and only["size_match"]))
    if strong and not only["barcode_changed"]:
        return {"classification": "HIGH_EVIDENCE_RECREATION_REVIEW", "candidates": [only],
                "reason": "deterministic identity evidence strongly suggests the same sellable product — explicit human approval still required",
                "recommended_action": "Review side-by-side; approve recreation only if correct"}
    return {"classification": "POSSIBLE_RECREATION_REVIEW", "candidates": [only],
            "reason": "meaningful identity evidence exists but certainty is insufficient",
            "recommended_action": "Review carefully; approve, reject, or leave unresolved"}


def continuity_sweep_new(new: dict, deleted_seeds: list[dict], dup_skus: set[str], dup_barcodes: set[str]) -> dict:
    """Reverse check: demonstrate each NEW variant was compared against every
    deleted historical identity using more than SKU/barcode."""
    comparisons = [compare_identity(s, new, dup_skus, dup_barcodes) for s in deleted_seeds]
    candidates = [c for c in comparisons if c["is_candidate"]]
    checked = {"historical_ids_checked": len(deleted_seeds),
               "evidence_used": ["normalized product identity", "normalized variant title", "exact size/pack",
                                  "gift/combo indicators", "proof", "ABV", "vintage", "vendor", "product type",
                                  "SKU", "barcode", "retail price (weak)"]}
    if not candidates:
        return {"classification": "GENUINELY_NEW", "predecessors": [], **checked,
                "reason": "no deleted historical identity shares credible deterministic identity evidence"}
    if len(candidates) > 1:
        return {"classification": "AMBIGUOUS", "predecessors": candidates, **checked,
                "reason": "multiple deleted historical identities present credible evidence"}
    only = candidates[0]
    if only["conflicting"]:
        return {"classification": "AMBIGUOUS", "predecessors": [only], **checked,
                "reason": "candidate has material identity conflicts"}
    if (only["sku_exact"] or only["barcode_exact"] or (only["title_exact"] and only["size_match"])) and not only["barcode_changed"]:
        return {"classification": "LIKELY_RECREATION", "predecessors": [only], **checked,
                "reason": "strong deterministic identity match to a deleted historical identity — approval still required"}
    return {"classification": "POSSIBLE_RECREATION", "predecessors": [only], **checked,
            "reason": "partial identity evidence to a deleted historical identity"}


# --- Orchestration ----------------------------------------------------------

def _variant_row(cur, variant_id: str) -> dict:
    cur.execute(
        """SELECT variant_id,product_title,variant_title,sku,barcode,shopify_vendor,product_type,retail_price
           FROM variants WHERE variant_id=%s""", (str(variant_id),))
    r = cur.fetchone()
    return {"variant_id": str(r[0]), "product_title": r[1], "variant_title": r[2], "sku": r[3],
            "barcode": r[4], "vendor": r[5], "product_type": r[6],
            "retail_price": str(r[7]) if r[7] is not None else None} if r else {"variant_id": str(variant_id)}


def load_duplicate_identifier_groups(conn) -> tuple[set[str], set[str]]:
    with conn.cursor() as cur:
        cur.execute("""SELECT sku FROM variants WHERE catalog_state='LIVE' AND sku IS NOT NULL AND sku<>''
                       GROUP BY sku HAVING count(DISTINCT variant_id)>1""")
        skus = {r[0].strip() for r in cur.fetchall()}
        cur.execute("""SELECT barcode FROM variants WHERE catalog_state='LIVE' AND barcode IS NOT NULL AND barcode<>''
                       GROUP BY barcode HAVING count(DISTINCT variant_id)>1""")
        barcodes = {r[0].strip() for r in cur.fetchall()}
    return skus, barcodes


def run_identity_investigation(conn, client, catalog_sync_id: str) -> dict:
    """Execute Steps 1-4 for a completed sync run and persist all evidence.
    Idempotent per (sync, subject, variant): re-running replaces prior diagnostic rows."""
    with conn.cursor() as cur:
        cur.execute("""SELECT seed_variant_id FROM catalog_reconciliation_items
                       WHERE catalog_sync_id=%s AND classification='MISSING' ORDER BY seed_variant_id""",
                    (catalog_sync_id,))
        missing_ids = [str(r[0]) for r in cur.fetchall()]
        cur.execute("""SELECT live_variant_id FROM catalog_reconciliation_items
                       WHERE catalog_sync_id=%s AND classification='NEW' ORDER BY live_variant_id""",
                    (catalog_sync_id,))
        new_ids = [str(r[0]) for r in cur.fetchall()]
        missing_rows = [_variant_row(cur, v) for v in missing_ids]
        new_rows = [_variant_row(cur, v) for v in new_ids]

    dup_skus, dup_barcodes = load_duplicate_identifier_groups(conn)

    # Step 1 — direct Shopify lookups (read-only node queries).
    lookups: dict[str, tuple[dict | None, str]] = {}
    for vid in missing_ids:
        node = lookup_variant_node(client, vid)
        lookups[vid] = (node, classify_existence(node))

    deleted_seeds = [row for row in missing_rows
                     if lookups[row["variant_id"]][1] == "DELETED_OR_NO_LONGER_RESOLVABLE"]

    now = datetime.now(timezone.utc).isoformat()
    summary = {"missing_existence": {}, "deleted_classifications": {}, "new_classifications": {},
               "heightened_review_ids": [], "still_active_defect_ids": []}

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("DELETE FROM identity_investigations WHERE catalog_sync_id=%s", (catalog_sync_id,))
            for row in missing_rows:
                vid = row["variant_id"]
                node, existence = lookups[vid]
                evidence = {"seed": row, "lookup_at": now, "existence": existence}
                classification = existence
                if existence == "STILL_EXISTS_ACTIVE":
                    summary["still_active_defect_ids"].append(vid)
                    evidence["defect"] = "reconciliation marked MISSING but Shopify reports ACTIVE — enumeration defect, investigate before any decision"
                if existence == "DELETED_OR_NO_LONGER_RESOLVABLE":
                    # Tier 1: unique SKU/barcode. Tier 2: full identity evidence sweep.
                    evidence["identifier_tier_analysis"] = classify_deleted_vs_new(row, new_rows, dup_skus, dup_barcodes)
                    verdict = continuity_sweep_deleted(row, new_rows, dup_skus, dup_barcodes)
                    evidence["recreation_analysis"] = verdict
                    classification = f"DELETED/{verdict['classification']}"
                    summary["deleted_classifications"][verdict["classification"]] = \
                        summary["deleted_classifications"].get(verdict["classification"], 0) + 1
                    if _touches_dup(row, dup_skus, dup_barcodes) or any(
                            c.get("cautions") for c in verdict.get("candidates", [])):
                        summary["heightened_review_ids"].append(vid)
                summary["missing_existence"][existence] = summary["missing_existence"].get(existence, 0) + 1
                cur.execute(
                    """INSERT INTO identity_investigations(catalog_sync_id,subject,variant_id,shopify_lookup_json,
                           shopify_status,classification,evidence_json,heightened_review)
                       VALUES (%s,'MISSING_SEED',%s,%s::jsonb,%s,%s,%s::jsonb,%s)""",
                    (catalog_sync_id, vid, json.dumps(node), existence.replace("STILL_EXISTS_", "") if node else "NOT_RESOLVABLE",
                     classification, json.dumps(evidence),
                     vid in summary["heightened_review_ids"] or existence == "STILL_EXISTS_ACTIVE"))
            for row in new_rows:
                verdict = continuity_sweep_new(row, deleted_seeds, dup_skus, dup_barcodes)
                verdict["heightened_review"] = (_touches_dup(row, dup_skus, dup_barcodes)
                                                or any(c.get("cautions") for c in verdict.get("predecessors", [])))
                summary["new_classifications"][verdict["classification"]] = \
                    summary["new_classifications"].get(verdict["classification"], 0) + 1
                if verdict["heightened_review"]:
                    summary["heightened_review_ids"].append(row["variant_id"])
                cur.execute(
                    """INSERT INTO identity_investigations(catalog_sync_id,subject,variant_id,shopify_lookup_json,
                           shopify_status,classification,evidence_json,heightened_review)
                       VALUES (%s,'NEW_LIVE',%s,NULL,'ACTIVE',%s,%s::jsonb,%s)""",
                    (catalog_sync_id, row["variant_id"], verdict["classification"],
                     json.dumps({"new": row, "analysis": verdict, "lookup_at": now}),
                     verdict["heightened_review"]))
    return summary
