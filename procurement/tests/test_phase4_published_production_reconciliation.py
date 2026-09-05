"""Corrective Phase 4 published-production executor and safety tests."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timedelta
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import MagicMock, patch
import uuid


PROCUREMENT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROCUREMENT_ROOT.parent
sys.path.insert(0, str(PROCUREMENT_ROOT / "src"))
sys.path.insert(0, str(PROCUREMENT_ROOT / "tools"))

import reconcile_phase4_published_production as corrective
import test_phase4_terminal_disposition_postgres as terminal_fixture_module

from procurement_os.historical_sales_manifest import protected_state_fingerprints
from procurement_os.historical_sales_terminal import (
    ExecutionGitIdentity,
    derive_execution_git_identity,
)


RUNNER_PATH = PROCUREMENT_ROOT / "tools" / "run_tests.py"
RUNNER_SPEC = importlib.util.spec_from_file_location(
    "phase4_corrective_test_database_runner_contract", RUNNER_PATH
)
assert RUNNER_SPEC is not None and RUNNER_SPEC.loader is not None
test_runner = importlib.util.module_from_spec(RUNNER_SPEC)
sys.modules[RUNNER_SPEC.name] = test_runner
RUNNER_SPEC.loader.exec_module(test_runner)

DB_DIR = PROCUREMENT_ROOT / "db"
BOOTSTRAP = REPOSITORY_ROOT / "scripts" / "phase4-published-production-bootstrap.sh"
PRE_007_MIGRATIONS = (
    "schema_postgres.sql",
    "001_v1_3_catalog_sales.sql",
    "002_seed_import_records.sql",
    "003_phase3_reconciliation.sql",
    "004_identity_decision_invariants.sql",
    "005_identity_investigation.sql",
    "006_phase4_sales_backfill.sql",
)
SAFE_TEST_URL = "postgresql://test:test@127.0.0.1:5432/procurement_test"
EXECUTION_SHA = "a" * 40
TREE_SHA = "b" * 40


def validated_test_connection():
    """Use only the authoritative TEST_DATABASE_URL safety contract."""

    target = test_runner._validated_test_database_target()
    test_runner._clear_libpq_environment()

    import psycopg

    connection = psycopg.connect(target.url, connect_timeout=5)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT current_database(),current_setting('server_version'),
                          current_setting('server_version_num')::integer"""
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError("PostgreSQL identity query returned no row")
        database_info = test_runner._validate_database_facts(
            target,
            database=row[0],
            server_version=row[1],
            server_version_num=row[2],
        )
    except BaseException:
        connection.close()
        raise
    return connection, target, database_info


def run_isolated_real_git(arguments):
    with corrective._isolated_git_subprocess_environment():
        return subprocess.run(
            arguments,
            check=True,
            capture_output=True,
            text=True,
        )


