from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class AssortmentOffer:
    product_id: str
    assortable: bool | None = None
    assortment_scope: str = "PRODUCT"
    assortment_group: str | None = None

def may_assort(a: AssortmentOffer, b: AssortmentOffer) -> bool:
    # Book-stated non-assortable always wins.
    if a.assortable is False or b.assortable is False:
        return False
    # Default rule: assortment remains inside one Shopify product.
    if a.product_id == b.product_id:
        return True
    # Rare cross-product programs require explicit evidence on both sides.
    return bool(
        a.assortment_scope == "EXPLICIT_CROSS_PRODUCT"
        and b.assortment_scope == "EXPLICIT_CROSS_PRODUCT"
        and a.assortment_group
        and a.assortment_group == b.assortment_group
    )
