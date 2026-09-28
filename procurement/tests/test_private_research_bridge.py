"""Private real-source research bridge acceptance tests."""

from __future__ import annotations

import unittest


class PrivateResearchBridgeTests(unittest.TestCase):
    def test_private_research_bridge_has_distinct_review_only_contract(self):
        from procurement_os import private_research

        self.assertEqual(
            private_research.CONTRACT,
            "BUFFALO_PRIVATE_REAL_RESEARCH_WORKSPACE_V1",
        )
        self.assertEqual(
            private_research.AUTHORITY,
            "PRIVATE_REAL_SOURCE_REVIEW_ONLY",
        )
        self.assertFalse(private_research.OPERATIONAL_AUTHORITY)


if __name__ == "__main__":
    unittest.main()
