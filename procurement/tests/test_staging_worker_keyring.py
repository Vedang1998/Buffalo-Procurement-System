"""Gateway worker-key activation state tests."""
from __future__ import annotations

import unittest

from procurement_os.staging_worker_types import WorkerKeyring, WorkerKeyState


SYNTHETIC_KEY = bytes.fromhex("11" * 32)
RESEARCH_KEY = bytes.fromhex("22" * 32)


class WorkerKeyringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.keyring = WorkerKeyring(
            {"synthetic": SYNTHETIC_KEY, "research": RESEARCH_KEY},
            active_roles=frozenset(),
        )

    def test_loaded_generation_is_disabled_until_prepare_and_commit(self) -> None:
        self.assertEqual(
            self.keyring.status("research").state,
            WorkerKeyState.DISABLED,
        )
        with self.assertRaisesRegex(ValueError, "not active"):
            self.keyring.current("research")

        self.keyring.prepare(role="research", key=RESEARCH_KEY, generation=0)
        self.assertEqual(
            self.keyring.status("research").state,
            WorkerKeyState.PENDING,
        )
        with self.assertRaisesRegex(ValueError, "not active"):
            self.keyring.current("research")

        self.keyring.commit(role="research", generation=0)
        self.assertEqual(self.keyring.current("research"), (RESEARCH_KEY, 0))

    def test_disable_is_exact_and_idempotent_before_process_stop(self) -> None:
        self.keyring.prepare(role="research", key=RESEARCH_KEY, generation=0)
        self.keyring.commit(role="research", generation=0)
        self.keyring.disable(role="research", generation=0)
        self.keyring.disable(role="research", generation=0)
        self.assertEqual(
            self.keyring.status("research").state,
            WorkerKeyState.DISABLED,
        )
        with self.assertRaisesRegex(ValueError, "not pending"):
            self.keyring.commit(role="research", generation=0)
        with self.assertRaisesRegex(ValueError, "differs"):
            self.keyring.disable(role="research", generation=1)
        with self.assertRaisesRegex(ValueError, "not newer"):
            self.keyring.prepare(role="research", key=RESEARCH_KEY, generation=0)

    def test_prepare_requires_disabled_state_and_never_reuses_any_role_key(self) -> None:
        self.keyring.prepare(role="research", key=RESEARCH_KEY, generation=0)
        with self.assertRaisesRegex(ValueError, "disabled"):
            self.keyring.prepare(
                role="research", key=bytes.fromhex("33" * 32), generation=1
            )
        self.keyring.disable(role="research", generation=0)
        with self.assertRaisesRegex(ValueError, "never be reused"):
            self.keyring.prepare(
                role="research", key=SYNTHETIC_KEY, generation=1
            )

    def test_burned_generations_may_be_skipped_but_never_rolled_back(self) -> None:
        replacement = bytes.fromhex("33" * 32)
        self.keyring.prepare(role="research", key=replacement, generation=3)
        self.keyring.commit(role="research", generation=3)
        self.assertEqual(self.keyring.current("research"), (replacement, 3))
        self.keyring.disable(role="research", generation=3)
        with self.assertRaisesRegex(ValueError, "not newer"):
            self.keyring.prepare(
                role="research", key=bytes.fromhex("44" * 32), generation=2
            )

    def test_status_and_repr_never_contain_key_material(self) -> None:
        status = self.keyring.status("synthetic")
        self.assertEqual((status.role, status.generation), ("synthetic", 0))
        self.assertNotIn(SYNTHETIC_KEY.hex(), repr(status))
        with self.assertRaisesRegex(ValueError, "role"):
            self.keyring.status("unknown")


if __name__ == "__main__":
    unittest.main()
