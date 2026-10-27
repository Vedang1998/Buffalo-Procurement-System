"""Preventive artifact GET and causal transfer collection; no live browser."""
import ast
from contextlib import nullcontext
import json
import os
from pathlib import Path
from hosted_test_node import node_executable
import subprocess
from types import SimpleNamespace
import unittest
from uuid import UUID

ROOT=Path(__file__).resolve().parents[2]


class HostedDownloadTests(unittest.TestCase):
    def cases(self,*names):
        for name in names:
            with self.subTest(case=name):
                node=node_executable();self.assertIsNotNone(node)
                result=subprocess.run([node,str(ROOT/'procurement/tests/hosted_download_fixture.mjs'),name],cwd=ROOT,
                    env={'PATH':os.defpath,'LC_ALL':'C.UTF-8'},capture_output=True,text=True,timeout=5)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(json.loads(result.stdout),{'assertion':'PASS','scope':'SCRIPTED_CDP_DOWNLOAD_ONLY'})

    def test_completion_before_click_ack_is_buffered(self):self.cases('success','delayed_evaluate','late_continue_ack','late_response')
    def test_optional_download_path_is_not_file_read_authority(self):self.cases('path_ignored')
    def test_unknown_initial_total_can_become_exact_size(self):self.cases('zero_total')
    def test_artifact_get_is_denied_without_exact_allowance(self):self.cases('default_artifact')
    def test_binding_types_accessors_and_parser_bound_refuse(self):self.cases('bad_binding','binding_getter','oversize_binding')
    def test_replayed_url_and_more_than_three_downloads_refuse(self):self.cases('replay_url','max_count')
    def test_actual_anchor_inventory_page_and_target_checked_before_click(self):self.cases('wrong_page','duplicate_anchor','disconnected','blank_target','download_attr')
    def test_begin_frame_url_filename_and_event_scope_are_exact(self):self.cases('wrong_frame','wrong_url','wrong_name','browser_session')
    def test_extra_begin_and_late_download_remain_terminal(self):self.cases('extra_begin','late_extra')
    def test_unobserved_guid_and_progress_before_begin_refuse(self):self.cases('unknown_progress','wrong_guid')
    def test_canceled_and_post_completion_transition_refuse(self):self.cases('canceled','completed_then_canceled')
    def test_negative_nonfinite_fractional_and_oversized_counts_refuse(self):self.cases('negative','nonfinite','fraction','oversize')
    def test_counts_cannot_decrease_or_complete_short(self):self.cases('decrease_received','decrease_total','short_complete')
    def test_exact_single_get_and_http_response_are_required(self):self.cases('no_network','duplicate_get','http_error','old_transfer_before_get')
    def test_attachment_type_name_cache_policy_and_header_uniqueness(self):self.cases('wrong_type','wrong_filename_header','cacheable','duplicate_header')
    def test_timeout_missing_begin_abort_and_socket_close_are_bounded(self):self.cases('timeout','no_begin','abort','abort_context','socket_close')
    def test_concurrent_collect_fails_both_and_cancels_only_owned_guid(self):self.cases('concurrent')

    def test_actual_api_attachment_arguments_match_collector_contract(self):
        # The hosted protocol gate intentionally runs Python -I -S. Execute the
        # actual route's explicit headers/media_type arguments without importing
        # FastAPI; ASGI charset rendering is separately checked with locked deps.
        def Response(data,*,media_type,headers):
            return SimpleNamespace(data=data,media_type=media_type,headers=headers)
        module=ast.parse((ROOT/'procurement/src/procurement_os/api.py').read_text())
        function=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name=='monday_run_artifact')
        function.decorator_list=[]
        run='00000000-0000-4000-8000-000000000001'
        for suffix,mime in [('internal.csv','text/csv'),('review.zip','application/zip')]:
            artifact={'data':b'synthetic','filename':f'monday-{run}-42.{suffix}','content_type':mime}
            ns={'UUID':UUID,'Response':Response,'HTTPException':RuntimeError,'EmergencyPacketError':ValueError,
                '_db_conn':lambda:nullcontext(object()),'get_storage':lambda:object(),'read_monday_artifact':lambda *a,**k:artifact}
            exec(compile(ast.Module(body=[function],type_ignores=[]),'actual-artifact-endpoint','exec'),ns)
            response=ns['monday_run_artifact'](UUID(run),42)
            self.assertEqual(response.headers,{'Content-Disposition':'attachment; filename='+artifact['filename'],'Cache-Control':'no-store'})
            self.assertEqual(response.media_type,mime)
