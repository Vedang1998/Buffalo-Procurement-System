"""Real JS transition and guarded form programs against a synthetic DOM adapter.

No browser, service, database or credential-bearing execution is represented.
"""
import hashlib
import json
import os
from pathlib import Path
from hosted_test_node import node_executable
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[2]
FIXTURE=ROOT/'procurement/tests/hosted_apply_step_fixture.mjs'


class HostedApplyStepTests(unittest.TestCase):
    def run_case(self,name):
        node=node_executable()
        self.assertIsNotNone(node,'Node is required for the scripted JS component tests')
        result=subprocess.run([node,str(FIXTURE),name],cwd=ROOT,
            env={'PATH':os.defpath,'LC_ALL':'C.UTF-8'},capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        observed=json.loads(result.stdout)
        if name!='canonical':self.assertEqual(observed['assertion'],'PASS')
        return observed

    def cases(self,*names):
        for name in names:
            with self.subTest(case=name):self.run_case(name)

    def test_real_transition_submits_only_preview_then_exact_apply(self):self.run_case('success')

    def test_javascript_intent_hash_matches_existing_python_canonical_contract(self):
        value=self.run_case('canonical')
        # Execute the existing pure canonical function without importing the
        # application's DB dependencies into the stdlib-only hosted test gate.
        import ast
        module=ast.parse((ROOT/'procurement/src/procurement_os/local_backup_v2.py').read_text())
        function=next(node for node in module.body if isinstance(node,ast.FunctionDef) and node.name=='canonical_sha256')
        namespace={'json':json,'hashlib':hashlib,'Any':object}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'existing-canonical-sha256','exec'),namespace)
        self.assertEqual(namespace['canonical_sha256'](value['intent']),value['sha256'])

    def test_unconfirmed_or_wrong_batch_cannot_submit_apply(self):self.cases('before_validated','before_wrong_batch','source_bad')

    def test_every_backup_binding_is_required_before_first_post(self):
        self.cases(*('backup:'+key for key in ('target_kind','manifest_sha256','prechange_scope_sha256','batch_id',
            'vendor_id','price_scope_key','prior_event_id','prior_head_version','raw_content_sha256',
            'staging_release_sha256','runtime_attestation_identity')))

    def test_preview_exact_identity_and_prior_head_cannot_drift(self):
        self.cases(*('preview:'+key for key in ('price_book_batch_id','vendor_id','price_scope_key',
            'prior_event_id','expected_prior_head_version','expected_current_scope_sha256')))

    def test_preview_recovery_and_runtime_binding_cannot_drift(self):
        self.cases(*('preview:'+key for key in ('backup_manifest_sha256','backup_prechange_scope_sha256',
            'staging_backup_release_sha256','staging_runtime_attestation_identity')))

    def test_preview_evidence_dates_and_scope_cannot_drift(self):
        self.cases(*('preview:'+key for key in ('raw_content_sha256','declaration_sha256','policy_sha256',
            'confirmation_payload_sha256','application_at','variant_ids','scope_membership_sha256')))

    def test_preview_row_controls_and_commercial_authority_fail_closed(self):
        self.cases(*('preview:'+key for key in ('expected_current_row_count','resulting_current_row_count',
            'commercial_authority','real_price_approval','unaffected_price_state_sha256','resulting_current_scope_sha256')))

    def test_preview_canonical_hash_unknown_field_and_http_refusal(self):self.cases('preview_hash','preview_extra','preview_http')

    def test_extra_duplicate_and_legacy_form_fields_block_final_post(self):self.cases('form_duplicate','form_legacy','form_backup_path')

    def test_wrong_actor_target_or_changed_action_cannot_submit(self):self.cases('form_actor','form_target','form_action_changed')

    def test_changed_csrf_is_rechecked_in_executed_dom_program(self):self.run_case('form_csrf_changed')

    def test_actual_post_guard_blocks_duplicate_or_changed_csrf_before_forwarding(self):self.cases('request_duplicate','request_csrf','request_wrong_route','request_repeat')

    def test_redirect_and_final_state_must_be_observed_after_actual_post(self):self.cases('redirect_status','redirect_foreign','after_not_applied')

    def test_binding_accessors_and_tojson_never_run_or_reach_page(self):self.cases('binding_getter','binding_tojson')
