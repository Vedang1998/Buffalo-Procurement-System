"""Gateway research lifecycle admission and lease tests."""
from __future__ import annotations

import asyncio
import unittest

from procurement_os.staging_research_gateway import ResearchGatewayCoordinator
from procurement_os.staging_supervisor import ControlStatus
from procurement_os.staging_worker_types import WorkerKeyring


SYNTHETIC_KEY = bytes.fromhex("11" * 32)
RESEARCH_KEY = bytes.fromhex("22" * 32)


class _Control:
    def __init__(self, state: str = "STOPPED", generation: int = 0) -> None:
        self.state = state
        self.generation = generation
        self.calls: list[str] = []
        self.call_times: list[float] = []
        self.block: asyncio.Event | None = None
        self.raise_on_call: int | None = None

    async def request(self, operation: str) -> ControlStatus:
        self.calls.append(operation)
        self.call_times.append(asyncio.get_running_loop().time())
        if self.raise_on_call == len(self.calls):
            raise OSError("/private/sentinel/control.sock")
        if self.block is not None:
            await self.block.wait()
        if operation == "research-start":
            if self.state == "STOPPED":
                self.state = "VALIDATING"
            return self._status()
        if operation == "research-stop":
            self.state = "STOPPED"
            return self._status()
        if operation != "research-status":
            raise ValueError("unexpected operation")
        return self._status()

    def _status(self) -> ControlStatus:
        return ControlStatus(
            state=self.state,
            generation=self.generation,
            retry_after_seconds=3 if self.state == "VALIDATING" else None,
        )


class ResearchGatewayCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.keyring = WorkerKeyring(
            {"synthetic": SYNTHETIC_KEY, "research": RESEARCH_KEY},
            active_roles=frozenset(),
        )
        self.control = _Control()
        self.now = 100.0
        self.coordinator = ResearchGatewayCoordinator(
            control=self.control,
            keyring=self.keyring,
            monotonic=lambda: self.now,
            cache_seconds=1,
            heartbeat_seconds=1,
        )

    async def asyncTearDown(self) -> None:
        await self.coordinator.shutdown()

    def _activate(self, generation: int = 0, key: bytes = RESEARCH_KEY) -> None:
        self.keyring.prepare(role="research", key=key, generation=generation)
        self.keyring.commit(role="research", generation=generation)

    async def test_cold_concurrency_is_one_status_start_and_never_routable(self) -> None:
        gate = asyncio.Event()
        self.control.block = gate
        tasks = [
            asyncio.create_task(self.coordinator.authorized_request())
            for _ in range(12)
        ]
        await asyncio.sleep(0)
        gate.set()
        leases = await asyncio.gather(*tasks)
        self.assertEqual(self.control.calls, ["research-status", "research-start"])
        self.assertTrue(all(not lease.decision.routable for lease in leases))
        self.assertTrue(all(lease.decision.state == "VALIDATING" for lease in leases))

    async def test_cancelled_waiter_does_not_cancel_shared_control(self) -> None:
        gate = asyncio.Event()
        self.control.block = gate
        first = asyncio.create_task(self.coordinator.authorized_request())
        second = asyncio.create_task(self.coordinator.authorized_request())
        await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        gate.set()
        lease = await second
        self.assertEqual(lease.decision.state, "VALIDATING")
        self.assertEqual(self.control.calls, ["research-status", "research-start"])

    async def test_ready_requires_exact_active_generation_and_holds_lease(self) -> None:
        self.control.state = "READY"
        self._activate()
        lease = await self.coordinator.authorized_request()
        async with lease as decision:
            self.assertTrue(decision.routable)
            self.assertEqual(decision.generation, 0)
            self.assertEqual(self.coordinator._leases, {0: 1})
        self.assertEqual(self.coordinator._leases, {})
        self.assertEqual(
            self.control.calls,
            ["research-status", "research-start"],
        )
        await asyncio.sleep(1.2)
        self.assertEqual(
            self.control.calls,
            [
                "research-status",
                "research-start",
                "research-status",
                "research-start",
            ],
        )

        self.control.calls.clear()
        self.keyring.disable(role="research", generation=0)
        self.now += 2
        mismatch = await self.coordinator.authorized_request()
        self.assertFalse(mismatch.decision.routable)
        self.assertEqual(mismatch.decision.state, "STOPPING")

    async def test_failed_is_latched_for_get_and_explicit_retry_is_singleflight(self) -> None:
        self.control.state = "FAILED"
        leases = await asyncio.gather(
            *(self.coordinator.authorized_request() for _ in range(4))
        )
        self.assertTrue(all(lease.decision.state == "FAILED" for lease in leases))
        self.assertEqual(self.control.calls, ["research-status"])

        self.control.calls.clear()
        decisions = await asyncio.gather(
            *(self.coordinator.retry_failed() for _ in range(4))
        )
        self.assertTrue(all(value.state == "VALIDATING" for value in decisions))
        self.assertEqual(
            self.control.calls,
            ["research-status", "research-stop", "research-start"],
        )

    async def test_lease_heartbeat_touches_only_same_ready_generation(self) -> None:
        self.control.state = "READY"
        self._activate()
        await self.coordinator.startup()
        lease = await self.coordinator.authorized_request()
        await lease.__aenter__()
        baseline = len(self.control.calls)
        deadline = asyncio.get_running_loop().time() + 2
        while (
            "research-start" not in self.control.calls[baseline:]
            and asyncio.get_running_loop().time() < deadline
        ):
            await asyncio.sleep(0.02)
        self.assertGreater(len(self.control.calls), baseline)
        self.assertIn("research-start", self.control.calls[baseline:])

        self.control.state = "STOPPED"
        baseline = len(self.control.calls)
        await asyncio.sleep(1.05)
        self.assertNotIn("research-start", self.control.calls[baseline:])
        await lease.__aexit__(None, None, None)

    async def test_shutdown_cancels_heartbeat_and_rejects_new_admission(self) -> None:
        await self.coordinator.startup()
        await self.coordinator.shutdown()
        lease = await self.coordinator.authorized_request()
        self.assertFalse(lease.decision.routable)
        self.assertEqual(self.control.calls, [])

    async def test_shutdown_preserves_caller_cancellation(self) -> None:
        first_cancel = asyncio.Event()

        async def delayed_cancellation() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                first_cancel.set()
                await asyncio.Event().wait()

        background = asyncio.create_task(delayed_cancellation())
        self.coordinator._heartbeat_task = background
        shutdown = asyncio.create_task(self.coordinator.shutdown())
        await asyncio.wait_for(first_cancel.wait(), timeout=1)
        shutdown.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await shutdown
        self.assertTrue(background.cancelled())

    async def test_in_flight_lease_releases_cleanly_after_shutdown(self) -> None:
        self.control.state = "READY"
        self._activate()
        lease = await self.coordinator.authorized_request()
        await lease.__aenter__()
        self.assertEqual(self.coordinator._leases, {0: 1})
        await self.coordinator.shutdown()
        await lease.__aexit__(asyncio.CancelledError, asyncio.CancelledError(), None)
        self.assertEqual(self.coordinator._leases, {})

    async def test_cancelled_lease_exit_cannot_leak_activity_lease(self) -> None:
        self.control.state = "READY"
        self._activate()
        lease = await self.coordinator.authorized_request()
        await lease.__aenter__()
        task = asyncio.create_task(lease.__aexit__(None, None, None))
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self.assertEqual(self.coordinator._leases, {})

    async def test_rapid_ready_requests_stay_within_real_control_budget(self) -> None:
        self.control.state = "READY"
        self._activate()
        for _ in range(8):
            lease = await self.coordinator.authorized_request()
            async with lease as decision:
                self.assertTrue(decision.routable)
        await asyncio.sleep(1.2)
        for start in self.control.call_times:
            within_window = sum(
                start <= observed < start + 1
                for observed in self.control.call_times
            )
            self.assertLessEqual(within_window, 8)

    async def test_routable_decision_is_not_reused_after_supervisor_stops(self) -> None:
        self.control.state = "READY"
        self._activate()
        first = await self.coordinator.authorized_request()
        async with first as decision:
            self.assertTrue(decision.routable)
        self.control.state = "STOPPING"
        second = await self.coordinator.authorized_request()
        self.assertFalse(second.decision.routable)
        self.assertEqual(second.decision.state, "STOPPING")

    async def test_background_touch_never_caches_routable_admission(self) -> None:
        self.control.state = "READY"
        self._activate()
        lease = await self.coordinator.authorized_request()
        async with lease as decision:
            self.assertTrue(decision.routable)
        deadline = asyncio.get_running_loop().time() + 2.5
        while (
            len(self.control.calls) < 4
            and asyncio.get_running_loop().time() < deadline
        ):
            await asyncio.sleep(0.02)
        self.assertGreaterEqual(len(self.control.calls), 4)
        before = len(self.control.calls)
        self.control.state = "STOPPING"
        stopped = await self.coordinator.authorized_request()
        self.assertFalse(stopped.decision.routable)
        self.assertEqual(stopped.decision.state, "STOPPING")
        self.assertGreater(len(self.control.calls), before)

    async def test_ready_key_mismatch_and_stale_touch_never_extend_idle(self) -> None:
        self.control.state = "READY"
        lease = await self.coordinator.authorized_request()
        self.assertFalse(lease.decision.routable)
        self.assertEqual(self.control.calls, ["research-status"])

        self._activate()
        self.control.generation = 1
        self.control.calls.clear()
        await self.coordinator._touch_generation(0)
        self.assertNotIn("research-start", self.control.calls)

    async def test_touch_failure_is_contained_and_heartbeat_remains_live(self) -> None:
        self.control.state = "READY"
        self._activate()
        await self.coordinator.startup()
        lease = await self.coordinator.authorized_request()
        await lease.__aenter__()
        self.coordinator._last_activity_touch_at = float("-inf")
        self.control.raise_on_call = 3
        loop = asyncio.get_running_loop()
        unhandled: list[dict[str, object]] = []
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
        try:
            await asyncio.sleep(1.2)
            self.assertIsNotNone(self.coordinator._heartbeat_task)
            self.assertFalse(self.coordinator._heartbeat_task.done())
            self.assertEqual(self.coordinator._leases, {0: 1})
            self.assertEqual(unhandled, [])
            self.control.raise_on_call = None
            await lease.__aexit__(None, None, None)
        finally:
            loop.set_exception_handler(previous_handler)

    async def test_disable_during_control_delay_prevents_ready_touch(self) -> None:
        self.control.state = "READY"
        self._activate()
        task = asyncio.create_task(self.coordinator.authorized_request())
        while self.control.calls != ["research-status"]:
            await asyncio.sleep(0)
        self.keyring.disable(role="research", generation=0)
        lease = await task
        self.assertFalse(lease.decision.routable)
        self.assertEqual(self.control.calls, ["research-status"])


if __name__ == "__main__":
    unittest.main()
