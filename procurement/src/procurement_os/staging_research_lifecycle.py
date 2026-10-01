"""Fail-closed on-demand lifecycle for the private research worker."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import threading
import time
from typing import Callable, Protocol

from .staging_supervisor import ControlStatus


INITIALIZATION_TIMEOUT_SECONDS = 2_700
DEFAULT_RETRY_BACKOFF_SECONDS = 5
RESEARCH_IDLE_TIMEOUT_SECONDS = 1_800
PROCESS_PEAK_LIMIT_BYTES = 5 * 1024**3
SERVICE_CGROUP_PEAK_LIMIT_BYTES = 6 * 1024**3


class ResearchLifecycleError(ValueError):
    """The research lifecycle or one of its observations is invalid."""


class AttemptFailure(str, Enum):
    """Bounded failure classes emitted by the worker-readiness boundary."""

    CRASH = "CRASH"
    TIMEOUT = "TIMEOUT"
    INTEGRITY = "INTEGRITY"
    SEMANTIC = "SEMANTIC"
    RESOURCE = "RESOURCE"
    OOM = "OOM"
    ACTIVATION = "ACTIVATION"


_RETRYABLE_FAILURES = frozenset({AttemptFailure.CRASH, AttemptFailure.TIMEOUT})


class RecoverableAttemptError(RuntimeError):
    """A positively classified crash/timeout with no sensitive detail."""

    def __init__(self, failure: AttemptFailure) -> None:
        if failure not in _RETRYABLE_FAILURES:
            raise ResearchLifecycleError("recoverable failure class is invalid")
        self.failure = failure
        super().__init__(failure.value)


@dataclass(frozen=True)
class AttemptObservation:
    """One generation-bound, non-sensitive worker-readiness observation."""

    generation: int
    validating: bool = False
    ready: bool = False
    failure: AttemptFailure | None = None
    validation_identity: str | None = None

    def validate(self) -> None:
        if (
            type(self.generation) is not int
            or self.generation < 0
            or type(self.validating) is not bool
            or type(self.ready) is not bool
        ):
            raise ResearchLifecycleError("research observation is invalid")
        selected = int(self.validating) + int(self.ready) + int(self.failure is not None)
        if selected != 1 or (
            self.failure is not None and not isinstance(self.failure, AttemptFailure)
        ):
            raise ResearchLifecycleError("research observation is ambiguous")
        if self.ready:
            if (
                not isinstance(self.validation_identity, str)
                or len(self.validation_identity) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in self.validation_identity
                )
            ):
                raise ResearchLifecycleError("research validation identity is invalid")
        elif self.validation_identity is not None:
            raise ResearchLifecycleError("research validation identity is unexpected")


@dataclass(frozen=True)
class AggregateResourceSample:
    """Aggregate-only resource evidence; it carries no paths or process IDs."""

    process_peak_rss_bytes: int
    service_cgroup_peak_bytes: int
    oom_delta: int
    oom_kill_delta: int
    oom_group_kill_delta: int

    def validate(self) -> None:
        values = (
            self.process_peak_rss_bytes,
            self.service_cgroup_peak_bytes,
            self.oom_delta,
            self.oom_kill_delta,
            self.oom_group_kill_delta,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise ResearchLifecycleError("research resource sample is invalid")

    def accepted(self) -> bool:
        self.validate()
        return (
            self.process_peak_rss_bytes < PROCESS_PEAK_LIMIT_BYTES
            and self.service_cgroup_peak_bytes < SERVICE_CGROUP_PEAK_LIMIT_BYTES
            and self.oom_delta == 0
            and self.oom_kill_delta == 0
            and self.oom_group_kill_delta == 0
        )


class ResearchLifecycleDriver(Protocol):
    """Privileged operations serialized behind the lifecycle state machine."""

    def recover_unpublished(self) -> None: ...

    def launch(self, minimum_generation: int) -> int:
        """Launch research and return its actual staged-key generation.

        Staging may have burned one or more keys before this call succeeds, so
        the returned generation may be newer than the supplied lower bound.
        Cleanup remains role-scoped if launch raises before returning it.
        """
        ...

    def probe(self, generation: int) -> AttemptObservation: ...

    def commit(self, generation: int) -> None: ...

    def terminate(self, generation: int | None) -> None:
        """Prove all role-owned state gone; generation is only a binding hint.

        ``None`` means launch may have created state before its actual key
        generation was returned.  Concrete drivers must then clean the owned
        research role rather than inventing or guessing a generation.
        """
        ...


class ResearchLifecycleManager:
    """Single-flight research lifecycle implementing supervisor control hooks.

    The 45-minute limit covers the complete initialization graph, including
    its one possible retry.  Only an explicitly classified crash or timeout
    may retry, and only after cleanup was acknowledged.
    """

    def __init__(
        self,
        *,
        driver: ResearchLifecycleDriver,
        monotonic: Callable[[], float] = time.monotonic,
        resource_sampler: Callable[[], AggregateResourceSample],
        expected_validation_identity: str,
        initialization_timeout_seconds: int = INITIALIZATION_TIMEOUT_SECONDS,
        retry_backoff_seconds: int = DEFAULT_RETRY_BACKOFF_SECONDS,
        idle_timeout_seconds: int = RESEARCH_IDLE_TIMEOUT_SECONDS,
    ) -> None:
        if (
            not callable(monotonic)
            or not callable(resource_sampler)
            or type(initialization_timeout_seconds) is not int
            or initialization_timeout_seconds < 1
            or initialization_timeout_seconds > INITIALIZATION_TIMEOUT_SECONDS
            or type(retry_backoff_seconds) is not int
            or retry_backoff_seconds < 1
            or retry_backoff_seconds > initialization_timeout_seconds
            or type(idle_timeout_seconds) is not int
            or idle_timeout_seconds < 1
            or idle_timeout_seconds > RESEARCH_IDLE_TIMEOUT_SECONDS
            or not isinstance(expected_validation_identity, str)
            or len(expected_validation_identity) != 64
            or any(
                character not in "0123456789abcdef"
                for character in expected_validation_identity
            )
        ):
            raise ResearchLifecycleError("research lifecycle configuration is invalid")
        self.driver = driver
        self.monotonic = monotonic
        self.resource_sampler = resource_sampler
        self.expected_validation_identity = expected_validation_identity
        self.initialization_timeout_seconds = initialization_timeout_seconds
        self.retry_backoff_seconds = retry_backoff_seconds
        self.idle_timeout_seconds = idle_timeout_seconds
        self._lock = threading.RLock()
        self._state = "STOPPED"
        self._generation = 0
        self._last_worker_generation = -1
        self._active_generation: int | None = None
        self._cleanup_required = False
        self._graph_started_at: float | None = None
        self._retry_due_at: float | None = None
        self._automatic_retries = 0
        self._history = ["STOPPED"]
        self._phase = "IDLE"
        self._operation_serial = 0
        self._operation_in_progress: int | None = None
        self._operation_started_wall: float | None = None
        self._termination_retry_allowed = False
        self._stop_requested = False
        self._last_authenticated_activity_at: float | None = None

    @property
    def transition_history(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._history)

    def start(self) -> ControlStatus:
        """Enqueue one graph, or touch the authenticated READY worker."""

        with self._lock:
            if self._state == "STOPPED":
                if self._active_generation is not None or self._cleanup_required:
                    return self._snapshot_locked()
                now = self._now()
                self._graph_started_at = now
                self._retry_due_at = None
                self._automatic_retries = 0
                self._stop_requested = False
                self._transition_locked("VALIDATING")
                self._allocate_attempt_locked()
            elif self._state == "READY":
                self._last_authenticated_activity_at = self._now()
            return self._snapshot_locked()

    def stop(self) -> ControlStatus:
        """Disable and clean the current generation before reporting STOPPED."""

        with self._lock:
            if self._state == "STOPPED":
                return self._snapshot_locked()
            self._stop_requested = True
            self._retry_due_at = None
            self._transition_locked("STOPPING")
            if self._operation_in_progress is None:
                if not self._cleanup_required:
                    self._complete_stop_locked()
                else:
                    self._phase = "TERMINATE"
            return self._snapshot_locked()

    def status(self) -> ControlStatus:
        with self._lock:
            return self._snapshot_locked()

    def tick(self) -> ControlStatus:
        """Advance deadlines, readiness, resource gates, and a due retry."""

        action: tuple[int, str, int | None] | None
        with self._lock:
            action = self._plan_action_locked()
            if action is None:
                return self._snapshot_locked()
        serial, kind, generation = action
        outcome: object = None
        error: BaseException | None = None
        try:
            if kind == "RECOVER":
                self.driver.recover_unpublished()
            elif kind == "LAUNCH":
                assert generation is not None
                outcome = self.driver.launch(generation)
            elif kind in {"SAMPLE", "FINAL_SAMPLE", "READY_SAMPLE"}:
                outcome = self.resource_sampler()
            elif kind == "PROBE":
                assert generation is not None
                outcome = self.driver.probe(generation)
            elif kind == "COMMIT":
                assert generation is not None
                self.driver.commit(generation)
            elif kind == "TERMINATE":
                self.driver.terminate(generation)
            else:
                raise ResearchLifecycleError("research lifecycle action is invalid")
        except BaseException as exc:
            error = exc
        with self._lock:
            self._apply_action_locked(
                serial=serial,
                kind=kind,
                generation=generation,
                outcome=outcome,
                error=error,
            )
            return self._snapshot_locked()

    def _plan_action_locked(self) -> tuple[int, str, int | None] | None:
        now = self._now()
        if self._operation_in_progress is not None:
            return None
        if self._state == "STOPPING":
            if not self._cleanup_required:
                self._complete_stop_locked()
                return None
            self._phase = "TERMINATE"
        elif self._state == "VALIDATING":
            if self._deadline_reached_locked(now):
                if not self._cleanup_required:
                    self._latch_failed_locked()
                    return None
                self._termination_retry_allowed = False
                self._phase = "TERMINATE"
            elif self._phase == "WAIT_RETRY":
                if self._retry_due_at is None or now < self._retry_due_at:
                    return None
                self._retry_due_at = None
                self._allocate_attempt_locked()
        elif self._state == "READY":
            if (
                self._last_authenticated_activity_at is None
                or now - self._last_authenticated_activity_at
                >= self.idle_timeout_seconds
            ):
                self._stop_requested = True
                self._transition_locked("STOPPING")
                self._phase = "TERMINATE"
            else:
                self._phase = "READY_SAMPLE"
        else:
            return None
        generation = (
            self._generation
            if self._phase == "LAUNCH"
            else self._active_generation
        )
        if (
            self._phase
            in {"SAMPLE", "PROBE", "FINAL_SAMPLE", "COMMIT", "READY_SAMPLE"}
            and generation is None
        ):
            self._latch_failed_locked()
            return None
        if self._phase == "TERMINATE" and not self._cleanup_required:
            self._latch_failed_locked()
            return None
        self._operation_serial += 1
        serial = self._operation_serial
        self._operation_in_progress = serial
        self._operation_started_wall = time.monotonic()
        if self._phase == "LAUNCH":
            # Once launch begins it may create a child/key before returning its
            # actual generation.  From this point cleanup is role-scoped and
            # mandatory even when launch raises or returns malformed data.
            self._cleanup_required = True
        return serial, self._phase, generation

    def _apply_action_locked(
        self,
        *,
        serial: int,
        kind: str,
        generation: int | None,
        outcome: object,
        error: BaseException | None,
    ) -> None:
        if self._operation_in_progress != serial:
            raise ResearchLifecycleError("research lifecycle operation binding differs")
        self._operation_in_progress = None
        self._operation_started_wall = None
        now = self._now()
        if self._state == "STOPPING":
            if kind == "TERMINATE":
                if error is not None:
                    self._latch_failed_locked()
                else:
                    self._active_generation = None
                    self._cleanup_required = False
                    if self._stop_requested:
                        self._complete_stop_locked()
                    else:
                        self._latch_failed_locked()
            elif not self._cleanup_required:
                if self._stop_requested:
                    self._complete_stop_locked()
                else:
                    self._latch_failed_locked()
            else:
                if kind == "LAUNCH":
                    self._adopt_launch_generation_locked(
                        minimum_generation=generation,
                        outcome=outcome,
                        error=error,
                    )
                self._phase = "TERMINATE"
            return
        if self._state not in {"VALIDATING", "READY"}:
            return
        if kind == "TERMINATE":
            if error is not None:
                self._latch_failed_locked()
                return
            self._active_generation = None
            self._cleanup_required = False
            can_retry = (
                self._termination_retry_allowed
                and self._automatic_retries == 0
                and self._graph_started_at is not None
                and now + self.retry_backoff_seconds
                < self._graph_started_at + self.initialization_timeout_seconds
            )
            self._termination_retry_allowed = False
            if can_retry:
                self._automatic_retries = 1
                self._retry_due_at = now + self.retry_backoff_seconds
                self._phase = "WAIT_RETRY"
            else:
                self._latch_failed_locked()
            return
        if self._state == "VALIDATING" and self._deadline_reached_locked(now):
            if not self._cleanup_required:
                self._latch_failed_locked()
            else:
                self._termination_retry_allowed = False
                self._phase = "TERMINATE"
            return
        if kind == "RECOVER":
            if error is not None:
                self._latch_failed_locked()
            else:
                self._phase = "LAUNCH"
            return
        if kind == "LAUNCH":
            if self._adopt_launch_generation_locked(
                minimum_generation=generation,
                outcome=outcome,
                error=error,
            ):
                self._phase = "SAMPLE"
                return
            retry_allowed = isinstance(error, RecoverableAttemptError)
            self._termination_retry_allowed = retry_allowed
            self._phase = "TERMINATE"
            return
        if kind in {"SAMPLE", "FINAL_SAMPLE", "READY_SAMPLE"}:
            accepted = False
            if error is None and type(outcome) is AggregateResourceSample:
                try:
                    accepted = outcome.accepted()
                except BaseException:
                    accepted = False
            if not accepted:
                self._termination_retry_allowed = False
                if kind == "READY_SAMPLE":
                    self._transition_locked("STOPPING")
                self._phase = "TERMINATE"
                return
            self._phase = {
                "SAMPLE": "PROBE",
                "FINAL_SAMPLE": "COMMIT",
                "READY_SAMPLE": "IDLE",
            }[kind]
            return
        if kind == "PROBE":
            if error is not None or not isinstance(outcome, AttemptObservation):
                self._termination_retry_allowed = False
                self._phase = "TERMINATE"
                return
            try:
                outcome.validate()
            except ResearchLifecycleError:
                self._termination_retry_allowed = False
                self._phase = "TERMINATE"
                return
            if outcome.generation != generation:
                self._termination_retry_allowed = False
                self._phase = "TERMINATE"
            elif outcome.validating:
                self._phase = "SAMPLE"
            elif outcome.failure is not None:
                self._termination_retry_allowed = outcome.failure in _RETRYABLE_FAILURES
                self._phase = "TERMINATE"
            elif outcome.validation_identity != self.expected_validation_identity:
                self._termination_retry_allowed = False
                self._phase = "TERMINATE"
            else:
                self._phase = "FINAL_SAMPLE"
            return
        if kind == "COMMIT":
            if error is not None:
                self._termination_retry_allowed = False
                self._phase = "TERMINATE"
            else:
                self._phase = "IDLE"
                self._transition_locked("READY")
                self._last_authenticated_activity_at = now
            return
        raise ResearchLifecycleError("research lifecycle action is invalid")

    def _allocate_attempt_locked(self) -> None:
        if self._active_generation is not None or self._cleanup_required:
            self._latch_failed_locked()
            return
        self._generation = max(
            0,
            self._last_worker_generation + 1,
        )
        self._phase = "RECOVER"

    def _adopt_launch_generation_locked(
        self,
        *,
        minimum_generation: int | None,
        outcome: object,
        error: BaseException | None,
    ) -> bool:
        if (
            error is not None
            or minimum_generation is None
            or type(outcome) is not int
            or not minimum_generation <= outcome <= (2**63 - 1)
            or outcome <= self._last_worker_generation
        ):
            self._active_generation = None
            return False
        self._active_generation = outcome
        self._generation = outcome
        self._last_worker_generation = outcome
        return True

    def _latch_failed_locked(self) -> None:
        self._retry_due_at = None
        self._phase = "IDLE"
        self._transition_locked("FAILED")

    def _complete_stop_locked(self) -> None:
        self._active_generation = None
        self._cleanup_required = False
        self._graph_started_at = None
        self._retry_due_at = None
        self._automatic_retries = 0
        self._termination_retry_allowed = False
        self._stop_requested = False
        self._last_authenticated_activity_at = None
        self._phase = "IDLE"
        self._transition_locked("STOPPED")

    def _deadline_reached_locked(self, now: float) -> bool:
        return (
            self._graph_started_at is None
            or now - self._graph_started_at >= self.initialization_timeout_seconds
        )

    def _transition_locked(self, state: str) -> None:
        if state not in {"STOPPED", "VALIDATING", "READY", "STOPPING", "FAILED"}:
            raise ResearchLifecycleError("research lifecycle state is invalid")
        if state != self._state:
            self._state = state
            self._history.append(state)

    def _snapshot_locked(self) -> ControlStatus:
        retry_after: int | None = None
        if self._state == "VALIDATING":
            now = self._now()
            if self._retry_due_at is not None:
                retry_after = max(1, math.ceil(self._retry_due_at - now))
            else:
                retry_after = min(
                    self.retry_backoff_seconds,
                    self.initialization_timeout_seconds,
                )
        status = ControlStatus(
            state=self._state,
            generation=self._generation,
            retry_after_seconds=retry_after,
        )
        status.validate()
        return status

    def _now(self) -> float:
        try:
            now = float(self.monotonic())
        except (TypeError, ValueError, OverflowError) as exc:
            raise ResearchLifecycleError("research lifecycle clock is invalid") from exc
        if not math.isfinite(now) or now < 0:
            raise ResearchLifecycleError("research lifecycle clock is invalid")
        return now

    def _runner_failed(self) -> None:
        """Make an unexpected ticker failure immediately unroutable."""

        with self._lock:
            self._stop_requested = False
            self._retry_due_at = None
            if not self._cleanup_required:
                self._latch_failed_locked()
            else:
                self._transition_locked("STOPPING")
                self._phase = "TERMINATE"

    def _watchdog_expire_if_due(self, *, action_timeout_seconds: float) -> bool:
        """Make an over-deadline graph unroutable without waiting on its action."""

        with self._lock:
            action_overdue = (
                self._operation_in_progress is not None
                and self._operation_started_wall is not None
                and time.monotonic() - self._operation_started_wall
                >= action_timeout_seconds
            )
            graph_overdue = (
                self._state == "VALIDATING"
                and self._operation_in_progress is not None
                and self._deadline_reached_locked(self._now())
            )
            if (
                self._state not in {"VALIDATING", "READY"}
                or not (action_overdue or graph_overdue)
            ):
                return False
            self._stop_requested = False
            self._retry_due_at = None
            self._termination_retry_allowed = False
            self._transition_locked("STOPPING")
            if self._operation_in_progress is None:
                if not self._cleanup_required:
                    self._latch_failed_locked()
                else:
                    self._phase = "TERMINATE"
            return True


class ResearchLifecycleRunner:
    """Independent single-threaded ticker for one lifecycle manager."""

    def __init__(
        self,
        manager: ResearchLifecycleManager,
        *,
        interval_seconds: float = 0.25,
        join_timeout_seconds: float = 5.0,
        action_timeout_seconds: float = 5.0,
        fatal_handler: Callable[[], None],
    ) -> None:
        if (
            not isinstance(manager, ResearchLifecycleManager)
            or not isinstance(interval_seconds, (int, float))
            or not 0.01 <= float(interval_seconds) <= 5.0
            or not isinstance(join_timeout_seconds, (int, float))
            or not 0.1 <= float(join_timeout_seconds) <= 30.0
            or not isinstance(action_timeout_seconds, (int, float))
            or not 0.05 <= float(action_timeout_seconds) <= 30.0
            or not callable(fatal_handler)
        ):
            raise ResearchLifecycleError("research lifecycle runner is invalid")
        self.manager = manager
        self.interval_seconds = float(interval_seconds)
        self.join_timeout_seconds = float(join_timeout_seconds)
        self.action_timeout_seconds = float(action_timeout_seconds)
        self.fatal_handler = fatal_handler
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._watchdog_thread: threading.Thread | None = None
        self._failure: BaseException | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                raise ResearchLifecycleError("research lifecycle runner already started")
            thread = threading.Thread(
                target=self._run,
                name="buffalo-research-lifecycle",
                daemon=False,
            )
            watchdog = threading.Thread(
                target=self._watchdog,
                name="buffalo-research-watchdog",
                daemon=False,
            )
            self._thread = thread
            self._watchdog_thread = watchdog
            thread.start()
            watchdog.start()

    def check(self) -> None:
        with self._lock:
            failure = self._failure
        if failure is not None:
            raise ResearchLifecycleError("research lifecycle runner failed") from failure

    def shutdown(self) -> None:
        with self._lock:
            thread = self._thread
            watchdog = self._watchdog_thread
        if thread is None or watchdog is None:
            raise ResearchLifecycleError("research lifecycle runner is not started")
        if threading.current_thread() in {thread, watchdog}:
            raise ResearchLifecycleError("research lifecycle runner cannot join itself")
        self.manager.stop()
        deadline = time.monotonic() + self.join_timeout_seconds
        while self.manager.status().state != "STOPPED" and time.monotonic() < deadline:
            self._wake.set()
            if not thread.is_alive():
                try:
                    self.manager.tick()
                except BaseException:
                    break
            else:
                time.sleep(min(self.interval_seconds, 0.05))
        self._stop.set()
        self._wake.set()
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
        watchdog.join(timeout=max(0.0, deadline - time.monotonic()))
        if thread.is_alive() or watchdog.is_alive():
            raise ResearchLifecycleError("research lifecycle runner did not stop")
        if self.manager.status().state != "STOPPED":
            raise ResearchLifecycleError("research lifecycle worker did not stop")
        self.check()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                self.manager.tick()
                self._wake.wait(self.interval_seconds)
                self._wake.clear()
        except BaseException as exc:
            self.manager._runner_failed()
            try:
                self.manager.tick()
            except BaseException:
                pass
            with self._lock:
                self._failure = exc
            self._stop.set()
            self._wake.set()

    def _watchdog(self) -> None:
        try:
            while not self._stop.wait(min(self.interval_seconds, 0.05)):
                if not self.manager._watchdog_expire_if_due(
                    action_timeout_seconds=self.action_timeout_seconds
                ):
                    continue
                error = ResearchLifecycleError(
                    "research lifecycle initialization deadline expired"
                )
                try:
                    self.fatal_handler()
                except BaseException as exc:
                    error = ResearchLifecycleError(
                        "research lifecycle fatal handler failed"
                    )
                    error.__cause__ = exc
                with self._lock:
                    if self._failure is None:
                        self._failure = error
                self._wake.set()
                return
        except BaseException as exc:
            self.manager._runner_failed()
            with self._lock:
                if self._failure is None:
                    self._failure = exc
            self._stop.set()
            self._wake.set()
