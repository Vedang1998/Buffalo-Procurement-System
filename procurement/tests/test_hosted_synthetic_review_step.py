"""Owner review/DRAFT component causal tests; not live browser acceptance."""
import ast
import html
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from decimal import Decimal
from html.parser import HTMLParser
from uuid import UUID
import re

ROOT=Path(__file__).resolve().parents[2]


class HostedReviewStepTests(unittest.TestCase):
    def cases(self,*names):
        for name in names:
            with self.subTest(case=name):
                node=shutil.which('node');self.assertIsNotNone(node)
                result=subprocess.run([node,str(ROOT/'procurement/tests/hosted_review_step_fixture.mjs'),name],cwd=ROOT,
                    env={'PATH':os.defpath,'LC_ALL':'C.UTF-8'},capture_output=True,text=True,timeout=5)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(json.loads(result.stdout)['assertion'],'PASS')

    def test_eleven_posts_review_edit_reject_then_build_two_drafts(self):self.cases('success')
    def test_source_types_and_exact_recommendation_population_precede_page(self):self.cases('binding_source','binding_recommendations','binding_getter')
    def test_preseeded_stage_or_old_forecast_method_cannot_start_review(self):self.cases('initial_stage','initial_model')
    def test_original_variant_and_calculated_quantity_must_match(self):self.cases('initial_variant','initial_quantity')
    def test_recalculated_line_total_case_price_and_fee_are_verified_before_confirmation(self):self.cases('economics_total','economics_case_price','economics_fee')
    def test_recalculated_ordered_units_and_unit_cost_preserve_pack_identity(self):self.cases('economics_units','economics_unit_cost')
    def test_final_tier_remains_bound_to_applied_price_batch(self):self.cases('tier_binding','tier_southern_break','tier_bottle_break','tier_unit_price')
    def test_preview_fingerprint_quantity_or_http_refusal_stops_final_post(self):self.cases('preview_fingerprint','preview_quantity','preview_http')
    def test_unexpected_material_confirmation_and_legacy_token_are_not_bypassed(self):self.cases('material_required','preview_legacy')
    def test_changed_csrf_is_rechecked_before_request(self):self.cases('changed_csrf')
    def test_actual_duplicate_wrong_route_and_repeat_request_are_blocked(self):self.cases('request_duplicate','request_wrong_route','request_repeat')
    def test_final_review_redirect_must_be_exact_relative_same_origin_303(self):self.cases('redirect_foreign','redirect_status')
    def test_vendor_partition_fee_and_totals_precede_draft_confirmation(self):self.cases('draft_fee','draft_vendor','draft_total')
    def test_canonical_minimum_and_shortfall_precede_pay_fee_confirmation(self):self.cases('draft_minimum','draft_shortfall')
    def test_draft_hash_and_pay_fee_disposition_are_retained(self):self.cases('build_hash','build_disposition')
    def test_built_stage_and_three_lines_are_observed_after_post(self):self.cases('final_stage','final_line','final_partition')
    def test_captured_available_and_time_stay_frozen_in_built_lines(self):self.cases('final_stock','final_capture')
    def test_each_vendor_section_retains_its_own_fee_and_total(self):self.cases('final_vendor_fee','final_duplicate_po','final_shortfall','final_disposition','final_actor')
    def test_final_supplier_sku_ordered_units_and_unit_cost_are_exact(self):self.cases('final_sku','final_units','final_unit_cost')
    def test_persisted_tier_matches_the_exact_reviewed_tier(self):self.cases('final_tier')
    def test_duplicate_or_wrong_artifact_links_cannot_be_reported_as_three_outputs(self):self.cases('artifact_duplicate','artifact_kind')

    def test_preview_form_fields_come_from_actual_api_and_gateway_renderers(self):
        api=ast.parse((ROOT/'procurement/src/procurement_os/api.py').read_text())
        names={'_monday_review_preview_html','_monday_draft_preview_html','_html_escape','_form_value'}
        functions=[n for n in api.body if isinstance(n,ast.FunctionDef) and n.name in names]
        ns={'UUID':UUID,'Decimal':Decimal,'json':json,'html':html,'re':re}
        exec(compile(ast.Module(body=functions,type_ignores=[]),'actual-api-renderers','exec'),ns)
        gateway=ast.parse((ROOT/'procurement/src/procurement_os/staging_gateway.py').read_text())
        constants={'_REVIEW_TOKEN_LABEL','_REVIEW_TOKEN_INPUT','_POST_FORM','_CSRF_FORM_FIELD','_MAX_HTML_FORMS'}
        selected=[n for n in gateway.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in constants for t in n.targets)]
        methods=[n for cls in gateway.body if isinstance(cls,ast.ClassDef) for n in cls.body
                 if isinstance(n,ast.FunctionDef) and n.name in {'_strip_legacy_review_token_fields','_inject_csrf_forms'}]
        self.assertEqual(len(methods),2)
        for n in methods:n.decorator_list=[]
        ns['StagingGatewayError']=ValueError
        exec(compile(ast.Module(body=selected+methods,type_ignores=[]),'actual-gateway-renderers','exec'),ns)
        class Forms(HTMLParser):
            def __init__(self):super().__init__();self.forms=[];self.fields=[]
            def handle_starttag(self,tag,attrs):
                if tag=='form':self.forms.append(dict(attrs))
                elif tag=='input':self.fields.append(dict(attrs)['name'])
        preview={'metrics':{},'materiality':{},'action':'ACCEPT','approved_cases':6,'approved_loose_units':0,
                 'approved_units':36,'approved_unit_cost':'5.0000','approved_case_price':'30.0000',
                 'approved_merchandise_total':'180.00','approved_loose_order_fee':'0.00','approved_line_total':'180.00',
                 'resulting_inventory_units':36,'resulting_days_supply':18,'days_supply_status':'CALCULATED',
                 'input_fingerprint':'a'*64,'preview_fingerprint':'b'*64}
        pages=[(ns['_monday_review_preview_html'](run_id=UUID('aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'),recommendation_id=1,
                  preview=preview,actor='owner:staging:01',comment=''),
                {'action','actor','expected_input_fingerprint','approved_cases','approved_loose_units','comment','review_preview_fingerprint'}),
               (ns['_monday_draft_preview_html'](run_id=UUID('aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'),
                  preview={'vendors':[],'minimum_disposition':'PAY_FEE','actor':'owner:staging:01','preview_fingerprint':'b'*64}),
                {'actor','draft_preview_fingerprint','minimum_disposition'})]
        for raw,expected in pages:
            raw=ns['_strip_legacy_review_token_fields'](raw.encode())
            raw=ns['_inject_csrf_forms'](raw,'S'*43,max_bytes=100000)
            parsed=Forms();parsed.feed(raw.decode())
            self.assertEqual(parsed.forms,[{'method':'post'}])
            self.assertEqual(set(parsed.fields),expected|{'_buffalo_staging_csrf'})
            self.assertEqual(len(parsed.fields),len(expected)+1)

    def test_known_vendor_minima_match_current_synthetic_initializer(self):
        source=ast.parse((ROOT/'procurement/tools/initialize_synthetic_demo.py').read_text())
        sql=[n.value for n in ast.walk(source) if isinstance(n,ast.Constant) and isinstance(n.value,str)
             and 'INSERT INTO vendor_operating_rules(' in n.value]
        self.assertTrue(any("'DOLLAR',20,5,FALSE,NULL" in statement for statement in sql))
        self.assertTrue(any("'DOLLAR',120,7,TRUE,3" in statement for statement in sql))