class CorrectivePreconnectionSafetyTests(unittest.TestCase):
    def safe_environment(self) -> dict[str, str]:
        return {
            "REPLIT_DEPLOYMENT": "1",
            "DATABASE_URL": "postgresql://published.invalid/neondb",
            "RECONCILIATION_REVIEW_TOKEN": "synthetic-review-token",
            "PHASE4_REVIEW_TOKEN_INPUT": "synthetic-review-token",
        }

    def argv(self, *, sha: str = EXECUTION_SHA, tree: str = TREE_SHA) -> list[str]:
        return [
            "--expected-execution-git-sha",
            sha,
            "--expected-execution-tree-sha",
            tree,
        ]

    def assert_rejected_before_connect(
        self, environment: dict[str, str], *, expected: str | None = None
    ) -> None:
        with patch.object(corrective, "connect_database") as connect:
            if expected is None:
                with self.assertRaises(Exception):
                    corrective.execute(self.argv(), environ=environment)
            else:
                with self.assertRaisesRegex(Exception, expected):
                    corrective.execute(self.argv(), environ=environment)
        connect.assert_not_called()

    def test_missing_deployment_marker_fails_before_connection(self):
        environment = self.safe_environment()
        environment.pop("REPLIT_DEPLOYMENT")
        self.assert_rejected_before_connect(environment, expected="Replit deployment")

    def test_missing_database_url_fails_before_connection(self):
        environment = self.safe_environment()
        environment.pop("DATABASE_URL")
        self.assert_rejected_before_connect(environment, expected="configuration")

    def test_missing_configured_token_fails_before_connection(self):
        environment = self.safe_environment()
        environment.pop("RECONCILIATION_REVIEW_TOKEN")
        self.assert_rejected_before_connect(environment, expected="not configured")

    def test_missing_supplied_token_fails_before_connection(self):
        environment = self.safe_environment()
        environment.pop("PHASE4_REVIEW_TOKEN_INPUT")
        self.assert_rejected_before_connect(environment, expected="input is unavailable")

    def test_wrong_token_fails_before_connection(self):
        environment = self.safe_environment()
        environment["PHASE4_REVIEW_TOKEN_INPUT"] = "wrong-synthetic-token"
        self.assert_rejected_before_connect(environment, expected="invalid")

    def test_manifest_hash_drift_fails_before_connection(self):
        environment = self.safe_environment()
        with patch.object(
            corrective,
            "validate_runtime_execution_identity",
            return_value=corrective.RuntimeIdentity(
                REPOSITORY_ROOT, EXECUTION_SHA, TREE_SHA
            ),
        ), patch.object(
            corrective,
            "load_authorized_manifest",
            side_effect=ValueError("manifest SHA-256 mismatch"),
        ), patch.object(corrective, "connect_database") as connect:
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                corrective.execute(self.argv(), environ=environment)
        connect.assert_not_called()

    def test_git_head_mismatch_fails_before_connection(self):
        environment = self.safe_environment()
        with patch.object(
            corrective,
            "derive_runtime_execution_git_identity",
            side_effect=ValueError("derived execution Git SHA differs"),
        ), patch.object(corrective, "connect_database") as connect:
            with self.assertRaisesRegex(ValueError, "Git SHA differs"):
                corrective.execute(self.argv(), environ=environment)
        connect.assert_not_called()

    def test_git_tree_mismatch_fails_before_connection(self):
        environment = self.safe_environment()
        identity = ExecutionGitIdentity(REPOSITORY_ROOT, EXECUTION_SHA)

        def git_value(_root: Path, *arguments: str) -> str:
            if arguments == ("remote", "get-url", "origin"):
                return corrective.CANONICAL_ORIGIN
            if arguments == ("rev-parse", "HEAD^{tree}"):
                return "c" * 40
            raise AssertionError(arguments)

        with patch.object(
            corrective,
            "derive_runtime_execution_git_identity",
            return_value=identity,
        ), patch.object(corrective, "_git_text", side_effect=git_value), patch.object(
            corrective, "connect_database"
        ) as connect:
            with self.assertRaisesRegex(Exception, "tree differs"):
                corrective.execute(self.argv(), environ=environment)
        connect.assert_not_called()

    def test_wrong_database_name_stops_before_ddl_or_dml(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("heliumdb", "16.15", 160015, "public", None)
        with self.assertRaisesRegex(Exception, "not published production"):
            corrective.verify_database_target(conn)
        self.assertEqual(cursor.execute.call_count, 1)
        conn.execute.assert_not_called()
        conn.rollback.assert_not_called()

    def test_wrong_postgresql_major_stops_before_ddl_or_dml(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("neondb", "17.1", 170001, "public", None)
        with self.assertRaisesRegex(Exception, "PostgreSQL 16"):
            corrective.verify_database_target(conn)
        self.assertEqual(cursor.execute.call_count, 1)
        conn.execute.assert_not_called()

    def test_wrong_schema_stops_before_ddl_or_dml(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("neondb", "16.15", 160015, "private", None)
        with self.assertRaisesRegex(Exception, "schema is not public"):
            corrective.verify_database_target(conn)
        self.assertEqual(cursor.execute.call_count, 1)
        conn.execute.assert_not_called()

    def test_secret_safe_error_redacts_values_and_database_url(self):
        environment = self.safe_environment()
        raw = " | ".join(
            (
                environment["DATABASE_URL"],
                environment["RECONCILIATION_REVIEW_TOKEN"],
                environment["PHASE4_REVIEW_TOKEN_INPUT"],
            )
        )
        sanitized = corrective._safe_error(RuntimeError(raw), environment)
        for value in environment.values():
            if value != "1":
                self.assertNotIn(value, sanitized)
        self.assertIn("[REDACTED]", sanitized)

    def test_frozen_authority_constants_are_exact(self):
        self.assertEqual(
            corrective.FROZEN_PROTECTED_FINGERPRINTS["sales_daily"]["sha256"],
            "fd2b4e504b492d9e7609ef8642320f7de300f5294369476da0877aee8da8b2e8",
        )
        self.assertEqual(
            corrective.FROZEN_PROTECTED_FINGERPRINTS["raw_resolution"]["sha256"],
            "06e2726cc33849fc180788fa036a45dcd1b1acd7af32cf813f0ec9311b7dd37a",
        )
        self.assertEqual(corrective.EXPECTED_DATABASE_NAME, "neondb")


class CorrectiveTestDatabaseSafetyTests(unittest.TestCase):
    def assert_configuration_rejected_before_connect(
        self, environment: dict[str, str]
    ) -> None:
        with patch.dict(os.environ, environment, clear=True), patch(
            "psycopg.connect"
        ) as connect:
            with self.assertRaises(ValueError):
                validated_test_connection()
        connect.assert_not_called()

    def test_production_database_url_alone_cannot_be_fixture_authority(self):
        self.assert_configuration_rejected_before_connect(
            {"DATABASE_URL": "postgresql://production.example/neondb"}
        )

    def test_missing_test_database_url_fails_before_connection(self):
        self.assert_configuration_rejected_before_connect({})

    def test_non_loopback_test_database_url_fails_before_connection(self):
        self.assert_configuration_rejected_before_connect(
            {"TEST_DATABASE_URL": "postgresql://x:y@production.example/data_test"}
        )

    def test_non_test_database_name_fails_before_connection(self):
        self.assert_configuration_rejected_before_connect(
            {"TEST_DATABASE_URL": "postgresql://x:y@127.0.0.1:5432/neondb"}
        )

    def test_url_parameter_query_and_fragment_tricks_fail_before_connection(self):
        for value in (
            f"{SAFE_TEST_URL}?host=production.example",
            f"{SAFE_TEST_URL}#neondb",
            f"{SAFE_TEST_URL};host=production.example",
        ):
            with self.subTest(value=value):
                self.assert_configuration_rejected_before_connect(
                    {"TEST_DATABASE_URL": value}
                )

    def _identity_connection(self, row: tuple[object, ...]) -> MagicMock:
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = row
        return connection

    def test_connected_database_identity_mismatch_fails_before_fixture_ddl(self):
        connection = self._identity_connection(("neondb", "16.15", 160015))
        with patch.dict(
            os.environ, {"TEST_DATABASE_URL": SAFE_TEST_URL}, clear=True
        ), patch("psycopg.connect", return_value=connection):
            with self.assertRaisesRegex(ValueError, "current_database"):
                validated_test_connection()
        connection.execute.assert_not_called()
        connection.close.assert_called_once_with()

    def test_connected_wrong_major_fails_before_fixture_ddl(self):
        connection = self._identity_connection(("procurement_test", "17.1", 170001))
        with patch.dict(
            os.environ, {"TEST_DATABASE_URL": SAFE_TEST_URL}, clear=True
        ), patch("psycopg.connect", return_value=connection):
            with self.assertRaisesRegex(ValueError, "PostgreSQL 16"):
                validated_test_connection()
        connection.execute.assert_not_called()
        connection.close.assert_called_once_with()


class CorrectiveBootstrapTests(unittest.TestCase):
    def run_bootstrap_with_fake_tools(
        self, git_body: str
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        invoked = root / "python-invoked"
        git = root / "git"
        git.write_text("#!/bin/sh\n" + textwrap.dedent(git_body), encoding="utf-8")
        python = root / "python3"
        python.write_text(
            f"#!/bin/sh\nprintf invoked > '{invoked}'\nexit 0\n", encoding="utf-8"
        )
        git.chmod(git.stat().st_mode | stat.S_IXUSR)
        python.chmod(python.stat().st_mode | stat.S_IXUSR)
        test_bootstrap = root / BOOTSTRAP.name
        test_bootstrap.write_text(
            BOOTSTRAP.read_text(encoding="utf-8").replace(
                "git_command=/usr/bin/git", f"git_command={git}"
            ),
            encoding="utf-8",
        )
        test_bootstrap.chmod(test_bootstrap.stat().st_mode | stat.S_IXUSR)
        environment = {
            **os.environ,
            "PATH": f"{root}:{os.environ['PATH']}",
            "REPLIT_DEPLOYMENT": "1",
        }
        result = subprocess.run(
            [str(test_bootstrap), EXECUTION_SHA, TREE_SHA],
            text=True,
            capture_output=True,
            env=environment,
            check=False,
        )
        return result, invoked

    def test_clone_or_network_failure_never_invokes_repository_python(self):
        result, invoked = self.run_bootstrap_with_fake_tools(
            """
            if [ "$1" = clone ]; then exit 73; fi
            exit 74
            """
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(invoked.exists())

    def test_dirty_fresh_clone_never_invokes_repository_python(self):
        result, invoked = self.run_bootstrap_with_fake_tools(
            f"""
            if [ "$1" = clone ]; then
              for clone_dir in "$@"; do :; done
              mkdir -p "$clone_dir"
              exit 0
            fi
            shift
            if [ "$2" = remote ]; then
              printf '%s\\n' '{corrective.CANONICAL_ORIGIN}'
              exit 0
            fi
            if [ "$2" = rev-parse ] && [ "$4" = 'HEAD^{{commit}}' ]; then
              printf '%s\\n' '{EXECUTION_SHA}'
              exit 0
            fi
            if [ "$2" = rev-parse ] && [ "$4" = 'HEAD^{{tree}}' ]; then
              printf '%s\\n' '{TREE_SHA}'
              exit 0
            fi
            if [ "$2" = status ]; then
              printf '%s\\n' '?? unexpected-file'
              exit 0
            fi
            exit 0
            """
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not clean", result.stderr)
        self.assertFalse(invoked.exists())

    def test_bootstrap_orders_all_clone_proofs_before_python(self):
        source = BOOTSTRAP.read_text(encoding="utf-8")
        python_position = source.index("python3 ")
        for proof in (
            "observed_origin=",
            "observed_sha=",
            "observed_tree=",
            "observed_status=",
        ):
            self.assertLess(source.index(proof), python_position)
        self.assertNotIn("DATABASE_URL", source)
        self.assertNotIn("RECONCILIATION_REVIEW_TOKEN", source)
        self.assertNotIn("PHASE4_REVIEW_TOKEN_INPUT", source)

    def test_real_git_clone_ignores_hostile_inherited_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            runner = source / "procurement" / "tools" / Path(corrective.__file__).name
            runner.parent.mkdir(parents=True)
            verified_python = root / "verified-python"
            runner.write_text(
                textwrap.dedent(
                    f"""
                    import os
                    from pathlib import Path

                    expected = (
                        os.environ.get("DATABASE_URL") == "synthetic-database-sentinel"
                        and os.environ.get("RECONCILIATION_REVIEW_TOKEN")
                        == "synthetic-configured-token"
                        and os.environ.get("PHASE4_REVIEW_TOKEN_INPUT")
                        == "synthetic-input-token"
                    )
                    Path({str(verified_python)!r}).write_text(
                        "AUTHORIZED_ENVIRONMENT_PRESENT" if expected else "MISSING",
                        encoding="utf-8",
                    )
                    """
                ),
                encoding="utf-8",
            )
            (source / "scripts").mkdir()
            (source / "scripts" / BOOTSTRAP.name).write_text(
                "tracked bootstrap fixture\n", encoding="utf-8"
            )
            run_isolated_real_git(["git", "init", "-q", str(source)])
            run_isolated_real_git(["git", "-C", str(source), "add", "."])
            run_isolated_real_git(
                [
                    "git",
                    "-C",
                    str(source),
                    "-c",
                    "user.name=Corrective Test",
                    "-c",
                    "user.email=corrective@example.invalid",
                    "commit",
                    "-q",
                    "-m",
                    "fixture",
                ]
            )
            commit = run_isolated_real_git(
                ["git", "-C", str(source), "rev-parse", "HEAD^{commit}"]
            ).stdout.strip()
            tree = run_isolated_real_git(
                ["git", "-C", str(source), "rev-parse", "HEAD^{tree}"]
            ).stdout.strip()
            origin = root / "origin.git"
            run_isolated_real_git(
                ["git", "clone", "-q", "--bare", str(source), str(origin)]
            )

            hook_executed = root / "hostile-hook-executed"
            secret_exposed = root / "hostile-hook-secret-exposed"
            hostile_hooks = root / "hostile-hooks"
            hostile_hooks.mkdir()
            hook = hostile_hooks / "post-checkout"
            hook.write_text(
                textwrap.dedent(
                    f"""\
                    #!/bin/sh
                    printf executed > {str(hook_executed)!r}
                    if [ "${{DATABASE_URL:-}}" = synthetic-database-sentinel ] || \
                       [ "${{RECONCILIATION_REVIEW_TOKEN:-}}" = synthetic-configured-token ] || \
                       [ "${{PHASE4_REVIEW_TOKEN_INPUT:-}}" = synthetic-input-token ]; then
                      printf exposed > {str(secret_exposed)!r}
                    fi
                    """
                ),
                encoding="utf-8",
            )
            hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
            hostile_home = root / "hostile-home"
            hostile_home.mkdir()
            hostile_config = hostile_home / ".gitconfig"
            hostile_config.write_text(
                f"[core]\n\thooksPath = {hostile_hooks}\n", encoding="utf-8"
            )
            hostile_xdg = root / "hostile-xdg"
            (hostile_xdg / "git").mkdir(parents=True)
            (hostile_xdg / "git" / "config").write_text(
                f"[core]\n\thooksPath = {hostile_hooks}\n", encoding="utf-8"
            )
            hostile_template = root / "hostile-template"
            (hostile_template / "hooks").mkdir(parents=True)
            template_hook = hostile_template / "hooks" / "post-checkout"
            template_hook.write_bytes(hook.read_bytes())
            template_hook.chmod(template_hook.stat().st_mode | stat.S_IXUSR)
            hostile_path = root / "hostile-path"
            hostile_path.mkdir()
            hostile_git_executed = root / "hostile-git-executed"
            hostile_git = hostile_path / "git"
            hostile_git.write_text(
                f"#!/bin/sh\nprintf executed > {str(hostile_git_executed)!r}\nexit 99\n",
                encoding="utf-8",
            )
            hostile_git.chmod(hostile_git.stat().st_mode | stat.S_IXUSR)

            test_bootstrap = root / BOOTSTRAP.name
            test_bootstrap.write_text(
                BOOTSTRAP.read_text(encoding="utf-8").replace(
                    corrective.CANONICAL_ORIGIN, origin.as_uri()
                ),
                encoding="utf-8",
            )
            test_bootstrap.chmod(test_bootstrap.stat().st_mode | stat.S_IXUSR)
            environment = {
                **os.environ,
                "REPLIT_DEPLOYMENT": "1",
                "PATH": f"{hostile_path}:{os.environ['PATH']}",
                "HOME": str(hostile_home),
                "XDG_CONFIG_HOME": str(hostile_xdg),
                "GIT_CONFIG_GLOBAL": str(hostile_config),
                "GIT_CONFIG_SYSTEM": str(hostile_config),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.hooksPath",
                "GIT_CONFIG_VALUE_0": str(hostile_hooks),
                "GIT_TEMPLATE_DIR": str(hostile_template),
                "GIT_ASKPASS": str(hook),
                "SSH_ASKPASS": str(hook),
                "DATABASE_URL": "synthetic-database-sentinel",
                "RECONCILIATION_REVIEW_TOKEN": "synthetic-configured-token",
                "PHASE4_REVIEW_TOKEN_INPUT": "synthetic-input-token",
            }
            result = subprocess.run(
                [str(test_bootstrap), commit, tree],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                verified_python.read_text(encoding="utf-8"),
                "AUTHORIZED_ENVIRONMENT_PRESENT",
            )
            self.assertFalse(hook_executed.exists())
            self.assertFalse(secret_exposed.exists())
            self.assertFalse(hostile_git_executed.exists())


class CorrectivePythonGitIsolationTests(unittest.TestCase):
    def test_real_python_git_children_ignore_config_and_receive_no_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            for relative in (
                "scripts/phase4-published-production-bootstrap.sh",
                "procurement/tools/reconcile_phase4_published_production.py",
            ):
                path = repository / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("tracked\n", encoding="utf-8")
            run_isolated_real_git(["git", "init", "-q", str(repository)])
            run_isolated_real_git(["git", "-C", str(repository), "add", "."])
            run_isolated_real_git(
                [
                    "git",
                    "-C",
                    str(repository),
                    "-c",
                    "user.name=Corrective Test",
                    "-c",
                    "user.email=corrective@example.invalid",
                    "commit",
                    "-q",
                    "-m",
                    "fixture",
                ]
            )
            run_isolated_real_git(
                [
                    "git",
                    "-C",
                    str(repository),
                    "remote",
                    "add",
                    "origin",
                    corrective.CANONICAL_ORIGIN,
                ]
            )
            commit = run_isolated_real_git(
                ["git", "-C", str(repository), "rev-parse", "HEAD^{commit}"]
            ).stdout.strip()
            tree = run_isolated_real_git(
                ["git", "-C", str(repository), "rev-parse", "HEAD^{tree}"]
            ).stdout.strip()

            hostile_probe = root / "hostile-fsmonitor"
            hostile_executed = root / "hostile-config-executed"
            hostile_probe.write_text(
                f"#!/bin/sh\nprintf executed > {str(hostile_executed)!r}\n",
                encoding="utf-8",
            )
            hostile_probe.chmod(hostile_probe.stat().st_mode | stat.S_IXUSR)
            hostile_home = root / "hostile-home"
            hostile_home.mkdir()
            hostile_config = hostile_home / ".gitconfig"
            hostile_config.write_text(
                f"[core]\n\tfsmonitor = {hostile_probe}\n", encoding="utf-8"
            )
            hostile_path = root / "hostile-path"
            hostile_path.mkdir()
            hostile_git_executed = root / "hostile-git-executed"
            hostile_git = hostile_path / "git"
            hostile_git.write_text(
                f"#!/bin/sh\nprintf executed > {str(hostile_git_executed)!r}\nexit 99\n",
                encoding="utf-8",
            )
            hostile_git.chmod(hostile_git.stat().st_mode | stat.S_IXUSR)
            environment = {
                "PATH": f"{hostile_path}:{os.environ['PATH']}",
                "HOME": str(hostile_home),
                "GIT_CONFIG_GLOBAL": str(hostile_config),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.fsmonitor",
                "GIT_CONFIG_VALUE_0": str(hostile_probe),
                "DATABASE_URL": "synthetic-database-sentinel",
                "RECONCILIATION_REVIEW_TOKEN": "synthetic-configured-token",
                "PHASE4_REVIEW_TOKEN_INPUT": "synthetic-input-token",
            }

            def derive_with_real_git(expected_sha):
                return derive_execution_git_identity(
                    repository, expected_sha=expected_sha
                )

            with patch.dict(os.environ, environment, clear=False), patch.object(
                corrective,
                "derive_runtime_execution_git_identity",
                side_effect=derive_with_real_git,
            ):
                identity = corrective.validate_runtime_execution_identity(commit, tree)
                self.assertEqual(identity.git_sha, commit)
                self.assertEqual(identity.tree_sha, tree)
                self.assertFalse(hostile_executed.exists())
                self.assertFalse(hostile_git_executed.exists())

                child_result = root / "git-child-environment"
                probe = root / "git-child-probe"
                probe.write_text(
                    textwrap.dedent(
                        f"""\
                        #!/bin/sh
                        if [ -n "${{DATABASE_URL+x}}" ] || \
                           [ -n "${{RECONCILIATION_REVIEW_TOKEN+x}}" ] || \
                           [ -n "${{PHASE4_REVIEW_TOKEN_INPUT+x}}" ]; then
                          printf exposed > {str(child_result)!r}
                        else
                          printf isolated > {str(child_result)!r}
                        fi
                        """
                    ),
                    encoding="utf-8",
                )
                probe.chmod(probe.stat().st_mode | stat.S_IXUSR)
                with corrective._isolated_git_subprocess_environment():
                    subprocess.run(
                        [
                            "git",
                            "-C",
                            str(repository),
                            "-c",
                            f"alias.phase4-environment-probe=!{probe}",
                            "phase4-environment-probe",
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                self.assertEqual(
                    child_result.read_text(encoding="utf-8"), "isolated"
                )


class CorrectiveStateMachineDispatchTests(unittest.TestCase):
    @staticmethod
    @contextmanager
    def connection_context():
        yield MagicMock(), {
            "database": "neondb",
            "postgresql_version": "16.15",
            "postgresql_major": 16,
            "schema": "public",
            "identity_xid": None,
        }

    def run_from(self, states: list[str]) -> dict[str, object]:
        prepared = MagicMock()
        prepared.execution.git_sha = EXECUTION_SHA
        prepared.execution.tree_sha = TREE_SHA
        evidence = []
        for state in states:
            item: dict[str, object] = {}
            if state == "B_ORIGINAL_MANIFEST_PERSISTED_PRE_007":
                item = {"manifest": {"planned_mutations": {"total": 0}}}
            evidence.append((state, item))
        with patch.object(corrective, "prepare_execution", return_value=prepared), patch.object(
            corrective, "verified_connection", side_effect=lambda _url: self.connection_context()
        ), patch.object(corrective, "classify_state", side_effect=evidence), patch.object(
            corrective, "apply_original_manifest_stage", return_value={}
        ) as manifest, patch.object(
            corrective, "apply_migration_007_stage", return_value={}
        ) as migration, patch.object(
            corrective, "apply_terminal_stage", return_value={}
        ) as terminal, patch.object(
            corrective, "prove_terminal_noop", return_value={}
        ) as replay, patch.object(
            corrective, "apply_rebuild_stage", return_value={}
        ) as rebuild:
            result = corrective.execute(
                [
                    "--expected-execution-git-sha",
                    EXECUTION_SHA,
                    "--expected-execution-tree-sha",
                    TREE_SHA,
                ],
                environ={},
            )
        calls = {
            "manifest": manifest.call_count,
            "migration": migration.call_count,
            "terminal": terminal.call_count,
            "replay": replay.call_count,
            "rebuild": rebuild.call_count,
        }
        return {"result": result, "calls": calls}

    def test_restart_from_state_a_runs_each_permitted_stage_once(self):
        result = self.run_from(
            [
                "A_FROZEN_PRODUCTION_BASELINE",
                "B_ORIGINAL_MANIFEST_PERSISTED_PRE_007",
                "C_POST_007_PRE_TERMINAL",
                "D_CURRENT_TERMINAL_PRE_REBUILD",
                "E_CURRENT_TERMINAL_POST_REBUILD",
            ]
        )
        self.assertEqual(
            result["calls"],
            {"manifest": 1, "migration": 1, "terminal": 1, "replay": 1, "rebuild": 1},
        )

    def test_restart_from_state_b_does_not_repeat_manifest(self):
        result = self.run_from(
            [
                "B_ORIGINAL_MANIFEST_PERSISTED_PRE_007",
                "C_POST_007_PRE_TERMINAL",
                "D_CURRENT_TERMINAL_PRE_REBUILD",
                "E_CURRENT_TERMINAL_POST_REBUILD",
            ]
        )
        self.assertEqual(result["calls"]["manifest"], 0)
        self.assertEqual(result["calls"]["migration"], 1)

    def test_restart_from_state_c_does_not_repeat_prior_stages(self):
        result = self.run_from(
            [
                "C_POST_007_PRE_TERMINAL",
                "D_CURRENT_TERMINAL_PRE_REBUILD",
                "E_CURRENT_TERMINAL_POST_REBUILD",
            ]
        )
        self.assertEqual(result["calls"]["manifest"], 0)
        self.assertEqual(result["calls"]["migration"], 0)
        self.assertEqual(result["calls"]["terminal"], 1)

    def test_restart_from_state_d_requires_noop_before_rebuild(self):
        result = self.run_from(
            ["D_CURRENT_TERMINAL_PRE_REBUILD", "E_CURRENT_TERMINAL_POST_REBUILD"]
        )
        self.assertEqual(result["calls"]["replay"], 1)
        self.assertEqual(result["calls"]["rebuild"], 1)

    def test_completed_state_e_is_read_only_noop(self):
        result = self.run_from(["E_CURRENT_TERMINAL_POST_REBUILD"])
        self.assertTrue(result["result"]["repeat_safe"])
        self.assertEqual(
            result["calls"],
            {"manifest": 0, "migration": 0, "terminal": 0, "replay": 0, "rebuild": 0},
        )

    def test_partial_or_mixed_state_stops_without_action(self):
        prepared = MagicMock()
        with patch.object(corrective, "prepare_execution", return_value=prepared), patch.object(
            corrective, "verified_connection", side_effect=lambda _url: self.connection_context()
        ), patch.object(
            corrective,
            "classify_state",
            side_effect=corrective.CorrectiveValidationError("partial or drifted"),
        ), patch.object(corrective, "apply_original_manifest_stage") as manifest:
            with self.assertRaisesRegex(Exception, "partial or drifted"):
                corrective.execute(
                    [
                        "--expected-execution-git-sha",
                        EXECUTION_SHA,
                        "--expected-execution-tree-sha",
                        TREE_SHA,
                    ],
                    environ={},
                )
        manifest.assert_not_called()


class CorrectivePublishedProductionIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        from psycopg import sql

        self.conn, self.target, self.database_info = validated_test_connection()
        self.schema = f"phase4_published_{uuid.uuid4().hex}"
        self.conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.conn.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema))
        )
        for name in PRE_007_MIGRATIONS:
            self.conn.execute((DB_DIR / name).read_text(encoding="utf-8"))
            self.conn.execute(
                """INSERT INTO meta(key,value) VALUES (%s,'applied')
                   ON CONFLICT(key) DO UPDATE SET value='applied'""",
                (f"migration:{name}",),
            )
        self._remove_schema_reapplication_terminal_artifacts()
        self.conn.commit()
        self._seed_exact_state_a()
        self.frozen = protected_state_fingerprints(self.conn)
        self.conn.rollback()
        self.prepared = corrective.PreparedExecution(
            database_url=self.target.url,
            execution=corrective.RuntimeIdentity(
                REPOSITORY_ROOT, EXECUTION_SHA, TREE_SHA
            ),
            original_manifest=corrective.load_authorized_manifest(
                corrective.ORIGINAL_MANIFEST
            ),
            terminal_artifact=corrective.load_terminal_artifact(
                corrective.TERMINAL_MANIFEST, corrective.ORIGINAL_MANIFEST
            ),
        )

    def tearDown(self) -> None:
        from psycopg import sql

        try:
            self.conn.rollback()
            self.conn.execute("SET search_path TO public")
            self.conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema))
            )
            self.conn.commit()
        finally:
            self.conn.close()

    def _remove_schema_reapplication_terminal_artifacts(self) -> None:
        self.conn.execute(
            """CREATE OR REPLACE VIEW v_current_prices AS
               SELECT p.*,o.variant_id,o.vendor_id,o.supplier_sku,
                      o.shopify_units_per_case,o.qualifying_units_per_case,
                      o.assortment_scope,o.assortment_group,o.assortable
               FROM prices p JOIN supplier_offers o USING(offer_id)
               WHERE p.price_state='current' AND o.active"""
        )
        self.conn.execute(
            """CREATE OR REPLACE VIEW v_future_prices AS
               SELECT p.*,o.variant_id,o.vendor_id,o.supplier_sku,
                      o.shopify_units_per_case,o.qualifying_units_per_case,
                      o.assortment_scope,o.assortment_group,o.assortable
               FROM prices p JOIN supplier_offers o USING(offer_id)
               WHERE p.price_state='future' AND o.active"""
        )
        self.conn.execute("DROP FUNCTION IF EXISTS is_operational_current_variant(TEXT)")
        for column in sorted(corrective.TERMINAL_VARIANT_COLUMNS):
            self.conn.execute(f"ALTER TABLE variants DROP COLUMN IF EXISTS {column}")
        self.conn.execute("ALTER TABLE variants ALTER COLUMN product_id SET NOT NULL")

    def _seed_exact_state_a(self) -> None:
        fixture = terminal_fixture_module.Phase4TerminalDispositionPostgresTests(
            "test_migration_is_idempotent_and_constraints_are_named"
        )
        fixture.conn = self.conn
        with patch.object(
            terminal_fixture_module, "persist_manifest_decisions", return_value={}
        ):
            fixture.seed_preterminal_state()

        # The established terminal fixture contains 20 diagnostic family rows
        # that resolve via old-ID alias. Published production resolves those
        # exact full identities through historical identity evidence instead.
        self.conn.execute(
            """UPDATE shopify_sales_daily_raw
               SET source_variant_id='0',
                   source_identity_key='0|' || source_sku || '|' ||
                     upper(source_product_title) || '|' || upper(source_variant_title)
               WHERE resolution_method='APPROVED_HISTORICAL_IDENTITY'
                 AND source_variant_id<>'0'"""
        )
        variants = int(self.conn.execute("SELECT COUNT(*) FROM variants").fetchone()[0])
        missing = 2049 - variants
        if missing < 0:
            raise AssertionError("production-shaped fixture exceeded variant control")
        self.conn.execute(
            """INSERT INTO variants(
                 variant_id,product_id,product_title,variant_title,sku,active,catalog_state
               )
               SELECT 'FILL-' || lpad(g::text,4,'0'),
                      'product-fill-' || lpad(g::text,4,'0'),
                      'Fixture Fill ' || lpad(g::text,4,'0'),'750ML',
                      'FILL-SKU-' || lpad(g::text,4,'0'),TRUE,'LIVE'
               FROM generate_series(1,%s) AS g""",
            (missing,),
        )
        self.conn.execute(
            """UPDATE sales_backfill_runs
               SET source_net_items_sold=82501.0000,source_net_sales=1300975.14
               WHERE sales_backfill_id=%s""",
            (corrective.APPROVED_RUN_ID,),
        )
        self.conn.execute(
            """UPDATE readiness_gates
               SET status='FAIL',
                   evidence_json='{"blockers":["MATERIAL_HISTORICAL_IDENTITIES_UNRESOLVED"]}'::jsonb,
                   message='Historical sales remains blocked: MATERIAL_HISTORICAL_IDENTITIES_UNRESOLVED.'
               WHERE gate_name='SALES_BACKFILL' AND scope_type='GLOBAL' AND scope_id=''"""
        )
        self.conn.execute(
            """UPDATE readiness_gates
               SET status='PASS',message='Catalog reconciliation passed.'
               WHERE gate_name='CATALOG_SYNC' AND scope_type='GLOBAL' AND scope_id=''"""
        )
        catalog_run_id = str(uuid.uuid4())
        self.conn.execute(
            """INSERT INTO catalog_sync_runs(
                 catalog_sync_id,started_at,completed_at,status,shopify_api_version,
                 shopify_reported_variant_count,live_rows_received,exact_current_ids,
                 new_live_variants,missing_seed_variants,potential_recreations,
                 unresolved_count,source_hash,pagination_complete
               ) VALUES (%s,TIMESTAMPTZ '2026-08-10 14:55:53+00',
                         TIMESTAMPTZ '2026-08-10 14:55:54+00','COMPLETED',
                         '2026-07',2003,1999,1999,0,46,0,0,'fixture-catalog-hash',TRUE)""",
            (catalog_run_id,),
        )
        self._seed_run_checkpoints()
        self.conn.commit()

    def _seed_run_checkpoints(self) -> None:
        ranges = []
        cursor = corrective.START_DATE
        while cursor <= corrective.END_DATE:
            end = min(cursor + timedelta(days=30), corrective.END_DATE)
            ranges.append((cursor, end))
            cursor = end + timedelta(days=1)
        self.assertEqual(len(ranges), 21)
        terminal_rows = [480] * 20 + [483]
        for index, (chunk_start, chunk_end) in enumerate(ranges):
            pages = 4 if index < 7 else 3
            row_count = (pages - 1) * 1000 + terminal_rows[index]
            chunk_id = str(uuid.uuid4())
            units = "82501.0000" if index == 0 else "0"
            sales = "1300975.14" if index == 0 else "0"
            self.conn.execute(
                """INSERT INTO sales_backfill_chunks(
                     sales_backfill_chunk_id,sales_backfill_id,chunk_index,
                     requested_start_date,requested_end_date,query_version,
                     query_contract_hash,status,page_size,expected_pages,completed_pages,
                     row_count,unique_fact_count,source_net_items_sold,source_net_sales,
                     control_net_items_sold,control_net_sales,control_reconciled,
                     source_hash,parse_state
                   ) VALUES (%s,%s,%s,%s,%s,'SHOPIFYQL_SALES_V2','fixture-contract',
                             'COMPLETED',1000,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,
                             %s,'PASS')""",
                (
                    chunk_id,
                    corrective.APPROVED_RUN_ID,
                    index,
                    chunk_start,
                    chunk_end,
                    pages,
                    pages,
                    row_count,
                    row_count,
                    units,
                    sales,
                    units,
                    sales,
                    f"chunk-{index}",
                ),
            )
            for page_index in range(pages):
                is_terminal = page_index == pages - 1
                page_rows = terminal_rows[index] if is_terminal else 1000
                self.conn.execute(
                    """INSERT INTO sales_backfill_pages(
                         sales_backfill_chunk_id,page_index,page_offset,page_limit,
                         requested_start_date,requested_end_date,query_version,
                         query_contract_hash,status,is_terminal,row_count,parse_state,
                         source_hash
                       ) VALUES (%s,%s,%s,1000,%s,%s,'SHOPIFYQL_SALES_V2',
                                 'fixture-contract','COMPLETED',%s,%s,'PASS',%s)""",
                    (
                        chunk_id,
                        page_index,
                        page_index * 1000,
                        chunk_start,
                        chunk_end,
                        is_terminal,
                        page_rows,
                        f"page-{index}-{page_index}",
                    ),
                )

    @staticmethod
    def execution_patch():
        return patch(
            "procurement_os.historical_sales_terminal.derive_runtime_execution_git_identity",
            return_value=ExecutionGitIdentity(REPOSITORY_ROOT, EXECUTION_SHA),
        )

    def _capture_rebuild_controls(self, conn):
        terminal = corrective.inspect_terminal_state(
            conn, self.prepared.terminal_artifact, self.prepared.execution.git_sha
        )
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT COUNT(*)::int,COALESCE(SUM(units_sold),0),
                          COALESCE(SUM(net_sales),0)
                   FROM sales_daily WHERE source='SHOPIFYQL_SALES'
                     AND sale_date BETWEEN %s AND %s""",
                (corrective.START_DATE, corrective.END_DATE),
            )
            daily = cursor.fetchone()
            cursor.execute(
                """SELECT status,control_evidence,to_jsonb(r)::text
                   FROM sales_backfill_runs r WHERE sales_backfill_id=%s""",
                (corrective.APPROVED_RUN_ID,),
            )
            backfill = cursor.fetchone()
            cursor.execute(
                """SELECT gate_name,status,evidence_json,to_jsonb(g)::text
                   FROM readiness_gates g
                   ORDER BY gate_name,scope_type,scope_id"""
            )
            readiness = cursor.fetchall()
            cursor.execute("SELECT COUNT(*)::int FROM purchase_orders")
            purchase_orders = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*)::int FROM purchase_order_lines")
            purchase_order_lines = int(cursor.fetchone()[0])
        if daily is None or backfill is None:
            raise AssertionError("rebuild control snapshot is incomplete")
        sales_gate = next(row for row in readiness if row[0] == "SALES_BACKFILL")
        return {
            "fingerprints": protected_state_fingerprints(conn),
            "raw_status_counts": corrective._raw_status_counts(conn),
            "sales_daily": {
                "rows": int(daily[0]),
                "net_units": daily[1],
                "net_sales": daily[2],
            },
            "sales_backfill_run": {
                "status": str(backfill[0]),
                "evidence": backfill[1],
                "exact_row": str(backfill[2]),
            },
            "readiness_exact_rows": tuple(
                (str(row[0]), str(row[1]), row[2], str(row[3])) for row in readiness
            ),
            "sales_backfill_gate": {
                "status": str(sales_gate[1]),
                "evidence": sales_gate[2],
            },
            "terminal_classification": terminal["classification"],
            "source_lifecycle": terminal["source_lifecycle"],
            "terminal_planned_mutations": terminal["planned_mutations"],
            "purchase_orders": purchase_orders,
            "purchase_order_lines": purchase_order_lines,
        }

    def test_exact_state_a_through_e_and_completed_replay(self):
        with patch.object(
            corrective, "FROZEN_PROTECTED_FINGERPRINTS", self.frozen
        ), self.execution_patch():
            state_a, evidence_a = corrective.classify_state(self.conn, self.prepared)
            self.assertEqual(state_a, "A_FROZEN_PRODUCTION_BASELINE")
            self.assertEqual(evidence_a["manifest"]["planned_mutations"]["total"], 711)

            manifest = corrective.apply_original_manifest_stage(
                self.conn, self.prepared, actor="corrective-integration-test"
            )
            self.assertEqual(manifest["inserted_decisions"], 343)
            state_b, evidence_b = corrective.classify_state(self.conn, self.prepared)
            self.assertEqual(state_b, "B_ORIGINAL_MANIFEST_PERSISTED_PRE_007")
            self.assertEqual(evidence_b["manifest"]["planned_mutations"]["total"], 0)

            with tempfile.TemporaryDirectory() as temporary:
                bad_migration = Path(temporary) / "007.sql"
                bad_migration.write_text(
                    "ALTER TABLE variants ADD COLUMN rolled_back_probe TEXT; SELECT 1/0;",
                    encoding="utf-8",
                )
                with patch.object(corrective, "MIGRATION_007", bad_migration):
                    with self.assertRaises(Exception):
                        corrective.apply_migration_007_stage(self.conn, self.prepared)
            self.assertIsNone(
                self.conn.execute(
                    """SELECT 1 FROM information_schema.columns
                       WHERE table_schema=current_schema() AND table_name='variants'
                         AND column_name='rolled_back_probe'"""
                ).fetchone()
            )
            self.assertEqual(
                corrective.migration_007_state(self.conn)["classification"], "ABSENT"
            )

            self.conn.execute(
                "INSERT INTO meta(key,value) VALUES (%s,'applied')",
                (corrective.MIGRATION_007_MARKER,),
            )
            self.conn.commit()
            with self.assertRaisesRegex(Exception, "partial or drifted"):
                corrective.classify_state(self.conn, self.prepared)
            self.conn.execute(
                "DELETE FROM meta WHERE key=%s", (corrective.MIGRATION_007_MARKER,)
            )
            self.conn.commit()

            migration = corrective.apply_migration_007_stage(self.conn, self.prepared)
            self.assertEqual(migration["migration"], "007_phase4_terminal_disposition.sql")
            state_c, evidence_c = corrective.classify_state(self.conn, self.prepared)
            self.assertEqual(state_c, "C_POST_007_PRE_TERMINAL")
            self.assertEqual(evidence_c["terminal"]["diagnostics"], [])

            terminal = corrective.apply_terminal_stage(
                self.conn, self.prepared, actor="corrective-integration-test"
            )
            self.assertEqual(terminal["committed_mutations"], 858)
            state_d, _ = corrective.classify_state(self.conn, self.prepared)
            self.assertEqual(state_d, "D_CURRENT_TERMINAL_PRE_REBUILD")
            replay = corrective.prove_terminal_noop(
                self.conn, self.prepared, actor="corrective-integration-test"
            )
            self.assertEqual(replay["committed_mutations"], 0)
            self.assertFalse(any(replay["planned_mutations"].values()))

            finalizer = corrective.apply_rebuild_stage(self.conn, self.prepared)
            self.assertEqual(finalizer["status"], "PASS")
            self.assertEqual(finalizer["blockers"], [])
            state_e, evidence_e = corrective.classify_state(self.conn, self.prepared)
            self.assertEqual(state_e, "E_CURRENT_TERMINAL_POST_REBUILD")
            final = evidence_e["final"]
            self.assertEqual(final["resolution_methods"], corrective.EXPECTED_FINAL_METHOD_COUNTS)
            self.assertEqual(final["purchase_orders"], 0)
            self.assertEqual(final["purchase_order_lines"], 0)
            self.assertEqual(final["po_blocking_gate"], "VENDOR_RULES")

            before_repeat = protected_state_fingerprints(self.conn)
            self.conn.rollback()
            state_repeat, evidence_repeat = corrective.classify_state(
                self.conn, self.prepared
            )
            after_repeat = protected_state_fingerprints(self.conn)
            self.assertEqual(state_repeat, "E_CURRENT_TERMINAL_POST_REBUILD")
            self.assertEqual(before_repeat, after_repeat)
            self.assertEqual(
                evidence_repeat["terminal"]["planned_mutations"],
                {
                    "restored_variants": 0,
                    "terminal_decisions": 0,
                    "original_exclusion_normalizations": 0,
                    "terminal_aliases": 0,
                    "active_exclusions": 0,
                    "authority_registrations": 0,
                },
            )

    def test_real_finalizer_changes_roll_back_when_final_controls_raise(self):
        with patch.object(
            corrective, "FROZEN_PROTECTED_FINGERPRINTS", self.frozen
        ), self.execution_patch():
            corrective.apply_original_manifest_stage(
                self.conn, self.prepared, actor="corrective-rollback-test"
            )
            corrective.apply_migration_007_stage(self.conn, self.prepared)
            corrective.apply_terminal_stage(
                self.conn, self.prepared, actor="corrective-rollback-test"
            )
            state_d, _ = corrective.classify_state(self.conn, self.prepared)
            self.assertEqual(state_d, "D_CURRENT_TERMINAL_PRE_REBUILD")
            before = self._capture_rebuild_controls(self.conn)
            self.conn.rollback()

            observed_after_real_finalizer = {}

            def fail_after_real_finalizer(conn):
                observed_after_real_finalizer.update(
                    self._capture_rebuild_controls(conn)
                )
                raise RuntimeError("synthetic post-finalizer validation failure")

            with patch.object(
                corrective,
                "final_business_controls",
                side_effect=fail_after_real_finalizer,
            ) as final_controls:
                with self.assertRaisesRegex(
                    RuntimeError, "synthetic post-finalizer validation failure"
                ):
                    corrective.apply_rebuild_stage(self.conn, self.prepared)
            final_controls.assert_called_once_with(self.conn)

            self.assertEqual(
                observed_after_real_finalizer["raw_status_counts"],
                corrective.EXPECTED_FINAL_STATUS_COUNTS,
            )
            self.assertEqual(
                observed_after_real_finalizer["sales_daily"],
                corrective.EXPECTED_SALES_DAILY,
            )
            self.assertEqual(
                observed_after_real_finalizer["sales_backfill_run"]["status"],
                "COMPLETED",
            )
            self.assertTrue(
                observed_after_real_finalizer["sales_backfill_run"]["evidence"][
                    "canonical_aggregate_rebuilt"
                ]
            )
            self.assertEqual(
                observed_after_real_finalizer["sales_backfill_gate"]["status"],
                "PASS",
            )
            self.assertEqual(
                observed_after_real_finalizer["source_lifecycle"], "POST_REBUILD"
            )
            self.assertNotEqual(
                observed_after_real_finalizer["fingerprints"], before["fingerprints"]
            )

            from psycopg import sql

            fresh_conn, _, _ = validated_test_connection()
            try:
                fresh_conn.execute(
                    sql.SQL("SET search_path TO {}, public").format(
                        sql.Identifier(self.schema)
                    )
                )
                after = self._capture_rebuild_controls(fresh_conn)
                fresh_conn.rollback()
            finally:
                fresh_conn.close()

            self.assertEqual(before["fingerprints"], self.frozen)
            self.assertEqual(
                before["raw_status_counts"], corrective.EXPECTED_INITIAL_STATUS_COUNTS
            )
            self.assertEqual(before["sales_backfill_gate"]["status"], "FAIL")
            self.assertEqual(before["terminal_classification"], "CURRENT_TERMINAL_EXACT")
            self.assertEqual(before["source_lifecycle"], "PRE_REBUILD")
            self.assertEqual(before["purchase_orders"], 0)
            self.assertEqual(before["purchase_order_lines"], 0)
            self.assertEqual(after, before)

    def test_unexpected_migration_marker_stops_every_allowed_state(self):
        def assert_marker_rejected(expected_state, value):
            state, _ = corrective.classify_state(self.conn, self.prepared)
            self.assertEqual(state, expected_state)
            self.conn.execute(
                "INSERT INTO meta(key,value) VALUES ('migration:008_unapproved.sql',%s)",
                (value,),
            )
            self.conn.commit()
            try:
                self.assertEqual(
                    corrective.migration_007_state(self.conn)["classification"],
                    "PARTIAL_OR_DRIFTED",
                )

                @contextmanager
                def fixture_connection(_database_url):
                    yield self.conn, {
                        "database": "procurement_review_focused_test",
                        "postgresql_major": 16,
                    }

                with patch.object(
                    corrective, "prepare_execution", return_value=self.prepared
                ), patch.object(
                    corrective,
                    "verified_connection",
                    side_effect=fixture_connection,
                ), patch.object(
                    corrective, "_clear_libpq_environment"
                ), patch.object(
                    corrective, "apply_original_manifest_stage"
                ) as manifest, patch.object(
                    corrective, "apply_migration_007_stage"
                ) as migration, patch.object(
                    corrective, "apply_terminal_stage"
                ) as terminal, patch.object(
                    corrective, "prove_terminal_noop"
                ) as replay, patch.object(
                    corrective, "apply_rebuild_stage"
                ) as rebuild:
                    with self.assertRaisesRegex(Exception, "partial or drifted"):
                        corrective.execute(
                            [
                                "--expected-execution-git-sha",
                                EXECUTION_SHA,
                                "--expected-execution-tree-sha",
                                TREE_SHA,
                            ],
                            environ={},
                        )
                for operation in (manifest, migration, terminal, replay, rebuild):
                    operation.assert_not_called()
            finally:
                self.conn.rollback()
                self.conn.execute(
                    "DELETE FROM meta WHERE key='migration:008_unapproved.sql'"
                )
                self.conn.commit()
            restored, _ = corrective.classify_state(self.conn, self.prepared)
            self.assertEqual(restored, expected_state)

        with patch.object(
            corrective, "FROZEN_PROTECTED_FINGERPRINTS", self.frozen
        ), self.execution_patch():
            for value in ("applied", "unexpected-value"):
                with self.subTest(state="A_FROZEN_PRODUCTION_BASELINE", value=value):
                    assert_marker_rejected("A_FROZEN_PRODUCTION_BASELINE", value)

            corrective.apply_original_manifest_stage(
                self.conn, self.prepared, actor="corrective-marker-test"
            )
            for value in ("applied", "unexpected-value"):
                with self.subTest(
                    state="B_ORIGINAL_MANIFEST_PERSISTED_PRE_007", value=value
                ):
                    assert_marker_rejected(
                        "B_ORIGINAL_MANIFEST_PERSISTED_PRE_007", value
                    )

            corrective.apply_migration_007_stage(self.conn, self.prepared)
            for value in ("applied", "unexpected-value"):
                with self.subTest(state="C_POST_007_PRE_TERMINAL", value=value):
                    assert_marker_rejected("C_POST_007_PRE_TERMINAL", value)

            corrective.apply_terminal_stage(
                self.conn, self.prepared, actor="corrective-marker-test"
            )
            for value in ("applied", "unexpected-value"):
                with self.subTest(
                    state="D_CURRENT_TERMINAL_PRE_REBUILD", value=value
                ):
                    assert_marker_rejected("D_CURRENT_TERMINAL_PRE_REBUILD", value)

            corrective.apply_rebuild_stage(self.conn, self.prepared)
            for value in ("applied", "unexpected-value"):
                with self.subTest(
                    state="E_CURRENT_TERMINAL_POST_REBUILD", value=value
                ):
                    assert_marker_rejected("E_CURRENT_TERMINAL_POST_REBUILD", value)

    def test_validated_loopback_test_database_is_postgresql_16(self):
        self.assertTrue(self.target.database.endswith("_test"))
        self.assertEqual(self.database_info.database, self.target.database)
        self.assertEqual(self.database_info.server_major, 16)


class CorrectiveNoExternalActionTests(unittest.TestCase):
    def test_corrective_runtime_has_no_shopify_client_or_po_mutation_path(self):
        runtime = Path(corrective.__file__).read_text(encoding="utf-8").casefold()
        for forbidden in (
            "shopifygraphqlclient",
            "client.query(",
            "client.mutate(",
            "insert into purchase_orders",
            "insert into purchase_order_lines",
            "create_purchase_order",
        ):
            self.assertNotIn(forbidden, runtime)
        self.assertIn("rerun_sales_identity_resolution", runtime)


if __name__ == "__main__":
    unittest.main()
