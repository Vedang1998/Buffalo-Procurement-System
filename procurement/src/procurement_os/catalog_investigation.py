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
                    verdict = classify_deleted_vs_new(row, new_rows, dup_skus, dup_barcodes)
                    evidence["recreation_analysis"] = verdict
                    classification = f"DELETED/{verdict['classification']}"
                    summary["deleted_classifications"][verdict["classification"]] = \
                        summary["deleted_classifications"].get(verdict["classification"], 0) + 1
                    if verdict["heightened_review"]:
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
                verdict = classify_new_variant(row, deleted_seeds, dup_skus, dup_barcodes)
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
