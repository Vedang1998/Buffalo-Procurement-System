"""Pure/static acceptance checks for the persistent-mapping authority boundary."""
from __future__ import annotations

import ast
from contextlib import ExitStack
from datetime import date
import hashlib
import io
import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from types import SimpleNamespace
import unittest
from unittest import mock
from uuid import uuid4

import psycopg


ROOT = Path(__file__).resolve().parents[2]
PROCUREMENT = ROOT / "procurement"
SERVICE = PROCUREMENT / "src" / "procurement_os" / "persistent_mapping.py"
MIGRATION = PROCUREMENT / "db" / "014_persistent_mapping_foundation.sql"
RULES = PROCUREMENT / "config" / "rules.toml"
CANONICAL_AUTHORITY = (
    PROCUREMENT / "docs" / "authority" / "01_CANONICAL_SYSTEM_SPEC_v2_1.md"
)
MASTER_PLAN = PROCUREMENT / "docs" / "MASTER_PLAN_v2_0.md"
CURRENT_AUTHORITY = PROCUREMENT / "docs" / "CURRENT_AUTHORITY.md"
IMPLEMENTATION_SPEC = (
    ROOT
    / "docs"
    / "superpowers"
    / "specs"
    / "2026-09-10-persistent-mapping-foundation-implementation-spec.md"
)
RUNNER_PATH = PROCUREMENT / "tools" / "run_tests.py"
EXPECTED_GLOBAL_TEST_POPULATION = 1295


