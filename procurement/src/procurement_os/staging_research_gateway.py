"""Gateway-side single-flight admission for the on-demand research worker."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
import time
from typing import Awaitable, Callable, Protocol

from .staging_supervisor import ControlStatus
from .staging_worker_types import WorkerKeyState, WorkerKeyring


class ResearchGatewayError(ValueError):
    """Research lifecycle admission cannot be proven."""


class ResearchControlClient(Protocol):
    async def request(self, operation: str) -> ControlStatus: ...


@dataclass(frozen=True)
class ResearchDecision:
    state: str
    generation: int
    retry_after_seconds: int | None
    routable: bool

    def validate(self) -> None:
        status = ControlStatus(
            state=self.state,
            generation=self.generation,
            retry_after_seconds=self.retry_after_seconds,
        )
        status.validate()
        if type(self.routable) is not bool or self.routable != (
            self.state == "READY"
        ):
            raise ResearchGatewayError("research decision is invalid")


class ResearchLease:
    """Generation-bound lease spanning assertion minting and response close."""

    def __init__(
        self,
        *,
        coordinator: "ResearchGatewayCoordinator",
        decision: ResearchDecision,
        acquired: bool,
    ) -> None:
        self.coordinator = coordinator
        self.decision = decision
        self._acquired = acquired
        self._entered = False

    async def __aenter__(self) -> ResearchDecision:
        if self._entered:
            raise ResearchGatewayError("research lease was reused")
        self._entered = True
        return self.decision

    def __aexit__(self, _type, _value, _traceback) -> Awaitable[None]:
        if self._acquired:
            # Accounting is completed synchronously before this coroutine can
            # observe cancellation.  The coalesced final touch is supervisor-
            # owned and remains tracked independently of the browser task.
            self.coordinator._release_now(self.decision.generation)
            self._acquired = False
        return self._completed_exit()

    @staticmethod
    async def _completed_exit() -> None:
        return None


class ResearchGatewayCoordinator:
    """Coalesce lifecycle control and guard ACTIVE-generation routing."""

    def __init__(
        self,
        *,
        control: ResearchControlClient,
        keyring: WorkerKeyring,
        monotonic=time.monotonic,
        cache_seconds: float = 1.0,
        heartbeat_seconds: float = 60.0,
    ) -> None:
        if (
            not callable(getattr(control, "request", None))
            or not isinstance(keyring, WorkerKeyring)
            or not callable(monotonic)
            or not isinstance(cache_seconds, (int, float))
            or not 0.1 <= float(cache_seconds) <= 2.0
            or not isinstance(heartbeat_seconds, (int, float))
            or not 1 <= float(heartbeat_seconds) <= 300
        ):
            raise ResearchGatewayError("research coordinator configuration is invalid")
        self.control = control
        self.keyring = keyring
        self.monotonic = monotonic
        self.cache_seconds = float(cache_seconds)
        self.heartbeat_seconds = float(heartbeat_seconds)
        self._lock = asyncio.Lock()
        self._control_lock = asyncio.Lock()
        self._decision_task: asyncio.Task[ResearchDecision] | None = None
        self._retry_task: asyncio.Task[ResearchDecision] | None = None
        self._touch_task: asyncio.Task[None] | None = None
        self._pending_touch_generation: int | None = None
        self._cached: ResearchDecision | None = None
        self._cached_until = 0.0
        self._leases: dict[int, int] = {}
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._closed = False
        self._last_generation = 0
        self._last_control_call_at = float("-inf")
        self._last_activity_touch_at = float("-inf")
        self._last_release_at = float("-inf")
        self._minimum_control_interval_seconds = 0.15

    async def startup(self) -> None:
        async with self._lock:
            if self._closed or self._heartbeat_task is not None:
                raise ResearchGatewayError("research coordinator startup differs")
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(),
                name="buffalo-research-lease-heartbeat",
            )

    async def shutdown(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            heartbeat = self._heartbeat_task
            decision = self._decision_task
            retry = self._retry_task
            touch = self._touch_task
            self._heartbeat_task = None
            self._decision_task = None
            self._retry_task = None
            self._touch_task = None
            self._pending_touch_generation = None
            self._cached = None
            # In-flight response contexts still own their generation counts.
            # Closed coordinators suppress touches, but each later __aexit__
            # must be able to discharge its lease without replacing request
            # cancellation with an accounting error.
        caller_cancelled = False
        for task in (heartbeat, decision, retry, touch):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    caller_cancelled = True
            except Exception:
                # Teardown must retrieve and suppress any already-completed
                # background failure.  No private control endpoint or chained
                # transport detail is allowed to escape shutdown.
                pass
        if caller_cancelled:
            raise asyncio.CancelledError

    async def authorized_request(self) -> ResearchLease:
        decision = await self._shared_decision(force=False)
        acquired = False
        if decision.routable and self._active_key_matches(decision.generation):
            if not self._closed and self._active_key_matches(decision.generation):
                self._leases[decision.generation] = (
                    self._leases.get(decision.generation, 0) + 1
                )
                acquired = True
        if not acquired and decision.routable:
            decision = ResearchDecision(
                state="STOPPING",
                generation=decision.generation,
                retry_after_seconds=1,
                routable=False,
            )
        return ResearchLease(
            coordinator=self,
            decision=decision,
            acquired=acquired,
        )

    async def retry_failed(self) -> ResearchDecision:
        async with self._lock:
            if self._closed:
                return self._unavailable()
            task = self._retry_task
            if task is None or task.done():
                task = asyncio.create_task(self._retry_failed_once())
                self._retry_task = task
        return await asyncio.shield(task)

    async def _shared_decision(self, *, force: bool) -> ResearchDecision:
        async with self._lock:
            if self._closed:
                return self._unavailable()
            now = self._now()
            if not force and self._cached is not None and now < self._cached_until:
                return self._cached
            task = self._decision_task
            if task is None or task.done():
                task = asyncio.create_task(self._decide_and_cache())
                self._decision_task = task
        return await asyncio.shield(task)

    async def _decide_and_cache(self) -> ResearchDecision:
        decision = await self._request_decision(touch_ready=True, start_stopped=True)
        async with self._lock:
            if not self._closed:
                self._cache_decision_locked(decision)
            if self._decision_task is asyncio.current_task():
                self._decision_task = None
        return decision

    async def _retry_failed_once(self) -> ResearchDecision:
        try:
            status = await self._control("research-status")
            if status.state != "FAILED":
                return self._decision_from_status(status)
            status = await self._control("research-stop")
            if status.state == "STOPPED":
                status = await self._control("research-start")
            return self._decision_from_status(status)
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._unavailable()
        finally:
            async with self._lock:
                self._cached = None
                self._cached_until = 0.0
                if self._retry_task is asyncio.current_task():
                    self._retry_task = None

    async def _request_decision(
        self, *, touch_ready: bool, start_stopped: bool
    ) -> ResearchDecision:
        try:
            status = await self._control("research-status")
            if status.state == "STOPPED" and start_stopped:
                status = await self._control("research-start")
            elif (
                status.state == "READY"
                and touch_ready
                and self._active_key_matches(status.generation)
            ):
                # Recheck immediately before the cross-process touch.  A
                # pending, disabled, or stale generation cannot extend idle.
                if self._active_key_matches(status.generation):
                    generation = status.generation
                    status = await self._control(
                        "research-start",
                        precondition=lambda: self._active_key_matches(generation),
                    )
                    if (
                        status.state == "READY"
                        and self._active_key_matches(status.generation)
                    ):
                        self._last_activity_touch_at = (
                            asyncio.get_running_loop().time()
                        )
            return self._decision_from_status(status)
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._unavailable()

    async def _control(
        self,
        operation: str,
        *,
        precondition: Callable[[], bool] | None = None,
    ) -> ControlStatus:
        async with self._control_lock:
            loop = asyncio.get_running_loop()
            delay = (
                self._last_control_call_at
                + self._minimum_control_interval_seconds
                - loop.time()
            )
            if delay > 0:
                await asyncio.sleep(delay)
            if precondition is not None and not precondition():
                raise ResearchGatewayError("research control precondition differs")
            self._last_control_call_at = loop.time()
            value = await self.control.request(operation)
            if not isinstance(value, ControlStatus):
                raise ResearchGatewayError("research control response is invalid")
            value.validate()
            self._last_generation = max(self._last_generation, value.generation)
            return value

    def _decision_from_status(self, status: ControlStatus) -> ResearchDecision:
        routable = status.state == "READY" and self._active_key_matches(
            status.generation
        )
        if status.state == "READY" and not routable:
            return ResearchDecision(
                state="STOPPING",
                generation=status.generation,
                retry_after_seconds=1,
                routable=False,
            )
        decision = ResearchDecision(
            state=status.state,
            generation=status.generation,
            retry_after_seconds=status.retry_after_seconds,
            routable=routable,
        )
        decision.validate()
        return decision

    def _active_key_matches(self, generation: int) -> bool:
        try:
            status = self.keyring.status("research")
        except ValueError:
            return False
        return (
            status.state is WorkerKeyState.ACTIVE
            and status.generation == generation
        )

    def _release_now(self, generation: int) -> None:
        count = self._leases.get(generation, 0)
        if count <= 0:
            raise ResearchGatewayError("research lease accounting differs")
        if count == 1:
            self._leases.pop(generation)
            self._last_release_at = asyncio.get_running_loop().time()
            self._schedule_touch(generation)
        else:
            self._leases[generation] = count - 1

    async def _touch_generation(self, generation: int) -> None:
        task = self._schedule_touch(generation)
        if task is not None:
            await asyncio.shield(task)

    def _schedule_touch(self, generation: int) -> asyncio.Task[None] | None:
        if self._closed or not self._active_key_matches(generation):
            return None
        self._pending_touch_generation = generation
        task = self._touch_task
        if task is None or task.done():
            task = asyncio.create_task(self._drain_touches())
            self._touch_task = task
        return task

    async def _drain_touches(self) -> None:
        current = asyncio.current_task()
        try:
            while not self._closed:
                generation = self._pending_touch_generation
                self._pending_touch_generation = None
                if generation is None:
                    return
                await self._touch_once(generation)
                if self._pending_touch_generation == generation:
                    # The touch waited through the most recent release for
                    # this generation, so all of those release signals are
                    # represented by the acknowledgement just received.
                    self._pending_touch_generation = None
        except asyncio.CancelledError:
            raise
        except Exception:
            # Touches are best-effort liveness extensions.  A failed control
            # exchange makes admission temporarily unavailable but must not
            # strand lease accounting, kill the heartbeat, or expose a
            # transport exception from a detached task.
            await self._cache_unavailable()
        finally:
            if self._touch_task is current:
                self._touch_task = None

    async def _touch_once(self, generation: int) -> ResearchDecision:
        loop = asyncio.get_running_loop()
        while True:
            delay = (
                max(self._last_activity_touch_at, self._last_release_at)
                + self.cache_seconds
                - loop.time()
            )
            if delay <= 0:
                break
            await asyncio.sleep(delay)
        status = await self._control("research-status")
        if (
            status.state == "READY"
            and status.generation == generation
            and self._active_key_matches(generation)
        ):
            status = await self._control(
                "research-start",
                precondition=lambda: self._active_key_matches(generation),
            )
            if (
                status.state == "READY"
                and status.generation == generation
                and self._active_key_matches(generation)
            ):
                self._last_activity_touch_at = loop.time()
        decision = self._decision_from_status(status)
        async with self._lock:
            if not self._closed:
                self._cache_decision_locked(decision)
        return decision

    def _cache_decision_locked(self, decision: ResearchDecision) -> None:
        # Concurrent requests share an in-flight task, but a routable READY
        # result is never reused by a later request.  Every distinct admission
        # must observe the supervisor so READY -> STOPPING cannot hide behind
        # either an admission or background-touch cache.
        if decision.routable:
            self._cached = None
            self._cached_until = 0.0
        else:
            self._cached = decision
            self._cached_until = self._now() + self.cache_seconds

    async def _cache_unavailable(self) -> None:
        async with self._lock:
            if not self._closed:
                self._cached = self._unavailable()
                self._cached_until = 0.0

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.heartbeat_seconds)
                async with self._lock:
                    generations = tuple(self._leases)
                for generation in generations:
                    await self._touch_generation(generation)
        except asyncio.CancelledError:
            raise

    def _unavailable(self) -> ResearchDecision:
        return ResearchDecision(
            state="STOPPING",
            generation=self._last_generation,
            retry_after_seconds=1,
            routable=False,
        )

    def _now(self) -> float:
        try:
            value = float(self.monotonic())
        except (TypeError, ValueError, OverflowError) as exc:
            raise ResearchGatewayError("research coordinator clock is invalid") from exc
        if not math.isfinite(value) or value < 0:
            raise ResearchGatewayError("research coordinator clock is invalid")
        return value
