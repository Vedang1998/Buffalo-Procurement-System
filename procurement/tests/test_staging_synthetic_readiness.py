"""Tests for generation-bound synthetic database-readiness proofs."""
from __future__ import annotations

import json
import os
import struct
import unittest
from unittest import mock

from procurement_os.staging_research_readiness import (
    ReadinessProcessIdentity,
    ResearchReadinessError,
    mint_readiness_payload as mint_research_payload,
    parse_readiness_payload as parse_research_payload,
)
from procurement_os.staging_synthetic_readiness import (
    MAX_READINESS_PAYLOAD_BYTES,
    SyntheticReadinessError,
    SyntheticReadinessReader,
    SyntheticReadinessWriter,
    mint_readiness_frame,
    mint_readiness_payload,
    parse_readiness_payload,
)


class StagingSyntheticReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key = b"s" * 32
        self.identity = ReadinessProcessIdentity(
            pid=501,
            start_ticks=10_001,
            process_group=501,
            session_id=501,
        )
        self.validation_identity = "b" * 64

    def _frame(self, **overrides) -> bytes:
        values = {
            "key": self.key,
            "generation": 7,
            "identity": self.identity,
            "state": "READY",
            "validation_identity": self.validation_identity,
        }
        values.update(overrides)
        return mint_readiness_frame(**values)

    def _reader(self, descriptor: int, **overrides) -> SyntheticReadinessReader:
        values = {
            "key": self.key,
            "expected_generation": 7,
            "expected_identity": self.identity,
            "expected_validation_identity": self.validation_identity,
        }
        values.update(overrides)
        return SyntheticReadinessReader(descriptor, **values)

    def test_ready_frame_is_accepted_only_after_eof(self) -> None:
        read_descriptor, write_descriptor = os.pipe()
        reader = self._reader(read_descriptor)
        frame = self._frame()
        try:
            os.write(write_descriptor, frame[:3])
            self.assertIsNone(reader.poll())
            os.write(write_descriptor, frame[3:])
            self.assertIsNone(reader.poll())
            os.close(write_descriptor)
            write_descriptor = -1
            proof = reader.poll()
            self.assertIsNotNone(proof)
            assert proof is not None
            self.assertTrue(proof.ready)
            self.assertEqual(proof.generation, 7)
            self.assertEqual(proof.identity, self.identity)
            self.assertEqual(proof.validation_identity, self.validation_identity)
            self.assertIs(reader.poll(), proof)
        finally:
            if write_descriptor >= 0:
                os.close(write_descriptor)
            reader.close()

    def test_signed_database_failure_is_terminal_but_never_ready(self) -> None:
        payload = mint_readiness_payload(
            key=self.key,
            generation=7,
            identity=self.identity,
            state="FAILED",
            failure="DATABASE",
        )
        proof = parse_readiness_payload(
            payload,
            key=self.key,
            expected_generation=7,
            expected_identity=self.identity,
            expected_validation_identity=self.validation_identity,
        )
        self.assertFalse(proof.ready)
        self.assertEqual(proof.failure, "DATABASE")
        self.assertIsNone(proof.validation_identity)

    def test_key_generation_process_and_validation_bindings_are_exact(self) -> None:
        payload = self._frame()[4:]
        wrong_identity = ReadinessProcessIdentity(
            pid=502,
            start_ticks=self.identity.start_ticks,
            process_group=502,
            session_id=502,
        )
        cases = (
            {"key": b"x" * 32},
            {"expected_generation": 8},
            {"expected_identity": wrong_identity},
            {"expected_validation_identity": "c" * 64},
        )
        for overrides in cases:
            with self.subTest(overrides=tuple(overrides)):
                values = {
                    "key": self.key,
                    "expected_generation": 7,
                    "expected_identity": self.identity,
                    "expected_validation_identity": self.validation_identity,
                }
                values.update(overrides)
                with self.assertRaises(SyntheticReadinessError):
                    parse_readiness_payload(payload, **values)

        envelope = json.loads(payload)
        envelope["signature"] = "0" * 64
        tampered = json.dumps(
            envelope,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        with self.assertRaisesRegex(SyntheticReadinessError, "signature differs"):
            parse_readiness_payload(
                tampered,
                key=self.key,
                expected_generation=7,
                expected_identity=self.identity,
                expected_validation_identity=self.validation_identity,
            )

    def test_research_and_synthetic_proofs_cannot_cross_roles(self) -> None:
        research = mint_research_payload(
            key=self.key,
            generation=7,
            identity=self.identity,
            state="READY",
            validation_identity=self.validation_identity,
        )
        synthetic = mint_readiness_payload(
            key=self.key,
            generation=7,
            identity=self.identity,
            state="READY",
            validation_identity=self.validation_identity,
        )
        expectations = {
            "key": self.key,
            "expected_generation": 7,
            "expected_identity": self.identity,
            "expected_validation_identity": self.validation_identity,
        }
        with self.assertRaises(SyntheticReadinessError):
            parse_readiness_payload(research, **expectations)
        with self.assertRaises(ResearchReadinessError):
            parse_research_payload(synthetic, **expectations)

    def test_role_specific_failure_vocabulary_is_not_interchangeable(self) -> None:
        with self.assertRaises(SyntheticReadinessError):
            mint_readiness_payload(
                key=self.key,
                generation=7,
                identity=self.identity,
                state="FAILED",
                failure="SEMANTIC",
            )
        with self.assertRaises(ResearchReadinessError):
            mint_research_payload(
                key=self.key,
                generation=7,
                identity=self.identity,
                state="FAILED",
                failure="DATABASE",
            )

    def test_reader_rejects_absent_truncated_overlong_and_trailing_frames(self) -> None:
        valid = self._frame()
        attacks = (
            b"",
            valid[:-1],
            struct.pack("!I", MAX_READINESS_PAYLOAD_BYTES + 1),
            valid + b"x",
        )
        for raw in attacks:
            with self.subTest(size=len(raw)):
                read_descriptor, write_descriptor = os.pipe()
                reader = self._reader(read_descriptor)
                try:
                    if raw:
                        os.write(write_descriptor, raw)
                    os.close(write_descriptor)
                    write_descriptor = -1
                    with self.assertRaises(SyntheticReadinessError):
                        reader.poll()
                    self.assertTrue(reader.closed)
                finally:
                    if write_descriptor >= 0:
                        os.close(write_descriptor)
                    reader.close()

    def test_writer_emits_exactly_one_bound_frame(self) -> None:
        read_descriptor, write_descriptor = os.pipe()
        reader = self._reader(read_descriptor)
        with mock.patch(
            "procurement_os.staging_research_readiness.current_readiness_process_identity",
            return_value=self.identity,
        ):
            writer = SyntheticReadinessWriter(
                write_descriptor,
                key=self.key,
                generation=7,
            )
        writer.emit_ready(self.validation_identity)
        proof = reader.poll()
        self.assertIsNotNone(proof)
        assert proof is not None
        self.assertTrue(proof.ready)
        self.assertTrue(writer.closed)
        with self.assertRaisesRegex(SyntheticReadinessError, "already emitted"):
            writer.emit_failure("DATABASE")
        reader.close()

    def test_writer_rejects_nonpipe_and_read_endpoints_without_consuming_them(self) -> None:
        read_descriptor, write_descriptor = os.pipe()
        try:
            with self.assertRaisesRegex(SyntheticReadinessError, "descriptor differs"):
                SyntheticReadinessWriter(
                    read_descriptor,
                    key=self.key,
                    generation=7,
                )
            os.write(write_descriptor, b"x")
            self.assertEqual(os.read(read_descriptor, 1), b"x")
        finally:
            os.close(read_descriptor)
            os.close(write_descriptor)


if __name__ == "__main__":
    unittest.main()
