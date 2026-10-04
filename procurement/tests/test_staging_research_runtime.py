"""Tests for the concrete research lifecycle driver and resource sampler."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from procurement_os import staging_research_runtime as target
from procurement_os.staging_research_lifecycle import (
    AggregateResourceSample,
    AttemptFailure,
    RecoverableAttemptError,
)
from procurement_os.staging_supervisor import (
    ProcessIdentity,
    ResearchChildCrashed,
    StagingChildSupervisor,
)


def _supervisor() -> StagingChildSupervisor:
    value = object.__new__(StagingChildSupervisor)
    value.research_validation_identity = target.RESEARCH_VALIDATION_IDENTITY
    value.worker_templates = {
        "research": SimpleNamespace(environment=tuple())
    }
    return value


class StagingResearchRuntimeTests(unittest.TestCase):
    def _driver(self, supervisor, release: Path, sampler=lambda: AggregateResourceSample(1, 1, 0, 0, 0)):
        return target.SupervisorResearchLifecycleDriver(
            supervisor=supervisor,
            release_path=release,
            validation_parent=release.parent,
            destination_uid=1001,
            destination_gid=1002,
            supervisor_uid=1000,
            supervisor_gid=1000,
            failure_sampler=sampler,
        )

    def test_preflight_is_single_use_and_binds_the_same_release(self) -> None:
        supervisor = _supervisor()
        launched: list[int] = []
        supervisor.start_research = lambda generation: (
            launched.append(generation) or SimpleNamespace(generation=generation)
        )
        with tempfile.TemporaryDirectory(prefix="runtime-driver-") as raw:
            release = Path(raw).resolve() / "release"
            driver = self._driver(supervisor, release)
            with mock.patch.object(
                target,
                "validate_staging_research_paths",
                return_value=SimpleNamespace(release=release),
            ), mock.patch.object(target, "validate_accepted_research_release") as validate:
                driver.recover_unpublished()
                self.assertEqual(driver.launch(4), 4)
                validate.assert_called_once()
                with self.assertRaisesRegex(
                    target.StagingResearchRuntimeError, "preflight is absent"
                ):
                    driver.launch(5)
                validate.side_effect = target.ResearchReleaseError(
                    "injected release refusal"
                )
                with self.assertRaisesRegex(
                    target.StagingResearchRuntimeError,
                    "release validation failed",
                ):
                    driver.recover_unpublished()
        self.assertEqual(launched, [4])

    def test_production_factory_wires_one_manager_runner_driver_and_sampler(self) -> None:
        supervisor = _supervisor()
        sampler = mock.Mock(return_value=AggregateResourceSample(1, 1, 0, 0, 0))
        driver = mock.Mock()
        fatal = mock.Mock()
        with mock.patch.object(
            target, "ServiceResourceSampler", return_value=sampler
        ) as sampler_factory, mock.patch.object(
            target, "SupervisorResearchLifecycleDriver", return_value=driver
        ) as driver_factory:
            runtime = target.build_supervised_research_runtime(
                supervisor=supervisor,
                release_path="/release",
                validation_parent="/validation",
                destination_uid=1001,
                destination_gid=1002,
                supervisor_uid=1000,
                supervisor_gid=1000,
                fatal_handler=fatal,
                cgroup_path="/sys/fs/cgroup/service",
            )
        sampler_factory.assert_called_once_with(
            supervisor=supervisor,
            cgroup_path="/sys/fs/cgroup/service",
        )
        self.assertIs(runtime.manager.driver, driver)
        self.assertIs(runtime.manager.resource_sampler, sampler)
        self.assertIs(runtime.runner.manager, runtime.manager)
        self.assertEqual(runtime.runner.action_timeouts["RECOVER"], 2_700.0)
        self.assertEqual(runtime.runner.action_timeouts["TERMINATE"], 35.0)
        self.assertEqual(runtime.runner.join_timeout_seconds, 45.0)
        driver_factory.assert_called_once()

    def test_probe_and_commit_classify_only_proven_crashes_as_retryable(self) -> None:
        supervisor = _supervisor()
        sample = AggregateResourceSample(1, 1, 0, 0, 0)
        driver = self._driver(supervisor, Path("/tmp/release"), sampler=lambda: sample)

        def crash(_generation):
            raise ResearchChildCrashed("gone")

        supervisor.probe_research = crash
        self.assertEqual(driver.probe(8).failure, AttemptFailure.CRASH)
        sample = AggregateResourceSample(1, 1, 0, 1, 0)
        self.assertEqual(driver.probe(8).failure, AttemptFailure.OOM)

        sample = AggregateResourceSample(1, 1, 0, 0, 0)
        supervisor.commit_research = crash
        with self.assertRaises(RecoverableAttemptError) as raised:
            driver.commit(8)
        self.assertEqual(raised.exception.failure, AttemptFailure.CRASH)
        sample = AggregateResourceSample(5 * 1024**3, 1, 0, 0, 0)
        with self.assertRaises(target.StagingResearchRuntimeError):
            driver.commit(8)

    def _resource_tree(
        self,
        root: Path,
        *,
        hard_max: int = 5 * 1024**3 - 1,
        relative: str = "service",
    ):
        proc = root / "proc"
        cgroup = root / "cgroup"
        service = cgroup if not relative else cgroup / relative
        (proc / "self").mkdir(parents=True)
        membership = f"0::/{relative}\n" if relative else "0::/\n"
        (proc / "self" / "cgroup").write_text(membership, encoding="ascii")
        pid_root = proc / str(os.getpid())
        pid_root.mkdir()
        (pid_root / "cgroup").write_text(membership, encoding="ascii")
        (pid_root / "status").write_text(
            "State:\tR (running)\nVmRSS:\t10 kB\nVmHWM:\t20 kB\n",
            encoding="ascii",
        )
        service.mkdir(parents=True)
        (service / "memory.max").write_text(f"{hard_max}\n", encoding="ascii")
        (service / "memory.peak").write_text("4096\n", encoding="ascii")
        (service / "memory.events").write_text(
            "oom 0\noom_kill 0\noom_group_kill 0\n", encoding="ascii"
        )
        return proc, cgroup, service

    def test_sampler_binds_the_current_cgroup_and_strict_hard_limit(self) -> None:
        supervisor = _supervisor()
        supervisor.owned_process_identities = lambda: ()
        with tempfile.TemporaryDirectory(prefix="runtime-resources-") as raw:
            proc, cgroup, service = self._resource_tree(Path(raw))
            sampler = target.ServiceResourceSampler(
                supervisor=supervisor,
                cgroup_path=service,
                _proc_root=proc,
                _cgroup_root=cgroup,
            )
            sample = sampler()
            self.assertEqual(sample.process_peak_rss_bytes, 20 * 1024)
            self.assertEqual(sample.service_cgroup_peak_bytes, 4096)
            self.assertTrue(sample.accepted())
            with self.assertRaisesRegex(
                target.StagingResearchRuntimeError, "binding differs"
            ):
                target.ServiceResourceSampler(
                    supervisor=supervisor,
                    cgroup_path=cgroup,
                    _proc_root=proc,
                    _cgroup_root=cgroup,
                )

        with tempfile.TemporaryDirectory(prefix="runtime-root-cgroup-") as raw:
            proc, cgroup, service = self._resource_tree(
                Path(raw), relative=""
            )
            sampler = target.ServiceResourceSampler(
                supervisor=supervisor,
                cgroup_path=service,
                _proc_root=proc,
                _cgroup_root=cgroup,
            )
            self.assertTrue(sampler().accepted())

        with tempfile.TemporaryDirectory(prefix="runtime-limit-") as raw:
            proc, cgroup, service = self._resource_tree(
                Path(raw), hard_max=5 * 1024**3
            )
            with self.assertRaisesRegex(
                target.StagingResearchRuntimeError, "hard limit"
            ):
                target.ServiceResourceSampler(
                    supervisor=supervisor,
                    cgroup_path=service,
                    _proc_root=proc,
                    _cgroup_root=cgroup,
                )

    def test_exact_zombie_does_not_hide_oom_or_become_resource_drift(self) -> None:
        supervisor = _supervisor()
        zombie = ProcessIdentity(
            pid=99123,
            start_ticks=44,
            process_group=99123,
            session_id=99123,
        )
        supervisor.owned_process_identities = lambda: (zombie,)
        with tempfile.TemporaryDirectory(prefix="runtime-zombie-") as raw:
            proc, cgroup, service = self._resource_tree(Path(raw))
            child = proc / str(zombie.pid)
            child.mkdir()
            (child / "cgroup").write_text("0::/service\n", encoding="ascii")
            (child / "status").write_text(
                "State:\tZ (zombie)\n", encoding="ascii"
            )
            sampler = target.ServiceResourceSampler(
                supervisor=supervisor,
                cgroup_path=service,
                _proc_root=proc,
                _cgroup_root=cgroup,
            )
            self.assertTrue(sampler().accepted())
            (service / "memory.events").write_text(
                "oom 1\noom_kill 1\noom_group_kill 0\n", encoding="ascii"
            )
            observed = sampler()
            self.assertEqual((observed.oom_delta, observed.oom_kill_delta), (1, 1))
            self.assertFalse(observed.accepted())


if __name__ == "__main__":
    unittest.main()