class PersistentMappingFoundationContractTests(unittest.TestCase):
    def test_mapping_foundation_has_no_shopify_price_order_supplier_or_artifact_mutator(self):
        source = SERVICE.read_text(encoding="utf-8")
        self.assertEqual(
            hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "5c47db4e51af91c1d84f74462959143170670700a408deabd2709ffbb8ca76dd",
        )
        tree = ast.parse(source)
        import_contract = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                import_contract.update(
                    ("import", alias.name, alias.asname) for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                import_contract.update(
                    (
                        "from",
                        node.level,
                        node.module,
                        alias.name,
                        alias.asname,
                    )
                    for alias in node.names
                )
        self.assertEqual(
            import_contract,
            {
                ("import", "copy", None),
                ("import", "hashlib", None),
                ("import", "json", None),
                ("import", "os", None),
                ("import", "psycopg", None),
                ("import", "time", None),
                ("from", 0, "__future__", "annotations", None),
                ("from", 0, "collections.abc", "Callable", None),
                ("from", 0, "collections.abc", "Mapping", None),
                ("from", 0, "collections.abc", "Sequence", None),
                ("from", 0, "dataclasses", "dataclass", None),
                ("from", 0, "datetime", "date", None),
                ("from", 0, "datetime", "datetime", None),
                ("from", 0, "decimal", "Decimal", None),
                ("from", 0, "ipaddress", "ip_interface", None),
                ("from", 0, "psycopg", "sql", None),
                ("from", 0, "psycopg.rows", "dict_row", None),
                ("from", 0, "psycopg.types.json", "Jsonb", None),
                ("from", 0, "typing", "Any", None),
                ("from", 0, "typing", "TypeVar", None),
                ("from", 0, "urllib.parse", "parse_qsl", None),
                ("from", 0, "urllib.parse", "urlparse", None),
                ("from", 0, "uuid", "NAMESPACE_URL", None),
                ("from", 0, "uuid", "UUID", None),
                ("from", 0, "uuid", "uuid5", None),
                ("from", 1, "config", "load_rules", None),
                (
                    "from",
                    1,
                    "synthetic_mapping_packet",
                    "MULTIVENDOR_PACKET_CONTRACT",
                    None,
                ),
                (
                    "from",
                    1,
                    "synthetic_mapping_packet",
                    "MULTIVENDOR_PACKET_SHA256",
                    None,
                ),
                (
                    "from",
                    1,
                    "synthetic_mapping_packet",
                    "PACKET_CONTRACT",
                    None,
                ),
                (
                    "from",
                    1,
                    "synthetic_mapping_packet",
                    "PACKET_SHA256",
                    None,
                ),
            },
        )
        self.assertEqual(
            sorted(
                (node.attr, node.lineno)
                for node in ast.walk(tree)
                if isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "os"
            ),
            [("getenv", 73), ("getenv", 74)],
        )
        for forbidden_builtin in (
            "open",
            "eval",
            "exec",
            "compile",
            "__import__",
        ):
            self.assertFalse(
                any(
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == forbidden_builtin
                    for node in ast.walk(tree)
                )
            )
        imported_roots = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertTrue(
            imported_roots.isdisjoint(
                {
                    "shopify",
                    "draft_po",
                    "recommendations",
                    "emergency_packet",
                    "httpx",
                    "requests",
                    "subprocess",
                }
            )
        )
        allowed_mapping_relations = {
            "mapping_rejections",
            "supplier_mapping_decisions",
            "supplier_mapping_review_batches",
            "supplier_mapping_review_candidates",
            "supplier_offer_selection_events",
            "supplier_offer_selection_heads",
            "supplier_offers",
            "v_effective_supplier_mapping_decisions",
            "v_supplier_offer_selection_shadow",
        }
        forbidden_operational_relations = {
            "prices",
            "price_book_batches",
            "purchase_orders",
            "purchase_order_lines",
            "procurement_recommendations",
            "monday_run_artifacts",
            "monday_packet_build_events",
            "po_operational_events",
            "po_reconciliation_events",
        }
        for relation in forbidden_operational_relations:
            self.assertIsNone(
                re.search(
                    rf"(?<![A-Za-z0-9_]){re.escape(relation)}(?![A-Za-z0-9_])",
                    source,
                ),
                relation,
            )
        dynamic_qualified_helpers = {
            "_composite_value",
            "_idempotency_row_exists",
            "_insert_record",
            "_project_record",
        }
        relation_helper_calls = {
            "_composite_value",
            "_idempotency_row_exists",
            "_insert_record",
            "_project_record",
        }
        allowed_record_relations = {
            "supplier_mapping_decisions",
            "supplier_mapping_review_batches",
            "supplier_mapping_review_candidates",
            "supplier_offer_selection_events",
        }
        supplier_offer_qualified_functions = []
        functions = [
            node for node in tree.body if isinstance(node, ast.FunctionDef)
        ]
        for function in (
            node for node in functions
        ):
            for call in (
                node for node in ast.walk(function) if isinstance(node, ast.Call)
            ):
                if isinstance(call.func, ast.Name) and call.func.id == "_qualified":
                    self.assertEqual(len(call.args), 1)
                    argument = call.args[0]
                    if isinstance(argument, ast.Constant):
                        self.assertIn(argument.value, allowed_mapping_relations)
                        if argument.value == "supplier_offers":
                            supplier_offer_qualified_functions.append(function.name)
                    else:
                        self.assertIn(function.name, dynamic_qualified_helpers)
                        self.assertIsInstance(argument, ast.Name)
                        self.assertEqual(argument.id, "table")
                if (
                    isinstance(call.func, ast.Name)
                    and call.func.id in relation_helper_calls
                ):
                    table_keywords = [
                        keyword
                        for keyword in call.keywords
                        if keyword.arg == "table"
                    ]
                    self.assertEqual(len(table_keywords), 1)
                    self.assertIsInstance(table_keywords[0].value, ast.Constant)
                    self.assertIn(
                        table_keywords[0].value.value,
                        allowed_record_relations,
                    )
        self.assertEqual(
            supplier_offer_qualified_functions,
            [
                "_insert_created_inactive_offer",
                "record_mapping_decision",
                "get_mapping_candidate_detail",
            ],
        )
        insert_offer_function = next(
            function
            for function in functions
            if function.name == "_insert_created_inactive_offer"
        )
        insert_offer_sql = [
            node.value
            for node in ast.walk(insert_offer_function)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("INSERT INTO")
        ]
        self.assertEqual(
            insert_offer_sql,
            [
                "INSERT INTO {}(variant_id,vendor_id,supplier_sku,package_type,"
                "size_text,raw_pack,shopify_units_per_case,qualifying_units_per_case,"
                "assortment_scope,assortment_group,assortable,valid_from,valid_to,"
                "replaces_offer_id,source_file,source_page,confidence,active) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,NULL,NULL,%s,%s,"
                "'VERIFIED',false) RETURNING offer_id"
            ],
        )
        self.assertEqual(
            sum(
                1
                for node in ast.walk(insert_offer_function)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute"
            ),
            1,
        )
        for function_name in (
            "record_mapping_decision",
            "get_mapping_candidate_detail",
        ):
            function = next(
                item for item in functions if item.name == function_name
            )
            sql_literals = [
                node.value.strip().upper()
                for node in ast.walk(function)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            ]
            self.assertFalse(
                any(
                    value.startswith(("INSERT ", "UPDATE ", "DELETE "))
                    for value in sql_literals
                )
            )
        for literal in (
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ):
            self.assertIsNone(
                re.search(
                    r"(?is)\b(?:insert\s+into|update|delete\s+from)\b[^;]{0,160}"
                    r"\bsupplier_offers\b",
                    literal,
                )
            )
        write_sql_literals = sorted(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and re.search(
                r"\bINSERT\s+INTO\b|\bDELETE\s+FROM\b|"
                r"\bUPDATE\s+(?=[A-Za-z_\"{])",
                node.value,
                re.IGNORECASE,
            )
        )
        self.assertEqual(len(write_sql_literals), 6)
        self.assertEqual(
            hashlib.sha256("\0".join(write_sql_literals).encode("utf-8")).hexdigest(),
            "5a1e068fb542adb28ab2028d8277707813db52c648489cf75409fb14172064c6",
        )
        for forbidden in (
            "shopify_client.",
            "build_vendor_drafts(",
            "prepare_monday_run(",
            "send_supplier",
            "release_purchase_order",
        ):
            self.assertNotIn(forbidden, source)
        sql_source = MIGRATION.read_text(encoding="utf-8").lower()
        for forbidden in (
            "insert into \"qa_mapping_test\".prices",
            "insert into \"qa_mapping_test\".purchase_orders",
            "insert into \"qa_mapping_test\".procurement_recommendations",
            "update \"qa_mapping_test\".prices set",
            "update \"qa_mapping_test\".supplier_offers set active",
        ):
            self.assertNotIn(forbidden, sql_source)

        import procurement_os.persistent_mapping as mapping_service
        from procurement_os import draft_po
        from procurement_os import emergency_packet
        from procurement_os import po_ledger
        from procurement_os import price_book
        from procurement_os import recommendations as recommendation_service
        from procurement_os import storage
        from procurement_os.shopify.graphql import ShopifyGraphQLClient

        external_calls: list[str] = []

        def forbidden_call(name):
            def invoke(*_args, **_kwargs):
                external_calls.append(name)
                raise AssertionError(f"external mutator was called: {name}")

            return invoke

        class Result:
            def __init__(self, value=True):
                self.value = value

            def fetchone(self):
                return self.value if isinstance(self.value, tuple) else (self.value,)

        class Info:
            transaction_status = type("Status", (), {"name": "IDLE"})()

        class Connection:
            def __init__(self, *, commit_error=None):
                self.commit_error = commit_error
                self.closed = False
                self.info = Info()
                self.statements: list[str] = []

            def execute(self, statement, _parameters=()):
                rendered = str(statement)
                self.statements.append(rendered)
                if "current_database" in rendered:
                    return Result(
                        (
                            "procurement_test",
                            "127.0.0.1/32",
                            160009,
                            "qa_release_login",
                            "qa_mapping_owner",
                        )
                    )
                return Result(True)

            def commit(self):
                if self.commit_error is not None:
                    error, self.commit_error = self.commit_error, None
                    raise error

            def rollback(self):
                return None

            def close(self):
                self.closed = True

        principal = mapping_service.Principal(
            "synthetic:pure-boundary:01",
            "procurement.mapping.approve",
            "a" * 64,
        )
        connections: list[Connection] = []

        def tracked_connection(*, commit_error=None) -> Connection:
            connection = Connection(commit_error=commit_error)
            connections.append(connection)
            return connection

        def run(*, operation, recovery_lookup=None, connect_side_effect=None):
            side_effect = connect_side_effect or [Connection()]
            connections.extend(side_effect)
            with mock.patch.object(psycopg, "connect", side_effect=side_effect):
                return mapping_service._execute_write(
                    "postgresql://qa_release_login@127.0.0.1:5432/"
                    "procurement_test?options=-c%20role%3Dqa_mapping_owner%20-c%20"
                    "search_path%3Dqa_mapping_test,pg_catalog",
                    operation_name="pure-zero-external-effects",
                    capability="human_mapping_writes_enabled",
                    principal=principal,
                    idempotency_key=uuid4(),
                    domain_locks=lambda _conn: ((0, "pure-domain"),),
                    operation=operation,
                    recovery_lookup=recovery_lookup,
                )

        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(
                    mapping_service,
                    "require_synthetic_mapping_capability",
                    return_value=None,
                )
            )
            for owner, attribute, label in (
                (ShopifyGraphQLClient, "query", "shopify_graphql"),
                (price_book, "promote_price_book_batch", "price_promotion"),
                (
                    recommendation_service,
                    "prepare_monday_run",
                    "recommendation_prepare",
                ),
                (draft_po, "build_vendor_drafts", "draft_po_build"),
                (
                    emergency_packet,
                    "build_emergency_review_packet",
                    "artifact_packet_build",
                ),
                (po_ledger, "finalize_reviewed_po", "po_finalization"),
                (po_ledger, "record_po_import_status", "po_import_status"),
                (storage.LocalFilesystemStorage, "put_bytes", "artifact_write"),
            ):
                stack.enter_context(
                    mock.patch.object(
                        owner, attribute, forbidden_call(label)
                    )
                )
            stack.enter_context(
                mock.patch.object(
                    Path, "write_bytes", forbidden_call("file_write_bytes")
                )
            )
            stack.enter_context(
                mock.patch.object(
                    Path, "write_text", forbidden_call("file_write_text")
                )
            )
            stack.enter_context(
                mock.patch.object(
                    subprocess, "run", forbidden_call("subprocess_run")
                )
            )

            self.assertEqual(run(operation=lambda _conn: {"ok": True}), {"ok": True})
            self.assertEqual(
                run(operation=lambda _conn: {"replayed": True}),
                {"replayed": True},
            )
            with self.assertRaises(mapping_service.PersistentMappingError):
                run(
                    operation=lambda _conn: (_ for _ in ()).throw(
                        mapping_service.PersistentMappingError("validation refused")
                    )
                )
            retry_calls: list[bool] = []

            def retry_once(_conn):
                retry_calls.append(True)
                if len(retry_calls) == 1:
                    raise psycopg.errors.SerializationFailure("pure retry")
                return {"retried": True}

            self.assertEqual(run(operation=retry_once), {"retried": True})
            first = Connection(commit_error=psycopg.OperationalError("lost commit"))
            recovered_connection = Connection()
            self.assertEqual(
                run(
                    operation=lambda _conn: {"created": True},
                    recovery_lookup=lambda _conn: {"replayed": True},
                    connect_side_effect=[first, recovered_connection],
                ),
                {"replayed": True},
            )
            unknown_first = Connection(
                commit_error=psycopg.OperationalError("lost commit")
            )
            unknown_recovery = Connection()
            with self.assertRaises(mapping_service.PersistentMappingError) as unknown:
                run(
                    operation=lambda _conn: {"created": True},
                    connect_side_effect=[unknown_first, unknown_recovery],
                )
            self.assertEqual(unknown.exception.code, "COMMIT_OUTCOME_UNKNOWN")

            database_url = (
                "postgresql://qa_release_login@127.0.0.1:5432/"
                "procurement_test?options=-c%20role%3Dqa_mapping_owner%20-c%20"
                "search_path%3Dqa_mapping_test,pg_catalog"
            )
            with (
                mock.patch.object(
                    mapping_service, "_validate_supported_intake", return_value=None
                ),
                mock.patch.object(
                    mapping_service,
                    "intake_supplier_mapping_review",
                    return_value={"path": "public-intake-success"},
                ),
                mock.patch.object(
                    psycopg, "connect", return_value=tracked_connection()
                ),
            ):
                self.assertEqual(
                    mapping_service.execute_supplier_mapping_intake(
                        database_url,
                        package={},
                        candidates=(),
                        principal=principal,
                        intake_idempotency_key=uuid4(),
                    ),
                    {"path": "public-intake-success"},
                )

            with (
                mock.patch.object(
                    mapping_service, "_mapping_domain_locks", return_value=()
                ),
                mock.patch.object(
                    mapping_service,
                    "record_mapping_decision",
                    side_effect=mapping_service.PersistentMappingError(
                        "public mapping refusal"
                    ),
                ),
                mock.patch.object(
                    psycopg, "connect", return_value=tracked_connection()
                ),
                self.assertRaises(mapping_service.PersistentMappingError),
            ):
                mapping_service.execute_mapping_decision(
                    database_url,
                    candidate_id=uuid4(),
                    action="DEFER",
                    reason="public error path",
                    principal=principal,
                    decision_idempotency_key=uuid4(),
                    expected_preview_sha256="a" * 64,
                )

            selection_attempts: list[bool] = []

            def public_selection_retry(_conn, **_kwargs):
                selection_attempts.append(True)
                if len(selection_attempts) == 1:
                    raise psycopg.errors.SerializationFailure("public retry")
                return {"path": "public-selection-retry"}

            with (
                mock.patch.object(
                    mapping_service, "_selection_domain_locks", return_value=()
                ),
                mock.patch.object(
                    mapping_service,
                    "record_routine_offer_selection",
                    side_effect=public_selection_retry,
                ),
                mock.patch.object(
                    psycopg, "connect", return_value=tracked_connection()
                ),
            ):
                self.assertEqual(
                    mapping_service.execute_routine_offer_selection(
                        database_url,
                        mapping_decision_id=uuid4(),
                        principal=principal,
                        selection_idempotency_key=uuid4(),
                        reason="public retry path",
                        effective_from=date(2026, 9, 13),
                        expected_preview_sha256="b" * 64,
                    ),
                    {"path": "public-selection-retry"},
                )
            self.assertEqual(len(selection_attempts), 2)

            with (
                mock.patch.object(
                    mapping_service, "_selection_domain_locks", return_value=()
                ),
                mock.patch.object(
                    mapping_service,
                    "record_routine_offer_clear",
                    return_value={"path": "public-clear-replay", "replayed": True},
                ),
                mock.patch.object(
                    psycopg, "connect", return_value=tracked_connection()
                ),
            ):
                self.assertEqual(
                    mapping_service.execute_routine_offer_clear(
                        database_url,
                        variant_id="1001",
                        principal=principal,
                        selection_idempotency_key=uuid4(),
                        reason="public replay path",
                        effective_from=date(2026, 9, 13),
                        expected_preview_sha256="c" * 64,
                    )["path"],
                    "public-clear-replay",
                )

            recovered_workers = [
                {"path": "public-mapping-created"},
                {"path": "public-mapping-recovered", "replayed": True},
            ]
            with (
                mock.patch.object(
                    mapping_service, "_mapping_domain_locks", return_value=()
                ),
                mock.patch.object(
                    mapping_service,
                    "record_mapping_decision",
                    side_effect=recovered_workers,
                ),
                mock.patch.object(
                    mapping_service, "_idempotency_row_exists", return_value=True
                ),
                mock.patch.object(
                    psycopg,
                    "connect",
                    side_effect=[
                        tracked_connection(
                            commit_error=psycopg.OperationalError("lost commit")
                        ),
                        tracked_connection(),
                    ],
                ),
            ):
                self.assertEqual(
                    mapping_service.execute_mapping_decision(
                        database_url,
                        candidate_id=uuid4(),
                        action="DEFER",
                        reason="public recovered commit path",
                        principal=principal,
                        decision_idempotency_key=uuid4(),
                        expected_preview_sha256="d" * 64,
                    ),
                    {"path": "public-mapping-recovered", "replayed": True},
                )

            unknown_worker = mock.Mock(return_value={"path": "unknown-first-effect"})
            with (
                mock.patch.object(
                    mapping_service, "_mapping_domain_locks", return_value=()
                ),
                mock.patch.object(
                    mapping_service,
                    "record_mapping_decision",
                    unknown_worker,
                ),
                mock.patch.object(
                    mapping_service,
                    "_idempotency_row_exists",
                    side_effect=psycopg.OperationalError("recovery lookup unavailable"),
                ),
                mock.patch.object(
                    psycopg,
                    "connect",
                    side_effect=[
                        tracked_connection(
                            commit_error=psycopg.OperationalError("lost commit")
                        ),
                        tracked_connection(),
                    ],
                ),
                self.assertRaises(mapping_service.PersistentMappingError) as unknown,
            ):
                mapping_service.execute_mapping_decision(
                    database_url,
                    candidate_id=uuid4(),
                    action="DEFER",
                    reason="public unknown outcome path",
                    principal=principal,
                    decision_idempotency_key=uuid4(),
                    expected_preview_sha256="e" * 64,
                )
            self.assertEqual(unknown.exception.code, "COMMIT_OUTCOME_UNKNOWN")
            self.assertEqual(unknown_worker.call_count, 1)

        self.assertEqual(external_calls, [])
        recorded_sql = "\n".join(
            statement.lower()
            for connection in connections
            for statement in connection.statements
        )
        for forbidden in (
            "insert into prices",
            "update prices",
            "purchase_orders",
            "procurement_recommendations",
            "monday_run_artifacts",
        ):
            self.assertNotIn(forbidden, recorded_sql)

    def test_mapping_authority_and_disabled_flags_match_approved_contract(self):
        def exact_between(text: str, start: str, end: str) -> str:
            self.assertEqual(text.count(start), 1)
            remainder = text.split(start, 1)[1]
            self.assertEqual(remainder.count(end), 1)
            return remainder.split(end, 1)[0]

        canonical = CANONICAL_AUTHORITY.read_text(encoding="utf-8")
        master = MASTER_PLAN.read_text(encoding="utf-8")
        specification = IMPLEMENTATION_SPEC.read_text(encoding="utf-8")
        proposal_start = "```diff\n+Persistent supplier mapping authority\n"
        proposal = "+Persistent supplier mapping authority\n" + exact_between(
            specification,
            proposal_start,
            "```\n\nDo not change canonical section 11's price lifecycle",
        )
        proposal_lines = proposal.splitlines()
        self.assertTrue(proposal_lines)
        self.assertTrue(all(line.startswith("+") for line in proposal_lines))
        expected_authority = "\n".join(line[1:] for line in proposal_lines)
        expected_heading, expected_body = expected_authority.split("\n\n", 1)
        self.assertEqual(expected_heading, "Persistent supplier mapping authority")
        self.assertEqual(
            hashlib.sha256(expected_body.encode("utf-8")).hexdigest(),
            "80696a01262b76e52e89c5ffaa4fcf9275478fb5e342f464c2184d6c47978e73",
        )
        canonical_body = exact_between(
            canonical,
            "G. Human intelligence that must NEVER disappear\n\n"
            "Persistent supplier mapping authority\n\n",
            "\n\nThe following are first-class structured concepts, not informal notes:",
        )
        master_body = exact_between(
            master,
            "### Chat history is never system memory\n\n"
            "A deleted chat must never erase an operational rule, accepted mapping "
            "or human explanation.\n\n"
            "### Persistent supplier mapping authority\n\n",
            "\n\n---\n\n## 4. Shopify application connection",
        )
        self.assertEqual(canonical_body, expected_body)
        self.assertEqual(master_body, expected_body)

        expected_current_authority = (
            "# Current Authority\n\n"
            "For this production build, use these sources in this order:\n\n"
            "1. `docs/MASTER_PLAN_v2_0.md` — current complete product/strategy "
            "specification.\n"
            "2. `config/rules.toml` — machine-enforced operating rules.\n"
            "3. current database schema/migrations and tested code.\n"
            "4. `docs/ACCEPTED_ARCHITECTURE_2026-08-09.md` — architecture-review "
            "decisions.\n"
            "5. August seed CSVs — historical data fixtures/evidence only.\n\n"
            "Older Master Plans, the v0.1 README, and deleted-chat reconstructions "
            "are **historical evidence, not current implementation authority**. "
            "They are intentionally not bundled as competing specifications in "
            "this production package.\n"
        )
        current_authority = CURRENT_AUTHORITY.read_text(encoding="utf-8")
        self.assertEqual(current_authority, expected_current_authority)
        self.assertEqual(
            hashlib.sha256(current_authority.encode("utf-8")).hexdigest(),
            "360ca9404e451fbf6d7441713d5b1f7eb02ab40b42d5a8c08996400ab1337a04",
        )

        rules_source = RULES.read_text(encoding="utf-8")
        rules = tomllib.loads(rules_source)
        self.assertEqual(
            rules["matching"],
            {
                "sku_first": True,
                "accepted_alias_second": True,
                "negative_mapping_memory": True,
                "auto_match_min_score": 0.92,
                "review_min_score": 0.82,
                "size_conflict_blocks_auto_match": True,
                "pack_conflict_blocks_auto_match": True,
                "fuzzy_is_supporting_evidence_only": True,
                "fuzzy_can_authorize": False,
            },
        )
        self.assertEqual(
            rules["persistent_mapping"],
            {
                "contract_version": "v1-shadow-only",
                "routine_selection_scope": "ROUTINE_PROCUREMENT_STANDARD",
                "review_intake_writes_enabled": False,
                "human_mapping_writes_enabled": False,
                "policy_mapping_writes_enabled": False,
                "routine_selection_writes_enabled": False,
                "selected_offer_shadow_reads_enabled": False,
                "synthetic_selected_offer_inputs_enabled": False,
                "recommendation_cutover_enabled": False,
                "offer_activation_enabled": False,
            },
        )
        self.assertIs(rules["strategy"]["selling_price_auto_update"], False)
        self.assertIs(rules["strategy"]["one_vendor_one_po"], True)
        self.assertIs(rules["strategy"]["human_review_before_final_po"], True)
        self.assertEqual(
            rules["shopify"],
            {
                "canonical_identity": "variant_id",
                "authentication": "merchant_owned_client_credentials",
                "api_version": "2026-07",
                "prefer_shopifyql_sales_backfill": True,
                "own_daily_inventory_snapshots": True,
                "po_output": "native_purchase_order_csv",
                "auto_write_supplier_sku_to_shopify": False,
            },
        )
        self.assertEqual(
            rules["pricing"],
            {
                "states": ["current", "future"],
                "archive_enabled": False,
                "rollover_day": 1,
                "future_upload_day_start": 15,
                "future_upload_day_end": 20,
                "delete_old_current_on_rollover": True,
                "future_price_recheck_each_monday": True,
                "minor_cent_difference_is_blocker": False,
                "strict_normalized_import_contract": True,
                "staging_required": True,
                "transactional_promotion_required": True,
                "run_price_snapshot_for_reproducibility": True,
                "synthetic_price_replacement_enabled": False,
            },
        )
        self.assertEqual(
            rules["vendor_minimums"],
            {
                "block_po": False,
                "auto_add_filler_to_reach_minimum": False,
                "flag_shortfall": True,
            },
        )
        self.assertEqual(
            rules["po"],
            {
                "official_shopify_csv_target": True,
                "routine_line_comments": False,
                "strategic_line_comments": True,
                "procurement_po_ledger_required": True,
                "one_vendor_one_po": True,
            },
        )
        for later_key in (
            "delete_old_current_on_approved_replacement",
            "retain_current_when_replacement_missing",
            "supplier_deal_overlays",
            "deal_overlays",
            "price_scope_authority",
            "carry_forward",
        ):
            self.assertNotIn(later_key, rules_source)
            self.assertNotIn(later_key, rules)
        approved_rules_addition = (
            "fuzzy_can_authorize = false\n\n"
            "[persistent_mapping]\n"
            'contract_version = "v1-shadow-only"\n'
            'routine_selection_scope = "ROUTINE_PROCUREMENT_STANDARD"\n'
            "review_intake_writes_enabled = false\n"
            "human_mapping_writes_enabled = false\n"
            "policy_mapping_writes_enabled = false\n"
            "routine_selection_writes_enabled = false\n"
            "selected_offer_shadow_reads_enabled = false\n"
            "synthetic_selected_offer_inputs_enabled = false\n"
            "recommendation_cutover_enabled = false\n"
            "offer_activation_enabled = false\n\n"
        )
        self.assertEqual(rules_source.count(approved_rules_addition), 1)
        approved_pricing_addition = "synthetic_price_replacement_enabled = false\n"
        self.assertEqual(rules_source.count(approved_pricing_addition), 1)
        predecessor_rules = rules_source.replace(
            approved_rules_addition, "\n"
        ).replace(approved_pricing_addition, "")
        self.assertEqual(
            hashlib.sha256(predecessor_rules.encode("utf-8")).hexdigest(),
            "43e94dc4db8b8c7d7927a4e5f9a3c8cd11814ff71329764c79f41efbdfe05c5c",
        )
        migration = MIGRATION.read_text(encoding="utf-8")
        self.assertEqual(migration.count("CREATE TABLE IF NOT EXISTS \"qa_mapping_test\".supplier_mapping_"), 3)
        self.assertIn(
            'CREATE TABLE IF NOT EXISTS "qa_mapping_test".supplier_offer_selection_events',
            migration,
        )
        self.assertIn(
            'CREATE TABLE IF NOT EXISTS "qa_mapping_test".supplier_offer_selection_heads',
            migration,
        )
        self.assertEqual(migration.count('CREATE OR REPLACE VIEW "qa_mapping_test".'), 4)
        self.assertIn("recommendation_cutover_enabled", migration)

    def test_persistent_mapping_modules_are_registered_at_exact_discovery_floors(self):
        spec = importlib.util.spec_from_file_location("mapping_matrix_runner", RUNNER_PATH)
        assert spec is not None and spec.loader is not None
        runner = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = runner
        spec.loader.exec_module(runner)
        self.assertEqual(
            runner.REQUIRED_MODULE_MINIMUMS["test_persistent_mapping_foundation_postgres.py"],
            39,
        )
        self.assertEqual(
            runner.REQUIRED_MODULE_MINIMUMS["test_persistent_mapping_foundation_contract.py"],
            3,
        )
        self.assertEqual(runner.GLOBAL_MINIMUM_TESTS, EXPECTED_GLOBAL_TEST_POPULATION)
        self.assertEqual(
            sum(runner.REQUIRED_MODULE_MINIMUMS.values()),
            EXPECTED_GLOBAL_TEST_POPULATION,
        )
        tests_dir = PROCUREMENT / "tests"
        suite = unittest.defaultTestLoader.discover(
            start_dir=str(tests_dir),
            pattern="test_*.py",
            top_level_dir=str(tests_dir),
        )
        actual = runner._module_test_counts(suite)
        self.assertEqual(suite.countTestCases(), EXPECTED_GLOBAL_TEST_POPULATION)
        self.assertEqual(
            actual["test_persistent_mapping_foundation_postgres.py"],
            39,
        )
        self.assertEqual(
            actual["test_persistent_mapping_foundation_contract.py"],
            3,
        )
        self.assertEqual(runner._module_registration_errors(tests_dir), [])
        self.assertEqual(runner._module_minimum_errors(actual), [])

        all_tests = list(runner._iter_tests(suite))
        for module, required in (
            ("test_persistent_mapping_foundation_postgres.py", 39),
            ("test_persistent_mapping_foundation_contract.py", 3),
        ):
            removed = False
            retained = []
            for test in all_tests:
                test_module = f"{test.id().split('.', 1)[0]}.py"
                if test_module == module and not removed:
                    removed = True
                    continue
                retained.append(test)
            self.assertTrue(removed)
            reduced_suite = unittest.TestSuite(retained)
            reduced_counts = runner._module_test_counts(reduced_suite)
            self.assertEqual(reduced_counts[module], required - 1)
            self.assertEqual(
                runner._module_minimum_errors(reduced_counts),
                [
                    f"{module} discovered {required - 1} tests; "
                    f"required minimum is {required}"
                ],
            )
            self.assertEqual(
                reduced_suite.countTestCases(),
                EXPECTED_GLOBAL_TEST_POPULATION - 1,
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    runner,
                    "_validated_test_database_target",
                    return_value=SimpleNamespace(
                        url="postgresql://runner@127.0.0.1:5432/procurement_test"
                    ),
                ),
                mock.patch.object(runner, "_clear_libpq_environment"),
                mock.patch.object(
                    runner,
                    "_validate_test_database",
                    return_value=SimpleNamespace(
                        server_version="16.9",
                        database="procurement_test",
                        server_address="127.0.0.1",
                    ),
                ),
                mock.patch.object(
                    runner.unittest.defaultTestLoader,
                    "discover",
                    return_value=reduced_suite,
                ),
                mock.patch.object(runner.unittest.TextTestRunner, "run") as run_tests,
                mock.patch.object(runner.sys, "stdout", stdout),
                mock.patch.object(runner.sys, "stderr", stderr),
                mock.patch.object(runner.sys, "path", list(runner.sys.path)),
                mock.patch.dict(runner.os.environ, {}, clear=False),
            ):
                self.assertEqual(runner.main(), 2)
            run_tests.assert_not_called()
            self.assertEqual(
                stderr.getvalue().splitlines(),
                [
                    f"ERROR: {module} discovered {required - 1} tests; "
                    f"required minimum is {required}",
                    f"ERROR: global discovered count "
                    f"{EXPECTED_GLOBAL_TEST_POPULATION - 1} is below floor "
                    f"{EXPECTED_GLOBAL_TEST_POPULATION}",
                ],
            )


if __name__ == "__main__":
    unittest.main()
