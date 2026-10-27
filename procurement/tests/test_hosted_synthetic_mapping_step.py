"""Causal scripted DOM tests, not authenticated browser acceptance."""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from uuid import UUID, uuid5, NAMESPACE_URL

ROOT=Path(__file__).resolve().parents[2]


class HostedMappingStepTests(unittest.TestCase):
    def cases(self,*names):
        for name in names:
            with self.subTest(case=name):
                node=shutil.which('node');self.assertIsNotNone(node)
                result=subprocess.run([node,str(ROOT/'procurement/tests/hosted_mapping_step_fixture.mjs'),name],
                    cwd=ROOT,env={'PATH':os.defpath,'LC_ALL':'C.UTF-8'},capture_output=True,text=True,timeout=5)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(json.loads(result.stdout)['assertion'],'PASS')

    def test_intake_seven_decisions_five_selections_derive_from_25_posts(self):self.cases('success')
    def test_binding_identity_and_exact_variant_population_refuse_before_page(self):self.cases('binding_variant','binding_duplicate','binding_source')
    def test_binding_accessors_never_execute(self):self.cases('binding_getter')
    def test_array_coercion_cannot_masquerade_as_bound_source_or_candidate(self):
        self.cases(*('binding_array:'+key for key in ('commit','tree','fixture_proof_sha256','apply_proof_sha256','candidate_id')))
    def test_preseeded_decisions_or_cutover_authority_refuse_before_intake(self):self.cases('preseeded','authority')
    def test_intake_population_must_be_exact(self):self.cases('intake_count')
    def test_candidate_variant_occurrence_and_vendor_are_exact(self):self.cases('row_variant','row_occurrence','row_vendor')
    def test_foreign_duplicate_and_predecided_candidates_refuse(self):self.cases('row_route','row_duplicate','row_predecided')
    def test_wrong_detail_location_cannot_submit(self):self.cases('detail_wrong_url')
    def test_missing_exact_offer_never_selects_first_other_option(self):self.cases('missing_exact_offer')
    def test_preview_reason_offer_and_idempotency_are_bound(self):self.cases('preview_reason','preview_offer','preview_idempotency')
    def test_preview_hash_confirm_and_response_refuse(self):self.cases('preview_hash','preview_confirm','preview_http')
    def test_legacy_actor_and_duplicate_fields_cannot_reach_confirmation(self):self.cases('preview_legacy','preview_actor','preview_duplicate')
    def test_changed_csrf_action_or_target_refuses_before_final_post(self):self.cases('changed_csrf','changed_action','changed_target')
    def test_actual_changed_duplicate_and_replayed_mutations_are_blocked(self):self.cases('request_duplicate','request_csrf','request_route','request_repeat')
    def test_redirect_is_exact_same_origin_303(self):self.cases('redirect_foreign','redirect_status')
    def test_selection_keeps_mapping_identity_date_and_select_action(self):self.cases('selection_mapping_id','selection_foreign_mapping','selection_clear','selection_date')
    def test_final_selection_heads_are_observed_not_assumed(self):self.cases('final_count')

    def test_decision_identity_matches_existing_python_uuid5_contract(self):
        key='aaaaaaaa-bbbb-4ccc-8ddd-000000000201'
        script="import {mappingDecisionId} from './procurement/tools/hosted_synthetic_mapping_step.mjs';console.log(mappingDecisionId(process.argv[1]));"
        result=subprocess.run([shutil.which('node'),'--input-type=module','-e',script,key],cwd=ROOT,
            env={'PATH':os.defpath,'LC_ALL':'C.UTF-8'},capture_output=True,text=True,timeout=5,check=True)
        self.assertEqual(result.stdout.strip(),str(uuid5(NAMESPACE_URL,'buffalo-mapping-decision:'+key)))
        source=(ROOT/'procurement/src/procurement_os/persistent_mapping.py').read_text()
        self.assertIn('uuid5(NAMESPACE_URL, f"buffalo-mapping-decision:{decision_idempotency_key}")',source)

    def test_actual_api_confirmation_form_has_implicit_action_and_exact_fields(self):
        module=ast.parse((ROOT/'procurement/src/procurement_os/api.py').read_text())
        names={'_mapping_confirmation_html','_html_escape','_form_value'}
        functions=[item for item in module.body if isinstance(item,ast.FunctionDef) and item.name in names]
        namespace={'UUID':UUID,'_mapping_banner':lambda:'synthetic banner'}
        exec(compile(ast.Module(body=functions,type_ignores=[]),'actual-api-renderer','exec'),namespace)
        class Forms(HTMLParser):
            def __init__(self):super().__init__();self.forms=[];self.inputs=[]
            def handle_starttag(self,tag,attrs):
                if tag=='form':self.forms.append(dict(attrs))
                elif tag=='input':self.inputs.append(dict(attrs))
        for values in ({'action':'DEFER','reason':'synthetic','idempotency_key':'key','existing_offer_id':'','offer_link_kind':''},
                       {'selection_action':'SELECT','reason':'synthetic','effective_from':'2026-10-05','idempotency_key':'key','mapping_decision_id':'decision'}):
            page=Forms();page.feed(namespace['_mapping_confirmation_html'](candidate_id=UUID('aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'),kind='synthetic',values=values,preview_sha256='a'*64))
            self.assertEqual(page.forms,[{'method':'post'}])
            self.assertEqual({item['name']:item['value'] for item in page.inputs},{**values,'expected_preview_sha256':'a'*64,'confirm':'CONFIRM'})
        self.assertEqual(namespace['_html_escape'](None),'—')
