"""On-demand research-worker lifecycle tests."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
import threading
import time
import unittest

from procurement_os.staging_research_lifecycle import (
    AggregateResourceSample,
    AttemptFailure,
    AttemptObservation,
    RecoverableAttemptError,
    ResearchLifecycleError,
    ResearchLifecycleManager,
    ResearchLifecycleRunner,
)


VALIDATION_IDENTITY = "ab" * 32


class _Clock:
    def __init__(self) -> None:
        self.value = 1_000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@dataclass
class _Driver:
    observation: AttemptObservation = field(
        default_factory=lambda: AttemptObservation(generation=0, validating=True)
    )
    launch_delay: float = 0.0
    preserve_observation_generation: bool = False

    def __post_init__(self) -> None:
        self.recoveries = 0
        self.launches: list[int] = []
        self.probes: list[int] = []
        self.commits: list[int] = []
        self.terminations: list[int] = []
        self.fail_recovery = False
        self.launch_failure: BaseException | None = None
        self.launched_generation: int | None = None
        self.fail_termination = False

    def recover_unpublished(self) -> None:
        self.recoveries += 1
        if self.fail_recovery:
            raise RuntimeError("recovery refused")

    def launch(self, generation: int) -> int:
        self.launches.append(generation)
        if self.launch_delay:
            time.sleep(self.launch_delay)
        if self.launch_failure is not None:
            raise self.launch_failure
        return generation if self.launched_generation is None else self.launched_generation

    def probe(self, generation: int) -> AttemptObservation:
        self.probes.append(generation)
        if self.preserve_observation_generation:
            return self.observation
        return replace(self.observation, generation=generation)

    def commit(self, generation: int) -> None:
        self.commits.append(generation)

    def terminate(self, generation: int) -> None:
        self.terminations.append(generation)
        if self.fail_termination:
            raise RuntimeError("cleanup refused")


class _Sampler:
    def __init__(self) -> None:
        self.sample = AggregateResourceSample(1, 1, 0, 0, 0)
        self.calls = 0

    def __call__(self) -> AggregateResourceSample:
        self.calls += 1
        return self.sample


class ResearchLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.driver = _Driver()
        self.sampler = _Sampler()
        self.manager = self._new_manager(
            driver=self.driver,
            clock=self.clock,
            sampler=self.sampler,
        )

    @staticmethod
    def _new_manager(
        *, driver: _Driver, clock: _Clock, sampler, retry_backoff_seconds: int = 7
    ) -> ResearchLifecycleManager:
        return ResearchLifecycleManager(
            driver=driver,
            monotonic=clock,
            resource_sampler=sampler,
            expected_validation_identity=VALIDATION_IDENTITY,
            retry_backoff_seconds=retry_backoff_seconds,
        )

    def _drive_until(self, predicate, *, maximum: int = 40) -> None:
        for _ in range(maximum):
            if predicate():
                return
            self.manager.tick()
        self.fail("lifecycle did not reach the expected condition")

    def _launch(self) -> None:
        self.manager.start()
        self._drive_until(lambda: bool(self.driver.launches))

    def _make_ready(self) -> None:
        self.driver.observation = AttemptObservation(
            generation=0,
            ready=True,
            validation_identity=VALIDATION_IDENTITY,
        )
        self._drive_until(lambda: self.manager.status().state == "READY")

    def test_exact_cold_start_ready_stop_path_is_nonblocking(self) -> None:
        status = self.manager.start()
        self.assertEqual((status.state, status.generation), ("VALIDATING", 0))
        self.assertEqual(status.retry_after_seconds, 7)
        self.assertEqual((self.driver.recoveries, self.driver.launches), (0, []))

        self._drive_until(lambda: bool(self.driver.launches))
        self.assertEqual((self.driver.recoveries, self.driver.launches), (1, [0]))
        self._make_ready()
        self.assertEqual(self.driver.commits, [0])

        status = self.manager.stop()
        self.assertEqual(status.state, "STOPPING")
        self.assertEqual(self.driver.terminations, [])
        self.assertEqual(self.manager.tick().state, "STOPPED")
        self.assertEqual(self.driver.terminations, [0])
        self.assertEqual(
            self.manager.transition_history,
            ("STOPPED", "VALIDATING", "READY", "STOPPING", "STOPPED"),
        )

    def test_concurrent_start_is_singleflight(self) -> None:
        barrier = threading.Barrier(8)

        def request_start() -> tuple[str, int]:
            barrier.wait()
            status = self.manager.start()
            return status.state, status.generation

        with ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(lambda _: request_start(), range(8)))

        self.assertEqual(values, [("VALIDATING", 0)] * 8)
        self.assertEqual((self.driver.recoveries, self.driver.launches), (0, []))
        self._drive_until(lambda: bool(self.driver.launches))
        self.assertEqual((self.driver.recoveries, self.driver.launches), (1, [0]))

    def test_crash_cleans_then_retries_once_and_latches_second_failure(self) -> None:
        self._launch()
        self.driver.observation = AttemptObservation(
            generation=0, failure=AttemptFailure.CRASH
        )
        self._drive_until(lambda: self.driver.terminations == [0])
        self.assertEqual(self.manager.status().state, "VALIDATING")

        self.clock.advance(7)
        self._drive_until(lambda: self.driver.launches == [0, 1])
        self.assertEqual(self.driver.recoveries, 2)

        self.driver.observation = AttemptObservation(
            generation=1, failure=AttemptFailure.CRASH
        )
        self._drive_until(lambda: self.manager.status().state == "FAILED")
        self.assertEqual(self.driver.terminations, [0, 1])
        self.clock.advance(100)
        self.manager.tick()
        self.assertEqual(self.driver.launches, [0, 1])

    def test_owner_stop_then_start_is_the_only_latched_failure_retry(self) -> None:
        self._launch()
        self.driver.observation = AttemptObservation(
            generation=0, failure=AttemptFailure.INTEGRITY
        )
        self._drive_until(lambda: self.manager.status().state == "FAILED")
        self.assertEqual(self.manager.start().state, "FAILED")
        self.assertEqual(self.manager.stop().state, "STOPPED")

        status = self.manager.start()
        self.assertEqual((status.state, status.generation), ("VALIDATING", 1))
        self._drive_until(lambda: self.driver.launches == [0, 1])

    def test_typed_launch_crash_is_cleaned_before_retry(self) -> None:
        self.driver.launch_failure = RecoverableAttemptError(AttemptFailure.CRASH)
        self.manager.start()
        self._drive_until(lambda: self.driver.terminations == [0])
        self.assertEqual(self.manager.status().state, "VALIDATING")
        self.driver.launch_failure = None
        self.clock.advance(7)
        self._drive_until(lambda: self.driver.launches == [0, 1])

    def test_cleanup_uncertainty_latches_failed_without_retry(self) -> None:
        self._launch()
        self.driver.fail_termination = True
        self.driver.observation = AttemptObservation(
            generation=0, failure=AttemptFailure.CRASH
        )
        self._drive_until(lambda: self.manager.status().state == "FAILED")
        self.driver.fail_termination = False
        self.clock.advance(100)
        self.manager.tick()
        self.assertEqual(self.driver.launches, [0])

    def test_untyped_launch_error_and_wrong_generation_never_retry(self) -> None:
        for configure in (
            lambda driver: setattr(driver, "launch_failure", RuntimeError("refused")),
            lambda driver: setattr(driver, "launched_generation", 99),
        ):
            with self.subTest(configure=configure):
                clock = _Clock()
                driver = _Driver()
                configure(driver)
                manager = self._new_manager(driver=driver, clock=clock, sampler=_Sampler())
                manager.start()
                for _ in range(4):
                    manager.tick()
                self.assertEqual(manager.status().state, "FAILED")
                self.assertEqual(driver.launches, [0])
                self.assertEqual(driver.terminations, [0])

    def test_recovery_refusal_latches_failed_without_launch(self) -> None:
        self.driver.fail_recovery = True
        self.assertEqual(self.manager.start().state, "VALIDATING")
        self.assertEqual(self.manager.tick().state, "FAILED")
        self.assertEqual(self.driver.launches, [])

    def test_resource_limits_are_strict_and_any_oom_delta_fails(self) -> None:
        cases = (
            AggregateResourceSample(5 * 1024**3, 1, 0, 0, 0),
            AggregateResourceSample(1, 6 * 1024**3, 0, 0, 0),
            AggregateResourceSample(1, 1, 1, 0, 0),
            AggregateResourceSample(1, 1, 0, 1, 0),
            AggregateResourceSample(1, 1, 0, 0, 1),
        )
        for sample in cases:
            with self.subTest(sample=sample):
                clock = _Clock()
                driver = _Driver()
                sampler = _Sampler()
                sampler.sample = sample
                manager = self._new_manager(driver=driver, clock=clock, sampler=sampler)
                manager.start()
                for _ in range(4):
                    manager.tick()
                self.assertEqual(manager.status().state, "FAILED")
                self.assertEqual(driver.terminations, [0])

    def test_final_sample_precedes_commit_and_sample_error_fails_closed(self) -> None:
        events: list[str] = []

        class OrderedDriver(_Driver):
            def commit(inner_self, generation: int) -> None:
                events.append("commit")
                super().commit(generation)

        driver = OrderedDriver(
            observation=AttemptObservation(
                generation=0,
                ready=True,
                validation_identity=VALIDATION_IDENTITY,
            )
        )

        def sample() -> AggregateResourceSample:
            events.append("sample")
            return AggregateResourceSample(1, 1, 0, 0, 0)

        manager = self._new_manager(driver=driver, clock=self.clock, sampler=sample)
        manager.start()
        for _ in range(8):
            if manager.status().state == "READY":
                break
            manager.tick()
        self.assertEqual(manager.status().state, "READY")
        self.assertEqual(events, ["sample", "sample", "commit"])

        bad_driver = _Driver()
        bad_manager = self._new_manager(
            driver=bad_driver,
            clock=self.clock,
            sampler=lambda: (_ for _ in ()).throw(RuntimeError("bad sample")),
        )
        bad_manager.start()
        for _ in range(4):
            bad_manager.tick()
        self.assertEqual(bad_manager.status().state, "FAILED")
        self.assertEqual(bad_driver.terminations, [0])

    def test_observation_and_configuration_contracts_fail_closed(self) -> None:
        invalid = (
            AttemptObservation(generation=0),
            AttemptObservation(generation=-1, validating=True),
            AttemptObservation(generation=0, validating=True, ready=True),
            AttemptObservation(generation=0, ready=True),
            AttemptObservation(
                generation=0,
                ready=True,
                failure=AttemptFailure.CRASH,
                validation_identity=VALIDATION_IDENTITY,
            ),
        )
        for observation in invalid:
            with self.subTest(observation=observation):
                with self.assertRaises(ResearchLifecycleError):
                    observation.validate()
        with self.assertRaises(ResearchLifecycleError):
            ResearchLifecycleManager(
                driver=self.driver,
                monotonic=self.clock,
                resource_sampler=self.sampler,
                expected_validation_identity=VALIDATION_IDENTITY,
                initialization_timeout_seconds=2_701,
            )

    def test_total_initialization_deadline_is_not_doubled_by_retry(self) -> None:
        self._launch()
        self.clock.advance(100)
        self.driver.observation = AttemptObservation(
            generation=0, failure=AttemptFailure.TIMEOUT
        )
        self._drive_until(lambda: self.driver.terminations == [0])
        self.clock.advance(7)
        self.driver.observation = AttemptObservation(generation=1, validating=True)
        self._drive_until(lambda: self.driver.launches == [0, 1])

        self.clock.advance(2_593)
        self._drive_until(lambda: self.manager.status().state == "FAILED")
        self.assertEqual(self.driver.launches, [0, 1])
        self.assertEqual(self.driver.terminations, [0, 1])

    def test_recovery_and_launch_cannot_cross_the_graph_deadline(self) -> None:
        clock = _Clock()

        class SlowRecovery(_Driver):
            def recover_unpublished(inner_self) -> None:
                super().recover_unpublished()
                clock.advance(2_700)

        recovery_driver = SlowRecovery()
        recovery_manager = self._new_manager(
            driver=recovery_driver, clock=clock, sampler=_Sampler()
        )
        recovery_manager.start()
        self.assertEqual(recovery_manager.tick().state, "FAILED")
        self.assertEqual(recovery_driver.launches, [])

        clock = _Clock()

        class SlowLaunch(_Driver):
            def launch(inner_self, generation: int) -> int:
                value = super().launch(generation)
                clock.advance(2_700)
                return value

        launch_driver = SlowLaunch()
        launch_manager = self._new_manager(
            driver=launch_driver, clock=clock, sampler=_Sampler()
        )
        launch_manager.start()
        launch_manager.tick()
        self.assertEqual(launch_manager.tick().state, "VALIDATING")
        self.assertEqual(launch_manager.tick().state, "FAILED")
        self.assertEqual(launch_driver.terminations, [0])

    def test_nonretryable_observations_never_auto_retry(self) -> None:
        for failure in (
            AttemptFailure.INTEGRITY,
            AttemptFailure.SEMANTIC,
            AttemptFailure.RESOURCE,
            AttemptFailure.OOM,
            AttemptFailure.ACTIVATION,
        ):
            with self.subTest(failure=failure):
                clock = _Clock()
                driver = _Driver(
                    observation=AttemptObservation(generation=0, failure=failure)
                )
                manager = self._new_manager(driver=driver, clock=clock, sampler=_Sampler())
                manager.start()
                for _ in range(6):
                    manager.tick()
                self.assertEqual(manager.status().state, "FAILED")
                clock.advance(100)
                manager.tick()
                self.assertEqual(driver.launches, [0])

    def test_concurrent_callers_cannot_turn_fast_failure_into_retry(self) -> None:
        self.driver.fail_recovery = True
        barrier = threading.Barrier(8)

        def request_start() -> str:
            barrier.wait()
            return self.manager.start().state

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(
                list(pool.map(lambda _: request_start(), range(8))),
                ["VALIDATING"] * 8,
            )
        self.assertEqual(self.manager.tick().state, "FAILED")
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(
                list(pool.map(lambda _: self.manager.start().state, range(8))),
                ["FAILED"] * 8,
            )
        self.assertEqual(self.driver.recoveries, 1)

    def test_stale_generation_ready_cannot_commit_replacement(self) -> None:
        self._launch()
        self.driver.observation = AttemptObservation(
            generation=0, failure=AttemptFailure.CRASH
        )
        self._drive_until(lambda: self.driver.terminations == [0])
        self.clock.advance(7)
        self._drive_until(lambda: self.driver.launches == [0, 1])

        self.driver.preserve_observation_generation = True
        self.driver.observation = AttemptObservation(
            generation=0,
            ready=True,
            validation_identity=VALIDATION_IDENTITY,
        )
        self._drive_until(lambda: self.manager.status().state == "FAILED")
        self.assertEqual(self.driver.commits, [])
        self.assertEqual(self.driver.terminations, [0, 1])

    def test_slow_cleanup_keeps_stopping_observable_and_status_responsive(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        class BlockingDriver(_Driver):
            def terminate(inner_self, generation: int) -> None:
                entered.set()
                release.wait(timeout=2)
                super().terminate(generation)

        driver = BlockingDriver()
        manager = self._new_manager(driver=driver, clock=self.clock, sampler=_Sampler())
        manager.start()
        manager.tick()
        manager.tick()
        self.assertEqual(manager.stop().state, "STOPPING")

        worker = threading.Thread(target=manager.tick)
        worker.start()
        self.assertTrue(entered.wait(timeout=1))
        started = time.monotonic()
        self.assertEqual(manager.status().state, "STOPPING")
        self.assertLess(time.monotonic() - started, 0.1)
        release.set()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(manager.status().state, "STOPPED")

    def test_runner_advances_without_browser_polling_and_enforces_deadline(self) -> None:
        runner = ResearchLifecycleRunner(
            self.manager, interval_seconds=0.01, fatal_handler=lambda: None
        )
        runner.start()
        try:
            self.manager.start()
            deadline = time.monotonic() + 1
            while not self.driver.launches and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(self.driver.launches, [0])

            self.driver.observation = AttemptObservation(
                generation=0,
                ready=True,
                validation_identity=VALIDATION_IDENTITY,
            )
            while self.manager.status().state != "READY" and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(self.manager.status().state, "READY")
        finally:
            runner.shutdown()

    def test_unexpected_runner_failure_cannot_leave_ready_worker_routable(self) -> None:
        self._launch()
        self._make_ready()
        original_tick = self.manager.tick
        calls = 0

        def broken_tick():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("unexpected ticker defect")
            return original_tick()

        self.manager.tick = broken_tick  # type: ignore[method-assign]
        runner = ResearchLifecycleRunner(
            self.manager, interval_seconds=0.01, fatal_handler=lambda: None
        )
        runner.start()
        deadline = time.monotonic() + 1
        while self.manager.status().state not in {"FAILED", "STOPPING"} and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertNotEqual(self.manager.status().state, "READY")
        with self.assertRaisesRegex(ResearchLifecycleError, "runner failed"):
            runner.shutdown()
        self.assertEqual(self.manager.status().state, "STOPPED")
        self.assertEqual(self.driver.terminations, [0])

    def test_orderly_runner_shutdown_stops_ready_worker_before_ticker(self) -> None:
        runner = ResearchLifecycleRunner(
            self.manager, interval_seconds=0.01, fatal_handler=lambda: None
        )
        runner.start()
        self.manager.start()
        deadline = time.monotonic() + 1
        while not self.driver.launches and time.monotonic() < deadline:
            time.sleep(0.005)
        self.driver.observation = AttemptObservation(
            generation=0,
            ready=True,
            validation_identity=VALIDATION_IDENTITY,
        )
        while self.manager.status().state != "READY" and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(self.manager.status().state, "READY")
        runner.shutdown()
        self.assertEqual(self.manager.status().state, "STOPPED")
        self.assertEqual(self.driver.terminations, [0])

    def test_shutdown_wakes_slow_ticker_before_short_join_deadline(self) -> None:
        runner = ResearchLifecycleRunner(
            self.manager,
            interval_seconds=1,
            join_timeout_seconds=0.1,
            fatal_handler=lambda: None,
        )
        self.manager.start()
        self.manager.tick()
        self.manager.tick()
        self.assertEqual(self.driver.launches, [0])
        runner.start()
        runner.shutdown()
        self.assertEqual(self.manager.status().state, "STOPPED")
        self.assertEqual(self.driver.terminations, [0])

    def test_watchdog_makes_blocked_over_deadline_action_unroutable(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        fatal = threading.Event()

        class BlockingLaunch(_Driver):
            def launch(inner_self, generation: int) -> int:
                inner_self.launches.append(generation)
                entered.set()
                release.wait(timeout=2)
                return generation

        driver = BlockingLaunch()
        manager = self._new_manager(driver=driver, clock=self.clock, sampler=_Sampler())
        runner = ResearchLifecycleRunner(
            manager,
            interval_seconds=0.01,
            join_timeout_seconds=1,
            fatal_handler=fatal.set,
        )
        runner.start()
        manager.start()
        self.assertTrue(entered.wait(timeout=1))
        self.clock.advance(2_700)
        self.assertTrue(fatal.wait(timeout=1))
        self.assertEqual(manager.status().state, "STOPPING")
        release.set()
        deadline = time.monotonic() + 1
        while manager.status().state not in {"FAILED", "STOPPED"} and time.monotonic() < deadline:
            time.sleep(0.005)
        with self.assertRaisesRegex(ResearchLifecycleError, "runner failed"):
            runner.shutdown()
        self.assertEqual(manager.status().state, "STOPPED")
        self.assertEqual(driver.terminations, [0])

    def test_watchdog_stops_ready_worker_when_resource_sample_hangs(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        fatal = threading.Event()

        class BlockingSampler(_Sampler):
            block = False

            def __call__(inner_self) -> AggregateResourceSample:
                if inner_self.block:
                    entered.set()
                    release.wait(timeout=2)
                return super().__call__()

        sampler = BlockingSampler()
        driver = _Driver(
            observation=AttemptObservation(
                generation=0,
                ready=True,
                validation_identity=VALIDATION_IDENTITY,
            )
        )
        manager = self._new_manager(driver=driver, clock=self.clock, sampler=sampler)
        manager.start()
        for _ in range(8):
            if manager.status().state == "READY":
                break
            manager.tick()
        self.assertEqual(manager.status().state, "READY")
        sampler.block = True
        runner = ResearchLifecycleRunner(
            manager,
            interval_seconds=0.01,
            join_timeout_seconds=1,
            action_timeout_seconds=0.05,
            fatal_handler=fatal.set,
        )
        runner.start()
        self.assertTrue(entered.wait(timeout=1))
        self.assertTrue(fatal.wait(timeout=1))
        self.assertEqual(manager.status().state, "STOPPING")
        release.set()
        with self.assertRaisesRegex(ResearchLifecycleError, "runner failed"):
            runner.shutdown()
        self.assertEqual(manager.status().state, "STOPPED")
        self.assertEqual(driver.terminations, [0])


if __name__ == "__main__":
    unittest.main()
