import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import unittest
from procurement_os.shopify.auth import ClientCredentialsTokenProvider, ShopifyConfig


class Response:
    def __init__(self,payload): self.payload=payload
    def raise_for_status(self): pass
    def json(self): return self.payload


class ShopifyAuthTests(unittest.TestCase):
    def test_domain_normalization(self):
        c=ShopifyConfig('buffalo-house','id','secret')
        self.assertEqual(c.shop_domain,'buffalo-house.myshopify.com')
        self.assertIn('/admin/api/2026-07/graphql.json',c.graphql_url)

    def test_token_cached_until_margin(self):
        calls=[]; now=[0.0]
        def post(*args,**kwargs):
            calls.append(1); return Response({'access_token':f't{len(calls)}','scope':'read_products','expires_in':1000})
        p=ClientCredentialsTokenProvider(ShopifyConfig('s','i','x'),refresh_margin_seconds=100,post_form=post,clock=lambda:now[0])
        self.assertEqual(p.get_token(),'t1')
        now[0]=800
        self.assertEqual(p.get_token(),'t1')
        now[0]=901
        self.assertEqual(p.get_token(),'t2')
        self.assertEqual(len(calls),2)

    def test_invalidate_forces_refresh(self):
        calls=[]
        def post(*args,**kwargs):
            calls.append(1); return Response({'access_token':str(len(calls)),'expires_in':1000})
        p=ClientCredentialsTokenProvider(ShopifyConfig('s','i','x'),post_form=post,clock=lambda:0)
        self.assertEqual(p.get_token(),'1'); p.invalidate(); self.assertEqual(p.get_token(),'2')

    def test_malformed_token_response_does_not_echo_payload(self):
        def post(*args,**kwargs):
            return Response({'diagnostic':'sensitive-upstream-payload'})
        p=ClientCredentialsTokenProvider(ShopifyConfig('s','i','x'),post_form=post,clock=lambda:0)
        with self.assertRaises(RuntimeError) as ctx:
            p.get_token()
        self.assertNotIn('sensitive-upstream-payload',str(ctx.exception))

if __name__ == '__main__': unittest.main()
