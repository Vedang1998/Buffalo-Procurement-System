import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import unittest
from datetime import date
from decimal import Decimal

from procurement_os.sales import (
    CurrentIdentity, HistoricalAlias, HistoricalIdentityIndex, HistoricalSourceDecision, SalesSourceRow,
    date_chunks, fetch_shopifyql_sales, parse_shopifyql_row, source_row_hash,
)


def sale(*, vid=None, sku=None, product='A', variant='750ML', units='1', net='10'):
    return SalesSourceRow(date(2024,11,28), vid, sku, product, variant, Decimal(units), Decimal(net))


class FakeClient:
    def __init__(self, pages): self.pages=list(pages); self.queries=[]
    def query(self, query, variables):
        self.queries.append(variables['query'])
        rows=self.pages.pop(0)
        return {'shopifyqlQuery': {'tableData': {'columns': [], 'rows': rows}, 'parseErrors': []}}


class SalesTests(unittest.TestCase):
    def setUp(self):
        self.idx = HistoricalIdentityIndex(
            [CurrentIdentity('100','NOW','Current A','750ML')],
            [HistoricalAlias('100','50','OLD','Old A','750ML')],
        )

    def test_current_variant_id_resolves(self):
        r=self.idx.resolve(sale(vid='100', sku='anything'))
        self.assertEqual((r.status,r.canonical_variant_id,r.method),('RESOLVED','100','EXACT_ACTIVE_VARIANT_ID'))

    def test_old_variant_id_resolves(self):
        r=self.idx.resolve(sale(vid='50', sku='OLD', product='Old A'))
        self.assertEqual((r.status,r.canonical_variant_id),('RESOLVED','100'))

    def test_null_id_resolves_from_historical_sku_title(self):
        r=self.idx.resolve(sale(vid=None, sku='OLD', product='Old A', variant='750ML'))
        self.assertEqual((r.status,r.canonical_variant_id),('RESOLVED','100'))

    def test_zero_id_is_absent_and_resolves(self):
        r=self.idx.resolve(sale(vid='0', sku='OLD', product='Old A'))
        self.assertEqual(r.canonical_variant_id,'100')

    def test_unique_sku_alone_does_not_resolve_when_title_changed(self):
        r=self.idx.resolve(sale(vid=None, sku='OLD', product='Renamed A'))
        self.assertEqual((r.status,r.canonical_variant_id,r.method),('UNRESOLVED',None,'SKU_EVIDENCE_ONLY'))
        self.assertEqual(r.candidates,('100',))

    def test_duplicate_sku_is_ambiguous(self):
        idx=HistoricalIdentityIndex(
            [CurrentIdentity('100','DUP','A','750ML'), CurrentIdentity('200','DUP','B','750ML')], []
        )
        r=idx.resolve(sale(sku='DUP', product='Other'))
        self.assertEqual(r.status,'AMBIGUOUS')
        self.assertEqual(set(r.candidates),{'100','200'})


    def test_explicit_exclusion_is_structured(self):
        row=sale(vid=None,sku='OLD',product='Old A')
        key=HistoricalIdentityIndex.source_key(row)
        idx=HistoricalIdentityIndex([],[],excluded_source_keys=[key])
        r=idx.resolve(row)
        self.assertEqual(r.status,'EXCLUDED')

    def test_exact_source_key_map_is_human_authority_not_title_matching(self):
        row=sale(vid='0',sku=None,product='Historical Title Only',variant='750ML')
        decision=HistoricalSourceDecision(
            HistoricalIdentityIndex.source_key(row),'MAP','100'
        )
        idx=HistoricalIdentityIndex(
            [CurrentIdentity('100',None,'Canonical Product','750ML')],[],
            source_decisions=[decision],
        )
        resolved=idx.resolve(row)
        self.assertEqual(
            (resolved.status,resolved.canonical_variant_id,resolved.method),
            ('RESOLVED','100','APPROVED_SOURCE_IDENTITY_DECISION'),
        )

    def test_source_key_map_does_not_leak_to_normalized_similar_identity(self):
        approved=sale(vid='0',sku=None,product='Historical Title Only',variant='750ML')
        idx=HistoricalIdentityIndex(
            [CurrentIdentity('100',None,'Canonical Product','750ML')],[],
            source_decisions=[HistoricalSourceDecision(
                HistoricalIdentityIndex.source_key(approved),'MAP','100'
            )],
        )
        other=sale(vid='0',sku=None,product='Historical Title Only Reserve',variant='750ML')
        self.assertEqual(idx.resolve(other).status,'UNRESOLVED')

    def test_leave_unresolved_source_decision_never_becomes_mapping(self):
        row=sale(vid='0',sku=None,product='Deliberately Unresolved',variant='8PK 12OZ')
        idx=HistoricalIdentityIndex(
            [CurrentIdentity('100',None,'Canonical Product','750ML')],[],
            source_decisions=[HistoricalSourceDecision(
                HistoricalIdentityIndex.source_key(row),'LEAVE_UNRESOLVED',None
            )],
        )
        self.assertEqual(idx.resolve(row).status,'UNRESOLVED')

    def test_source_key_map_can_target_preserved_inactive_variant(self):
        row=sale(vid='0',sku='OLD-INACTIVE',product='Historical Inactive',variant='750ML')
        idx=HistoricalIdentityIndex(
            [CurrentIdentity('300','OLD-INACTIVE','Retired Canonical','750ML',False,'RETIRED_CONFIRMED')],[],
            source_decisions=[HistoricalSourceDecision(
                HistoricalIdentityIndex.source_key(row),'MAP','300'
            )],
        )
        resolved=idx.resolve(row)
        self.assertEqual((resolved.status,resolved.canonical_variant_id),('RESOLVED','300'))

    def test_unknown_is_unresolved(self):
        self.assertEqual(self.idx.resolve(sale(sku='NOPE')).status,'UNRESOLVED')

    def test_hash_is_identity_of_group_not_metric_value(self):
        a=sale(vid='100',sku='NOW',units='1',net='10')
        b=sale(vid='100',sku='NOW',units='2',net='20')
        self.assertEqual(source_row_hash(a),source_row_hash(b))

    def test_parse_shopifyql_null_id(self):
        r=parse_shopifyql_row({'day':'2024-11-28','product_variant_id':'0','product_variant_sku_at_time_of_sale':'X','product_title_at_time_of_sale':'P','product_variant_title_at_time_of_sale':'750ML','net_items_sold':'3','net_sales':'17.97'})
        self.assertEqual(r.source_variant_id, '0')
        self.assertEqual(r.net_items_sold,Decimal('3'))

    def test_date_chunks_no_gaps(self):
        chunks=list(date_chunks(date(2026,1,1),date(2026,3,1),days=31))
        self.assertEqual(chunks[0],(date(2026,1,1),date(2026,1,31)))
        self.assertEqual(chunks[-1][1],date(2026,3,1))
        for left,right in zip(chunks,chunks[1:]):
            self.assertEqual(left[1].toordinal()+1,right[0].toordinal())

    def test_fetch_paginates_and_dedupes(self):
        row={'day':'2024-11-28','product_variant_id':'100','product_variant_sku_at_time_of_sale':'NOW','product_title_at_time_of_sale':'A','product_variant_title_at_time_of_sale':'750ML','net_items_sold':'1','net_sales':'10'}
        c=FakeClient([[row,row],[]])
        out=fetch_shopifyql_sales(c,date(2024,11,28),date(2024,11,28),limit=2)
        self.assertEqual(len(out),1)
        self.assertEqual(len(c.queries),2)
        self.assertIn('LIMIT 2 OFFSET 0',c.queries[0])
        self.assertIn('LIMIT 2 OFFSET 2',c.queries[1])

if __name__ == '__main__': unittest.main()
