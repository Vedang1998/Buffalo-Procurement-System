"""Driver refusal/accounting tests; real PID1 execution is a separate run."""
import contextlib
import io
import json
import unittest
from unittest.mock import Mock,patch

import hosted_synthetic_pid1_regression as live


class HostedPid1DriverTests(unittest.TestCase):
    def test_ordinary_or_host_root_invocation_is_inert(self):
        with patch.object(live.subprocess,'run') as run:
            for args in ([],['--execute'],['--run-pid1-regressions','extra']):
                self.assertEqual(live.main(args),78)
            with patch.object(live.os,'geteuid',return_value=0):
                self.assertEqual(live.main(['--run-pid1-regressions']),78)
        run.assert_not_called()

    def test_unavailable_namespace_is_not_run_never_a_passing_test(self):
        with patch.object(live.os,'geteuid',return_value=1001), \
             patch.object(live.subprocess,'run',return_value=Mock(returncode=1)) as run, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(live.main(['--run-pid1-regressions']),78)
        self.assertEqual(run.call_count,1)
        self.assertEqual(json.loads(out.getvalue())['real_pid1_regression'],'NOT_RUN')

    def test_child_assertion_failure_is_fail_not_expected_refusal(self):
        with patch.object(live.os,'geteuid',return_value=1001), \
             patch.object(live.subprocess,'run',side_effect=[Mock(returncode=0),Mock(returncode=1)]) as run, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(live.main(['--run-pid1-regressions']),1)
        result=json.loads(out.getvalue())
        self.assertEqual(result['real_pid1_regression'],'FAIL')
        self.assertEqual(result['completed_cases'],[])
        self.assertEqual(run.call_count,2)

    def test_unknown_case_refuses_before_namespace_or_fork(self):
        with patch.object(live.subprocess,'Popen') as fork,self.assertRaises(AssertionError):
            live._case('not-a-reviewed-case')
        fork.assert_not_called()


if __name__=='__main__':unittest.main()
