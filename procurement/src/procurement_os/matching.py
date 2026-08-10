from __future__ import annotations
from dataclasses import dataclass
from difflib import SequenceMatcher
import re, unicodedata

SIZE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(ML|L)\b", re.I)

def normalize_text(text: str | None) -> str:
    if not text: return ""
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").upper()
    s = s.replace("&", " AND ")
    for old,new in {"SAUVIGNON BLANC":"SB","CABERNET SAUVIGNON":"CAB","PINOT GRIGIO":"PG","PINOT NOIR":"PN","LITER":"L","LITRE":"L"}.items():
        s = s.replace(old,new)
    s = re.sub(r"[^A-Z0-9.]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def extract_size(text: str | None) -> str | None:
    m = SIZE_RE.search(normalize_text(text))
    if not m: return None
    value = float(m.group(1)); unit = m.group(2).upper()
    ml = value * 1000 if unit == "L" else value
    return f"{int(ml) if ml.is_integer() else ml:g}ML"

@dataclass(frozen=True)
class MatchCandidate:
    supplier_text: str
    shopify_product_title: str
    shopify_variant_title: str
    supplier_size: str | None = None
    shopify_size: str | None = None
    supplier_pack: str | None = None
    shopify_pack: str | None = None

@dataclass(frozen=True)
class MatchResult:
    score: float
    auto_match: bool
    review: bool
    blocked: bool
    reasons: tuple[str,...]

def score_candidate(c: MatchCandidate, auto_threshold=.92, review_threshold=.82) -> MatchResult:
    reasons=[]
    sn=normalize_text(c.supplier_text); qn=normalize_text(f"{c.shopify_product_title} {c.shopify_variant_title}")
    ss=c.supplier_size or extract_size(c.supplier_text); qs=c.shopify_size or extract_size(c.shopify_variant_title)
    if ss and qs and ss != qs:
        return MatchResult(0.0,False,True,True,("SIZE_CONFLICT",))
    pack_penalty=0.0
    if c.supplier_pack and c.shopify_pack and normalize_text(c.supplier_pack)!=normalize_text(c.shopify_pack):
        pack_penalty=.12; reasons.append("PACK_CONFLICT_REVIEW")
    ratio=SequenceMatcher(None,sn,qn).ratio()
    st=set(sn.split()); qt=set(qn.split())
    token=len(st & qt)/max(1,len(st | qt))
    score=max(ratio,token*.9+ratio*.1)-pack_penalty
    if ss and qs and ss==qs: score=min(1,score+.05); reasons.append("SIZE_MATCH")
    score=max(0,min(1,score))
    if pack_penalty: return MatchResult(score,False,True,False,tuple(reasons))
    if score>=auto_threshold: return MatchResult(score,True,False,False,tuple(reasons+["HIGH_CONFIDENCE"]))
    if score>=review_threshold: return MatchResult(score,False,True,False,tuple(reasons+["REVIEW_THRESHOLD"]))
    return MatchResult(score,False,True,False,tuple(reasons+["LOW_CONFIDENCE"]))
