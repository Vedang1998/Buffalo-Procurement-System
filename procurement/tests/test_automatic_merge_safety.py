"""Deterministic negative proofs for the retained inert merge-hook path."""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
REPLIT_PATH = REPO_ROOT / ".replit"
HOOK_PATH = REPO_ROOT / "scripts" / "post-merge.sh"
RUNNER_PATH = REPO_ROOT / "procurement" / "tools" / "run_tests.py"
WORKFLOWS_PATH = REPO_ROOT / ".github" / "workflows"

MESSAGE = "Automatic post-merge actions are disabled."
EXPECTED_HOOK = (
    "#!/bin/sh\n"
    "printf '%s\\n' 'Automatic post-merge actions are disabled.'\n"
    "exit 0\n"
).encode("utf-8")
EXPECTED_REPLIT_BYTES = 1017
EXPECTED_REPLIT_SHA256 = (
    "651f3048271498471335f90dac914d6fb125a38a28f73d28dd5703a03f963814"
)
SENTINEL_COMMANDS = (
    "pnpm",
    "npm",
    "npx",
    "yarn",
    "bun",
    "uv",
    "uvx",
    "pip",
    "pip3",
    "drizzle-kit",
    "psql",
    "createdb",
    "dropdb",
    "curl",
    "wget",
    "ssh",
    "nc",
    "git",
    "gh",
    "node",
    "python",
    "python3",
    "uvicorn",
    "replit",
    "shopify",
    "docker",
    "kubectl",
)

RUNNER_SPEC = importlib.util.spec_from_file_location(
    "automatic_merge_safety_runner", RUNNER_PATH
)
assert RUNNER_SPEC is not None and RUNNER_SPEC.loader is not None
runner = importlib.util.module_from_spec(RUNNER_SPEC)
sys.modules[RUNNER_SPEC.name] = runner
RUNNER_SPEC.loader.exec_module(runner)


