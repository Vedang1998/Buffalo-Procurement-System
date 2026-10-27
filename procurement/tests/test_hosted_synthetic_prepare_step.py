"""Causal retirement/preparation component checks, not browser acceptance."""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from uuid import UUID

ROOT=Path(__file__).resolve().parents[2]


class HostedPrepareStepTests(unittest.TestCase):
    def cases(self,*names):
        for name in names:
            with self.subTest(case=name):
                node=shutil.which('node');self.assertIsNotNone(node)
                result=subprocess.run([node,str(ROOT/'procurement/tests/hosted_prepare_step_fixture.mjs'),name],cwd=ROOT,
                    env={'PATH':os.defpath,'LC_ALL':'C.UTF-8'},capture_output=True,text=True,timeout=5)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(json.loads(result.stdout)['assertion'],'PASS')

    def test_three_posts_retire_preserved_v1_then_derive_v2_review_binding(self):self.cases('success')
    def test_identity_types_and_accessors_refuse_before_adapter(self):self.cases('binding_array','binding_getter')
    def test_mapping_completion_is_required_before_retirement(self):self.cases('mapping_incomplete')
    def test_stale_terminal_state_or_artifacts_prevent_retirement(self):self.cases('stale_state','stale_artifact')
    def test_every_retirement_fact_is_bound_before_confirmation(self):
        self.cases(*('retirement:'+key for key in ('Run','Business date','Frozen fingerprint','Retired method','Transition','Purchase orders / artifacts','Authenticated actor','Reason','Confirmation SHA-256')))
    def test_duplicate_retirement_facts_and_wrong_confirmation_hash_refuse(self):self.cases('retirement_duplicate','confirmation_hash')
    def test_retirement_http_refusal_and_unobserved_failed_state_stop_prepare(self):self.cases('retire_http','retired_state')
    def test_changed_csrf_blocks_retirement_confirmation(self):self.cases('changed_csrf')
    def test_actual_duplicate_wrong_route_and_repeated_requests_are_blocked(self):self.cases('request_duplicate','request_route','request_repeat')
    def test_foreign_prepare_redirect_or_old_method_is_not_ready(self):self.cases('prepare_redirect','prepared_method')
    def test_original_calculated_quantity_and_action_identity_are_exact(self):self.cases('prepared_quantity','prepared_route','prepared_duplicate_id')
    def test_missing_selection_and_loose_fee_blockers_remain_explicit(self):self.cases('blocker_reason')
    def test_forecast_commercial_authority_confidence_and_qualification_refuse(self):self.cases('forecast_authority','forecast_confidence','forecast_qualification')
    def test_forecast_calendar_history_and_policy_are_bound(self):self.cases('forecast_calendar','forecast_availability','forecast_policy')
    def test_forecast_calendar_vendor_and_aliases_cannot_conflict(self):self.cases('forecast_vendor','forecast_conflicting_alias','forecast_alias_only')
    def test_forecast_calendar_basis_buffalo_date_timezone_and_horizon_are_exact(self):self.cases('forecast_timezone','forecast_business_date','forecast_basis','forecast_horizon')
    def test_exactly_four_forecasts_each_in_one_owned_section(self):self.cases('forecast_duplicate','forecast_orphan')
    def test_human_visible_authority_warning_and_limited_qualification_remain(self):self.cases('forecast_warning','forecast_visible_qualification')
    def test_forecast_point_plus_protection_equals_target(self):self.cases('forecast_units')
    def test_origin_windows_keep_counts_and_nonindependence(self):self.cases('forecast_origins','forecast_independence')

    def test_actual_retirement_renderer_has_only_bound_confirmation_fields(self):
        module=ast.parse((ROOT/'procurement/src/procurement_os/api.py').read_text())
        names={'_monday_stale_forecast_retirement_preview_html','_html_escape','_form_value'}
        ns={'UUID':UUID}
        functions=[n for n in module.body if isinstance(n,ast.FunctionDef) and n.name in names]
        exec(compile(ast.Module(body=functions,type_ignores=[]),'actual-retirement-renderer','exec'),ns)
        preview={'run_id':'00000000-0000-4000-8000-000000000901','business_date':'2026-10-05','input_fingerprint':'a'*64,
            'retired_model_version':'EMERGENCY_TRANSPARENT_V1','prior_status':'RUNNING','prior_workflow_stage':'PREPARING',
            'target_status':'FAILED','target_workflow_stage':'FAILED','purchase_order_count':0,'artifact_count':0,
            'actor':'owner:staging:01','reason':'Synthetic retired forecast method requires V2 re-preparation','confirmation_sha256':'b'*64}
        class Forms(HTMLParser):
            def __init__(self):super().__init__();self.forms=[];self.fields={}
            def handle_starttag(self,tag,attrs):
                attrs=dict(attrs)
                if tag=='form':self.forms.append(attrs)
                elif tag=='input':self.fields[attrs['name']]=attrs.get('value')
        page=Forms();page.feed(ns['_monday_stale_forecast_retirement_preview_html'](run_id=UUID(preview['run_id']),preview=preview))
        self.assertEqual(page.forms,[{'method':'post'}])
        self.assertEqual(page.fields,{'reason':preview['reason'],'expected_confirmation_sha256':'b'*64,'review_token':None})
        # The actual gateway stripping/injection path is independently executed
        # by the review-step form contract test; no legacy token is supplied.
