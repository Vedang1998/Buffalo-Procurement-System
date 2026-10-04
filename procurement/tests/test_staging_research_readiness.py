"""Tests for generation- and process-bound research readiness proofs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest import mock

from procurement_os.staging_research_readiness import (
    MAX_READINESS_PAYLOAD_BYTES,
    ReadinessProcessIdentity,
    ResearchReadinessError,
    ResearchReadinessReader,
    ResearchReadinessWriter,
    current_readiness_process_identity,
    mint_readiness_frame,
    mint_readiness_payload,
    parse_readiness_payload,
)


def _canonical(value: dict[str, object]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


class StagingResearchReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key = b"r" * 32
        self.identity = ReadinessProcessIdentity(
            pid=401,
            start_ticks=9_001,
            process_group=401,
            session_id=401,
        )
        self.validation_identity = "a" * 64

    def _frame(self, **overrides) -> bytes:
        values = {
            "key": self.key,
            "generation": 3,
            "identity": self.identity,
            "state": "READY",
            "validation_identity": self.validation_identity,
        }
        values.update(overrides)
        return mint_readiness_frame(**values)

    def _reader(self, descriptor: int, **overrides) -> ResearchReadinessReader:
        values = {
            "key": self.key,
            "expected_generation": 3,
            "expected_identity": self.identity,
            "expected_validation_identity": self.validation_identity,
        }
        values.update(overrides)
        return ResearchReadinessReader(descriptor, **values)

    def test_ready_frame_is_fragmented_and_accepted_only_after_eof(self) -> None:
        read_descriptor, write_descriptor = os.pipe()
        reader = self._reader(read_descriptor)
        frame = self._frame()
        try:
            os.write(write_descriptor, frame[:2])
            self.assertIsNone(reader.poll())
            os.write(write_descriptor, frame[2:])
            self.assertIsNone(reader.poll())
            os.close(write_descriptor)
            write_descriptor = -1
            proof = reader.poll()
            self.assertIsNotNone(proof)
            assert proof is not None
            self.assertTrue(proof.ready)
            self.assertEqual(proof.generation, 3)
            self.assertEqual(proof.identity, self.identity)
            self.assertEqual(proof.validation_identity, self.validation_identity)
            self.assertIs(reader.poll(), proof)
            self.assertTrue(reader.closed)
        finally:
            if write_descriptor >= 0:
                os.close(write_descriptor)
            reader.close()

    def test_signed_failure_is_terminal_but_never_ready(self) -> None:
        payload = mint_readiness_payload(
            key=self.key,
            generation=3,
            identity=self.identity,
            state="FAILED",
            failure="SEMANTIC",
        )
        proof = parse_readiness_payload(
            payload,
            key=self.key,
            expected_generation=3,
            expected_identity=self.identity,
            expected_validation_identity=self.validation_identity,
        )
        self.assertFalse(proof.ready)
        self.assertEqual(proof.failure, "SEMANTIC")
        self.assertIsNone(proof.validation_identity)

    def test_binding_or_signature_drift_is_rejected(self) -> None:
        payload = self._frame()[4:]
        wrong_identity = ReadinessProcessIdentity(
            pid=402,
            start_ticks=self.identity.start_ticks,
            process_group=402,
            session_id=402,
        )
        cases = (
            {"key": b"x" * 32},
            {"expected_generation": 4},
            {"expected_identity": wrong_identity},
            {"expected_validation_identity": "b" * 64},
        )
        for overrides in cases:
            with self.subTest(overrides=tuple(overrides)):
                values = {
                    "key": self.key,
                    "expected_generation": 3,
                    "expected_identity": self.identity,
                    "expected_validation_identity": self.validation_identity,
                }
                values.update(overrides)
                with self.assertRaises(ResearchReadinessError):
                    parse_readiness_payload(payload, **values)

        envelope = json.loads(payload)
        envelope["signature"] = "0" * 64
        with self.assertRaisesRegex(ResearchReadinessError, "signature differs"):
            parse_readiness_payload(
                _canonical(envelope),
                key=self.key,
                expected_generation=3,
                expected_identity=self.identity,
                expected_validation_identity=self.validation_identity,
            )

    def test_mint_refuses_ambiguous_values_and_invalid_identity(self) -> None:
        invalid_identity = ReadinessProcessIdentity(
            pid=401,
            start_ticks=9_001,
            process_group=400,
            session_id=401,
        )
        cases = (
            {"key": b"short"},
            {"generation": True},
            {"identity": invalid_identity},
            {"state": "READY", "validation_identity": None},
            {
                "state": "READY",
                "failure": "SEMANTIC",
                "validation_identity": self.validation_identity,
            },
            {"state": "FAILED", "failure": "CRASH", "validation_identity": None},
            {
                "state": "FAILED",
                "failure": "SEMANTIC",
                "validation_identity": self.validation_identity,
            },
        )
        for overrides in cases:
            with self.subTest(overrides=tuple(overrides)):
                values = {
                    "key": self.key,
                    "generation": 3,
                    "identity": self.identity,
                    "state": "READY",
                    "failure": None,
                    "validation_identity": self.validation_identity,
                }
                values.update(overrides)
                with self.assertRaises(ResearchReadinessError):
                    mint_readiness_payload(**values)

    def test_reader_rejects_empty_truncated_overlong_and_multiple_frames(self) -> None:
        valid = self._frame()
        attacks = {
            "empty": b"",
            "short-prefix": b"\x00\x01",
            "zero-length": struct.pack("!I", 0),
            "too-large": struct.pack("!I", MAX_READINESS_PAYLOAD_BYTES + 1),
            "truncated": valid[:-1],
            "trailing": valid + b"x",
            "second-frame": valid + valid,
        }
        for name, raw in attacks.items():
            with self.subTest(name=name):
                read_descriptor, write_descriptor = os.pipe()
                reader = self._reader(read_descriptor)
                try:
                    if raw:
                        os.write(write_descriptor, raw)
                    os.close(write_descriptor)
                    write_descriptor = -1
                    with self.assertRaises(ResearchReadinessError):
                        reader.poll()
                    self.assertTrue(reader.closed)
                    with self.assertRaisesRegex(
                        ResearchReadinessError, "reader is poisoned"
                    ):
                        reader.poll()
                finally:
                    if write_descriptor >= 0:
                        os.close(write_descriptor)
                    reader.close()

    def test_noncanonical_duplicate_nan_and_unknown_members_are_rejected(self) -> None:
        payloads = (
            b'{"state":"READY", "state":"READY"}',
            b'{"value":NaN}',
            b'{"unknown":true}',
            b"[" * 1_100 + b"]" * 1_100,
            b"\xff",
        )
        for payload in payloads:
            with self.subTest(payload=payload[:16]):
                with self.assertRaises(ResearchReadinessError):
                    parse_readiness_payload(
                        payload,
                        key=self.key,
                        expected_generation=3,
                        expected_identity=self.identity,
                        expected_validation_identity=self.validation_identity,
                    )

    def test_reader_requires_an_anonymous_pipe_read_endpoint(self) -> None:
        read_descriptor, write_descriptor = os.pipe()
        try:
            with self.assertRaisesRegex(
                ResearchReadinessError, "descriptor differs"
            ):
                self._reader(write_descriptor)
        finally:
            os.close(read_descriptor)
            os.close(write_descriptor)

        with tempfile.TemporaryDirectory() as temporary:
            fifo = os.path.join(temporary, "readiness.fifo")
            os.mkfifo(fifo, 0o600)
            fifo_descriptor = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
            try:
                with self.assertRaisesRegex(
                    ResearchReadinessError, "descriptor differs"
                ):
                    self._reader(fifo_descriptor)
            finally:
                os.close(fifo_descriptor)

    def test_writer_emits_one_bound_frame_and_closes_its_pipe(self) -> None:
        read_descriptor, write_descriptor = os.pipe()
        reader = self._reader(read_descriptor)
        with mock.patch(
            "procurement_os.staging_research_readiness.current_readiness_process_identity",
            return_value=self.identity,
        ):
            writer = ResearchReadinessWriter(
                write_descriptor, key=self.key, generation=3
            )
        writer.emit_ready(self.validation_identity)
        proof = reader.poll()
        self.assertIsNotNone(proof)
        assert proof is not None
        self.assertTrue(proof.ready)
        self.assertTrue(writer.closed)
        with self.assertRaisesRegex(
            ResearchReadinessError, "already emitted"
        ):
            writer.emit_ready(self.validation_identity)
        reader.close()

    def test_writer_rejects_a_read_endpoint_without_consuming_it(self) -> None:
        read_descriptor, write_descriptor = os.pipe()
        try:
            with self.assertRaisesRegex(
                ResearchReadinessError, "descriptor differs"
            ):
                ResearchReadinessWriter(
                    read_descriptor, key=self.key, generation=3
                )
            os.write(write_descriptor, b"x")
            self.assertEqual(os.read(read_descriptor, 1), b"x")
        finally:
            os.close(read_descriptor)
            os.close(write_descriptor)

    def test_real_session_leader_identity_is_captured(self) -> None:
        source = Path(__file__).resolve().parents[1] / "src"
        script = (
            "import json,sys;"
            f"sys.path.insert(0,{str(source)!r});"
            "from procurement_os.staging_research_readiness import "
            "current_readiness_process_identity as capture;"
            "v=capture();print(json.dumps([v.pid,v.start_ticks,v.process_group,v.session_id]))"
        )
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        pid, start_ticks, process_group, session_id = json.loads(completed.stdout)
        self.assertGreater(start_ticks, 0)
        self.assertEqual((process_group, session_id), (pid, pid))

    def test_invalid_expectation_does_not_mutate_the_caller_descriptor(self) -> None:
        read_descriptor, write_descriptor = os.pipe()
        try:
            os.set_inheritable(read_descriptor, True)
            self.assertTrue(os.get_blocking(read_descriptor))
            with self.assertRaisesRegex(
                ResearchReadinessError, "expectation differs"
            ):
                self._reader(
                    read_descriptor,
                    expected_validation_identity="wrong",
                )
            self.assertTrue(os.get_inheritable(read_descriptor))
            self.assertTrue(os.get_blocking(read_descriptor))
        finally:
            os.close(read_descriptor)
            os.close(write_descriptor)

    def test_reader_close_is_idempotent_and_no_private_detail_is_exposed(self) -> None:
        read_descriptor, write_descriptor = os.pipe()
        reader = self._reader(read_descriptor)
        reader.close()
        reader.close()
        os.close(write_descriptor)
        with self.assertRaises(ResearchReadinessError) as caught:
            reader.poll()
        self.assertNotIn("/private/sentinel", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