def _walk_toml(value: object):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk_toml(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_toml(child)


class AutomaticMergeSafetyTests(unittest.TestCase):
    def _sentinel_fixture(self, root: Path) -> tuple[Path, Path]:
        tools = root / "fake-tools"
        tools.mkdir(mode=0o700)
        marker = root / "unexpected-tool-invocation.log"
        sentinel = (
            "#!/bin/sh\n"
            "printf '%s\\n' \"$0\" >> \"$MERGE_HOOK_SENTINEL_LOG\"\n"
            "exit 97\n"
        )
        for command in SENTINEL_COMMANDS:
            path = tools / command
            path.write_text(sentinel, encoding="utf-8")
            path.chmod(0o700)
        return tools, marker

    def _run_hook(
        self,
        *,
        tools: Path,
        marker: Path,
        arguments: tuple[str, ...] = (),
        additions: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            "PATH": str(tools),
            "LC_ALL": "C",
            "LANG": "C",
            "MERGE_HOOK_SENTINEL_LOG": str(marker),
        }
        if additions:
            environment.update(additions)
        return subprocess.run(
            [str(HOOK_PATH), *arguments],
            cwd=marker.parent,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    def _assert_inert(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, f"{MESSAGE}\n")
        self.assertEqual(result.stderr, "")

    def test_replit_configuration_remains_valid_toml(self) -> None:
        with REPLIT_PATH.open("rb") as handle:
            config = tomllib.load(handle)
        self.assertEqual(
            set(config), {"modules", "deployment", "workflows", "agent", "nix"}
        )

    def test_no_automatic_postmerge_registration_or_workflow_reference_remains(
        self,
    ) -> None:
        with REPLIT_PATH.open("rb") as handle:
            config = tomllib.load(handle)
        for key, value in _walk_toml(config):
            normalized_key = re.sub(r"[^a-z0-9]", "", key.lower())
            with self.subTest(key=key):
                self.assertNotEqual(normalized_key, "postmerge")
                self.assertNotEqual(value, "scripts/post-merge.sh")

        for path in sorted(WORKFLOWS_PATH.glob("*.y*ml")):
            source = path.read_text(encoding="utf-8")
            with self.subTest(workflow=path.name):
                self.assertNotIn("scripts/post-merge.sh", source)
                self.assertNotRegex(source, r"(?i)post[-_ ]?merge")

    def test_unrelated_replit_configuration_is_byte_exact(self) -> None:
        content = REPLIT_PATH.read_bytes()
        self.assertEqual(len(content), EXPECTED_REPLIT_BYTES)
        self.assertEqual(hashlib.sha256(content).hexdigest(), EXPECTED_REPLIT_SHA256)

    def test_hook_is_exact_executable_builtin_only_stub(self) -> None:
        self.assertEqual(HOOK_PATH.read_bytes(), EXPECTED_HOOK)
        self.assertEqual(stat.S_IMODE(HOOK_PATH.stat().st_mode), 0o755)

    def test_missing_and_empty_database_url_are_equivalent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            tools, marker = self._sentinel_fixture(Path(temporary_directory))
            missing = self._run_hook(tools=tools, marker=marker)
            empty = self._run_hook(
                tools=tools, marker=marker, additions={"DATABASE_URL": ""}
            )
            self._assert_inert(missing)
            self._assert_inert(empty)
            self.assertEqual(
                (missing.returncode, missing.stdout, missing.stderr),
                (empty.returncode, empty.stdout, empty.stderr),
            )
            self.assertFalse(marker.exists())

    def test_synthetic_database_url_cannot_reactivate_or_disclose_work(self) -> None:
        synthetic_secret = "SYNTHETIC_DATABASE_SECRET_DO_NOT_DISCLOSE"
        synthetic_url = (
            "postgresql://synthetic:"
            f"{synthetic_secret}@127.0.0.1:9/not_an_operational_database"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            tools, marker = self._sentinel_fixture(Path(temporary_directory))
            result = self._run_hook(
                tools=tools,
                marker=marker,
                additions={"DATABASE_URL": synthetic_url},
            )
            self._assert_inert(result)
            self.assertNotIn(synthetic_secret, result.stdout + result.stderr)
            self.assertNotIn(synthetic_url, result.stdout + result.stderr)
            self.assertFalse(marker.exists())

    def test_extra_arguments_are_ignored_and_not_disclosed(self) -> None:
        synthetic_argument = "SYNTHETIC_ARGUMENT_SECRET_DO_NOT_DISCLOSE"
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            tools, marker = self._sentinel_fixture(root)
            hostile_marker = root / "argument-was-evaluated"
            arguments = (
                "--apply",
                synthetic_argument,
                f"; touch {hostile_marker}",
                "$(curl https://invalid.example)",
            )
            result = self._run_hook(
                tools=tools,
                marker=marker,
                arguments=arguments,
            )
            self._assert_inert(result)
            self.assertNotIn(synthetic_argument, result.stdout + result.stderr)
            self.assertFalse(marker.exists())
            self.assertFalse(hostile_marker.exists())

    def test_package_database_network_and_deployment_sentinels_never_run(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            tools, marker = self._sentinel_fixture(Path(temporary_directory))
            result = self._run_hook(
                tools=tools,
                marker=marker,
                additions={
                    "DATABASE_URL": "postgresql://synthetic@127.0.0.1:9/synthetic_test"
                },
            )
            self._assert_inert(result)
            self.assertFalse(marker.exists())

    def test_repeated_calls_and_shell_environment_inputs_remain_inert(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            tools, marker = self._sentinel_fixture(root)
            shell_environment = root / "synthetic-shell-environment"
            shell_environment.write_text(
                f"printf '%s\\n' shell-environment >> {marker}\n", encoding="utf-8"
            )
            results = [
                self._run_hook(tools=tools, marker=marker),
                self._run_hook(
                    tools=tools,
                    marker=marker,
                    additions={"DATABASE_URL": "synthetic-value"},
                ),
                self._run_hook(
                    tools=tools,
                    marker=marker,
                    arguments=("--force",),
                    additions={
                        "ENV": str(shell_environment),
                        "BASH_ENV": str(shell_environment),
                    },
                ),
            ]
            for result in results:
                self._assert_inert(result)
            self.assertEqual(
                len(
                    {
                        (item.returncode, item.stdout, item.stderr)
                        for item in results
                    }
                ),
                1,
            )
            self.assertFalse(marker.exists())

    def test_module_floor_is_exact_and_global_floor_remains_sum_derived(self) -> None:
        discovered_methods = len(
            unittest.defaultTestLoader.getTestCaseNames(AutomaticMergeSafetyTests)
        )
        self.assertEqual(discovered_methods, 10)
        self.assertEqual(
            runner.REQUIRED_MODULE_MINIMUMS[Path(__file__).name], discovered_methods
        )
        self.assertEqual(
            runner.GLOBAL_MINIMUM_TESTS,
            sum(runner.REQUIRED_MODULE_MINIMUMS.values()),
        )
        source = RUNNER_PATH.read_text(encoding="utf-8")
        self.assertRegex(
            source,
            r"(?m)^GLOBAL_MINIMUM_TESTS = sum\(REQUIRED_MODULE_MINIMUMS\.values\(\)\)$",
        )


if __name__ == "__main__":
    unittest.main()
