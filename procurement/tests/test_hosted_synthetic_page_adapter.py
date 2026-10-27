"""Causal CDP event protocol, not Chromium/browser or containment evidence."""
import json
import os
from pathlib import Path
from hosted_test_node import node_executable
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[2]


class HostedPageAdapterTests(unittest.TestCase):
    def cases(self,*names):
        for name in names:
            with self.subTest(case=name):
                node=node_executable();self.assertIsNotNone(node)
                result=subprocess.run([node,str(ROOT/'procurement/tests/hosted_page_adapter_fixture.mjs'),name],cwd=ROOT,
                    env={'PATH':os.defpath,'LC_ALL':'C.UTF-8'},capture_output=True,text=True,timeout=5)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(json.loads(result.stdout),{'assertion':'PASS','scope':'SCRIPTED_CDP_PROTOCOL_ONLY'})

    def test_one_actual_post_preview_and_bound_303_redirect(self):self.cases('success','redirect','fetch_first','delayed_ack')
    def test_exact_destroyed_context_requires_observed_post_and_navigation(self):self.cases('destroyed','wrong_context','no_request')
    def test_default_and_closed_allowances_deny_mutation(self):self.cases('default_deny','after_guard_close')
    def test_denial_latches_before_delayed_ack_and_second_post(self):self.cases('denial_race')
    def test_throwing_or_asynchronous_guard_is_denied(self):self.cases('throw_guard','promise_guard')
    def test_duplicate_post_is_terminal_even_same_turn(self):self.cases('duplicate_post')
    def test_request_route_session_frame_and_body_are_bound(self):self.cases('wrong_route','wrong_session','wrong_request_frame','wrong_body')
    def test_collision_denies_exact_paused_request(self):self.cases('network_collision')
    def test_ambiguous_failed_or_timed_out_continuation_is_not_pass(self):self.cases('continue_error','continue_hangs')
    def test_redirect_get_must_be_preventively_continued(self):self.cases('redirect_get_error')
    def test_response_loader_frame_session_and_load_are_bound(self):self.cases('wrong_loader','wrong_response_session','wrong_response_frame','no_load')
    def test_navigation_requires_successful_http_and_no_protocol_error(self):self.cases('navigate','navigate_http','navigate_error')
    def test_prior_loader_load_event_does_not_satisfy_new_navigation(self):self.cases('stale_load')
    def test_delayed_pre_mark_response_does_not_satisfy_lookup(self):self.cases('stale_response','stale_request')
    def test_concurrent_operation_cancels_owned_pending_work(self):self.cases('concurrent')
    def test_abort_socket_close_timeout_close_pending_commands(self):self.cases('abort','socket_close','command_timeout')
    def test_evaluation_exceptions_and_size_refuse(self):self.cases('bad_value','large_value')
    def test_transport_validates_response_session(self):self.cases('transport_session','transport_no_session')
    def test_transport_cancellation_malformed_message_socket_error(self):self.cases('transport_cancel','transport_malformed','transport_socket_error')
    def test_dispatcher_owns_socket_and_bounded_port(self):self.cases('double_transport','bad_port')
    def test_exact_owned_target_and_root_frame_before_start(self):self.cases('wrong_target','wrong_frame')
    def test_session_reports_its_own_target_not_unrelated_browser_lookup(self):self.cases('target_binding')
    def test_json_read_executes_bounded_stream_and_preserves_dom(self):self.cases('read_success')
    def test_fabricated_or_multiple_json_reads_refuse(self):self.cases('read_fabricated','read_duplicate')
    def test_json_reader_rejects_status_redirect_type_and_navigation(self):self.cases('read_status','read_redirect','read_type','read_navigated')
    def test_json_reader_closes_stream_on_oversize_invalid_utf8_json(self):self.cases('read_size','read_utf8','read_json')
    def test_terminal_adapter_keeps_deny_listener_until_target_absence(self):self.cases('close','target_alive')
    def test_auth_challenge_and_target_detach_refuse(self):self.cases('auth_required','detached')
