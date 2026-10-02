"""Authenticated supervisor-to-gateway worker activation tests."""
from __future__ import annotations

import asyncio
import json
import socket
import struct
import traceback
import unittest

from procurement_os.staging_worker_activation import (
    ACTIVATION_VERSION,
    GatewayActivationProtocol,
    GatewayActivationServer,
    SupervisorActivationClient,
    WorkerActivationError,
    mint_activation_request,
    parse_activation_response,
)
from procurement_os.staging_worker_types import WorkerKeyState, WorkerKeyring


BOOT_KEY = bytes.fromhex("91" * 32)
SYNTHETIC_KEY = bytes.fromhex("11" * 32)
RESEARCH_KEY = bytes.fromhex("22" * 32)
NONCE = "ab" * 32


class WorkerActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 1_700_000_000.0
        self.keyring = WorkerKeyring(
            {"synthetic": SYNTHETIC_KEY, "research": RESEARCH_KEY},
            active_roles=frozenset(),
        )
        self.keys = {
            ("synthetic", 0): SYNTHETIC_KEY,
            ("research", 0): RESEARCH_KEY,
            ("research", 3): bytes.fromhex("33" * 32),
        }
        self.protocol = GatewayActivationProtocol(
            key=BOOT_KEY,
            keyring=self.keyring,
            load_key=lambda role, generation: self.keys[(role, generation)],
            wall_clock=lambda: self.now,
        )

    def _request(
        self,
        operation: str,
        *,
        role: str = "research",
        generation: int = 0,
        nonce: str = NONCE,
    ) -> bytes:
        return mint_activation_request(
            key=BOOT_KEY,
            operation=operation,
            role=role,
            generation=generation,
            issued_ms=int(self.now * 1_000),
            nonce=nonce,
        )

    def _dispatch(self, operation: str, **values) -> bytes:
        request = self._request(operation, **values)
        response = self.protocol.dispatch(request)
        role = values.get("role", "research")
        generation = values.get("generation", 0)
        parse_activation_response(
            message=response,
            key=BOOT_KEY,
            operation=operation,
            role=role,
            generation=generation,
            nonce=values.get("nonce", NONCE),
            worker_key=self.keys[(role, generation)]
            if operation == "PREPARE"
            else None,
        )
        return response

    def test_prepare_commit_disable_are_exact_and_prepare_proves_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "not active"):
            self.keyring.current("research")
        self._dispatch("PREPARE")
        self.assertEqual(
            self.keyring.status("research").state,
            WorkerKeyState.PENDING,
        )
        with self.assertRaisesRegex(ValueError, "not active"):
            self.keyring.current("research")

        self._dispatch("COMMIT", nonce="bc" * 32)
        self.assertEqual(self.keyring.current("research"), (RESEARCH_KEY, 0))
        self._dispatch("DISABLE", nonce="cd" * 32)
        self.assertEqual(
            self.keyring.status("research").state,
            WorkerKeyState.DISABLED,
        )
        with self.assertRaisesRegex(ValueError, "not active"):
            self.keyring.current("research")

    def test_skipped_generation_is_allowed_but_retired_generation_is_not(self) -> None:
        self._dispatch("PREPARE")
        self._dispatch("COMMIT", nonce="bc" * 32)
        self._dispatch("DISABLE", nonce="cd" * 32)
        self._dispatch("PREPARE", generation=3, nonce="de" * 32)
        self._dispatch("COMMIT", generation=3, nonce="ef" * 32)
        self.assertEqual(self.keyring.current("research"), (self.keys[("research", 3)], 3))
        self._dispatch("DISABLE", generation=3, nonce="fa" * 32)
        with self.assertRaisesRegex(WorkerActivationError, "transition refused"):
            self.protocol.dispatch(self._request("PREPARE", nonce="12" * 32))

    def test_exact_duplicate_returns_cached_ack_without_reapplying_transition(self) -> None:
        request = self._request("PREPARE")
        first = self.protocol.dispatch(request)
        second = self.protocol.dispatch(request)
        self.assertEqual(first, second)
        self.assertEqual(self.keyring.status("research").state, WorkerKeyState.PENDING)

        rebound = json.loads(request)
        rebound["generation"] = 1
        with self.assertRaises(WorkerActivationError):
            self.protocol.dispatch(
                mint_activation_request(
                    key=BOOT_KEY,
                    operation="PREPARE",
                    role="research",
                    generation=1,
                    issued_ms=int(self.now * 1_000),
                    nonce=NONCE,
                )
            )

    def test_wrong_key_proof_is_rejected_by_supervisor(self) -> None:
        response = self.protocol.dispatch(self._request("PREPARE"))
        with self.assertRaisesRegex(WorkerActivationError, "proof differs"):
            parse_activation_response(
                message=response,
                key=BOOT_KEY,
                operation="PREPARE",
                role="research",
                generation=0,
                nonce=NONCE,
                worker_key=bytes.fromhex("44" * 32),
            )

    def test_malformed_tampered_noncanonical_and_expired_requests_do_not_mutate(self) -> None:
        valid = self._request("PREPARE")
        cases = [
            valid + b" ",
            valid.replace(b'"version"', b'"extra":1,"version"', 1),
            valid.replace(b'"signature"', b'"signature2"', 1),
            b'{"version":"' + ACTIVATION_VERSION.encode("ascii") + b'"}',
            b'{"a":1,"a":1}',
        ]
        for value in cases:
            with self.subTest(value=value[:30]):
                with self.assertRaises(WorkerActivationError):
                    self.protocol.dispatch(value)
                self.assertEqual(
                    self.keyring.status("research").state,
                    WorkerKeyState.DISABLED,
                )

        self.now += 6
        with self.assertRaisesRegex(WorkerActivationError, "time is invalid"):
            self.protocol.dispatch(valid)

    def test_request_contract_rejects_bool_negative_and_overflow_generations(self) -> None:
        for generation in (True, -1, 2**63):
            with self.subTest(generation=generation):
                with self.assertRaises(WorkerActivationError):
                    mint_activation_request(
                        key=BOOT_KEY,
                        operation="PREPARE",
                        role="research",
                        generation=generation,
                        issued_ms=int(self.now * 1_000),
                    )

    def test_real_connected_socket_round_trip_and_clean_shutdown(self) -> None:
        async def scenario() -> None:
            supervisor_socket, gateway_socket = socket.socketpair(
                socket.AF_UNIX,
                socket.SOCK_STREAM,
            )
            client = SupervisorActivationClient(
                channel=supervisor_socket,
                key=BOOT_KEY,
                wall_clock=lambda: self.now,
            )
            server = GatewayActivationServer(
                channel=gateway_socket,
                protocol=self.protocol,
                fatal_handler=lambda: None,
            )
            task = asyncio.create_task(server.serve())
            try:
                await asyncio.to_thread(
                    client.prepare,
                    role="research",
                    generation=0,
                    worker_key=RESEARCH_KEY,
                )
                await asyncio.to_thread(client.commit, role="research", generation=0)
                self.assertEqual(self.keyring.current("research"), (RESEARCH_KEY, 0))
                await asyncio.to_thread(client.disable, role="research", generation=0)
                self.assertEqual(
                    self.keyring.status("research").state,
                    WorkerKeyState.DISABLED,
                )
            finally:
                client.close()
                await server.close()
                await asyncio.wait_for(task, timeout=1)

        asyncio.run(scenario())

    def test_server_rejects_oversized_frame_and_poisoned_channel(self) -> None:
        async def scenario() -> None:
            supervisor_socket, gateway_socket = socket.socketpair(
                socket.AF_UNIX,
                socket.SOCK_STREAM,
            )
            supervisor_socket.setblocking(False)
            server = GatewayActivationServer(
                channel=gateway_socket,
                protocol=self.protocol,
                fatal_handler=lambda: None,
            )
            task = asyncio.create_task(server.serve())
            try:
                loop = asyncio.get_running_loop()
                await loop.sock_sendall(supervisor_socket, struct.pack("!I", 2_049))
                with self.assertRaisesRegex(WorkerActivationError, "server failed"):
                    await asyncio.wait_for(task, timeout=1)
            finally:
                supervisor_socket.close()
                await server.close()

        asyncio.run(scenario())

    def test_server_survives_idle_then_processes_request(self) -> None:
        async def scenario() -> None:
            supervisor_socket, gateway_socket = socket.socketpair(
                socket.AF_UNIX,
                socket.SOCK_STREAM,
            )
            client = SupervisorActivationClient(
                channel=supervisor_socket,
                key=BOOT_KEY,
                wall_clock=lambda: self.now,
                timeout_seconds=0.2,
            )
            server = GatewayActivationServer(
                channel=gateway_socket,
                protocol=self.protocol,
                fatal_handler=lambda: None,
                timeout_seconds=0.05,
            )
            task = asyncio.create_task(server.serve())
            try:
                await asyncio.sleep(0.08)
                self.assertFalse(task.done())
                await asyncio.to_thread(
                    client.prepare,
                    role="research",
                    generation=0,
                    worker_key=RESEARCH_KEY,
                )
            finally:
                client.close()
                await server.close()
                await task

        asyncio.run(scenario())

    def test_ambiguous_client_exchange_permanently_poisons_channel(self) -> None:
        supervisor_socket, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        client = SupervisorActivationClient(
            channel=supervisor_socket,
            key=BOOT_KEY,
            wall_clock=lambda: self.now,
            timeout_seconds=0.05,
        )
        try:
            with self.assertRaisesRegex(WorkerActivationError, "exchange failed"):
                client.commit(role="research", generation=0)
            with self.assertRaisesRegex(WorkerActivationError, "closed"):
                client.disable(role="research", generation=0)
            peer.settimeout(0.1)
            first = peer.recv(4)
            self.assertEqual(len(first), 4)
            length = struct.unpack("!I", first)[0]
            self.assertEqual(len(peer.recv(length)), length)
            self.assertEqual(peer.recv(1), b"")
        finally:
            client.close()
            peer.close()

    def test_loader_path_never_appears_in_formatted_exception(self) -> None:
        protocol = GatewayActivationProtocol(
            key=BOOT_KEY,
            keyring=self.keyring,
            load_key=lambda _role, _generation: (_ for _ in ()).throw(
                OSError("/private/sentinel/research-7.key")
            ),
            wall_clock=lambda: self.now,
        )
        try:
            protocol.dispatch(self._request("PREPARE"))
        except WorkerActivationError as exc:
            rendered = "".join(traceback.format_exception(exc))
        else:
            self.fail("loader failure was accepted")
        self.assertNotIn("/private/sentinel", rendered)

    def test_unexpected_server_cancellation_poisons_and_invokes_fatal_handler(self) -> None:
        async def scenario() -> None:
            supervisor_socket, gateway_socket = socket.socketpair(
                socket.AF_UNIX,
                socket.SOCK_STREAM,
            )
            fatal: list[bool] = []
            server = GatewayActivationServer(
                channel=gateway_socket,
                protocol=self.protocol,
                fatal_handler=lambda: fatal.append(True),
            )
            task = asyncio.create_task(server.serve())
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(fatal, [True])
            self.assertTrue(server._closed)
            supervisor_socket.settimeout(0.1)
            self.assertEqual(supervisor_socket.recv(1), b"")
            supervisor_socket.close()

        asyncio.run(scenario())

    def test_unexpected_loader_failure_poisons_server_and_invokes_fatal(self) -> None:
        async def scenario() -> None:
            supervisor_socket, gateway_socket = socket.socketpair(
                socket.AF_UNIX,
                socket.SOCK_STREAM,
            )
            supervisor_socket.setblocking(False)
            fatal: list[bool] = []
            protocol = GatewayActivationProtocol(
                key=BOOT_KEY,
                keyring=self.keyring,
                load_key=lambda _role, _generation: (_ for _ in ()).throw(
                    RuntimeError("private loader failure")
                ),
                wall_clock=lambda: self.now,
            )
            server = GatewayActivationServer(
                channel=gateway_socket,
                protocol=protocol,
                fatal_handler=lambda: fatal.append(True),
            )
            task = asyncio.create_task(server.serve())
            loop = asyncio.get_running_loop()
            request = self._request("PREPARE")
            await loop.sock_sendall(
                supervisor_socket,
                struct.pack("!I", len(request)) + request,
            )
            with self.assertRaisesRegex(WorkerActivationError, "server failed"):
                await asyncio.wait_for(task, timeout=1)
            self.assertEqual(fatal, [True])
            self.assertTrue(server._closed)
            self.assertEqual(await loop.sock_recv(supervisor_socket, 1), b"")
            supervisor_socket.close()

        asyncio.run(scenario())

    def test_close_propagates_cancellation_of_the_closing_task(self) -> None:
        async def scenario() -> None:
            supervisor_socket, gateway_socket = socket.socketpair(
                socket.AF_UNIX,
                socket.SOCK_STREAM,
            )
            server = GatewayActivationServer(
                channel=gateway_socket,
                protocol=self.protocol,
                fatal_handler=lambda: None,
            )
            first_cancel = asyncio.Event()

            async def delayed_cancellation() -> None:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    first_cancel.set()
                    await asyncio.Event().wait()

            serve_task = asyncio.create_task(delayed_cancellation())
            server._serve_task = serve_task
            close_task = asyncio.create_task(server.close())
            await asyncio.wait_for(first_cancel.wait(), timeout=1)
            close_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await close_task
            self.assertTrue(serve_task.cancelled())
            supervisor_socket.close()

        asyncio.run(scenario())

    def test_errors_and_reprs_do_not_expose_keys(self) -> None:
        values = [repr(self.protocol), repr(self.keyring)]
        try:
            self.protocol.dispatch(self._request("COMMIT"))
        except WorkerActivationError as exc:
            values.append(str(exc))
        for value in values:
            self.assertNotIn(BOOT_KEY.hex(), value)
            self.assertNotIn(RESEARCH_KEY.hex(), value)


if __name__ == "__main__":
    unittest.main()
