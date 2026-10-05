"""Static contract for the reproducible Railway staging image."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
RAILWAY_CONFIG = ROOT / "railway.json"

PYTHON_IMAGE = (
    "python:3.13.11-slim-bookworm@"
    "sha256:20080e807bfc404f8450b185cf0fc95d553462673598549613735f70a5b4d5d0"
)
UV_IMAGE = (
    "ghcr.io/astral-sh/uv:0.12.3@"
    "sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc"
)
RESEARCH_AUTHORITY_FILES = {
    "procurement/seed/variant_aliases.csv": (
        "b9a2e862fcdff9e204f889ec277ae18201b78dc1197e53bb8876f5b520a68a21"
    ),
    "procurement/review/phase4_identity_manifest_corrected.csv": (
        "95fe0c7902efc337bb51ba0b5a2f974f9b2ac76d7221a25e7dcd52a8cd28d287"
    ),
    "procurement/review/phase4_terminal_disposition_manifest.csv": (
        "fb1e15e67fe66c7742b84ea2c50bf01ce8a5008f00b4887293404ac09d3f59ff"
    ),
}


class StagingContainerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        cls.dockerignore = DOCKERIGNORE.read_text(encoding="utf-8")
        cls.railway = json.loads(RAILWAY_CONFIG.read_text(encoding="utf-8"))

    def test_base_and_uv_images_are_exactly_pinned(self) -> None:
        self.assertTrue(
            self.dockerfile.startswith(
                "# syntax=docker/dockerfile:1.7@"
                "sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e\n"
            )
        )
        from_lines = tuple(
            line.strip()
            for line in self.dockerfile.splitlines()
            if line.startswith("FROM ")
        )
        self.assertEqual(
            from_lines,
            (
                f"FROM {UV_IMAGE} AS uv",
                f"FROM {PYTHON_IMAGE} AS dependencies",
                f"FROM {PYTHON_IMAGE} AS runtime",
            ),
        )

    def test_dependencies_are_installed_only_from_the_frozen_lock(self) -> None:
        self.assertIn("COPY pyproject.toml uv.lock ./", self.dockerfile)
        self.assertIn(
            "uv sync --frozen --no-dev --no-install-project", self.dockerfile
        )
        self.assertIn("UV_PROJECT_ENVIRONMENT=/opt/buffalo-venv", self.dockerfile)
        runtime = self.dockerfile.split(f"FROM {PYTHON_IMAGE} AS runtime", 1)[1]
        self.assertNotIn("/usr/local/bin/uv", runtime)
        self.assertNotRegex(runtime, r"\b(?:pip|uv) install\b")

    def test_runtime_packages_are_exact_and_version_pinned(self) -> None:
        self.assertNotRegex(
            self.dockerfile,
            r"(?m)^ARG (?:DEBIAN_SNAPSHOT|GIT_DEBIAN_VERSION|TINI_DEBIAN_VERSION)",
        )
        self.assertEqual(self.dockerfile.count("20260202T000000Z"), 3)
        self.assertIn("snapshot.debian.org/archive/debian/", self.dockerfile)
        self.assertIn("snapshot.debian.org/archive/debian-security/", self.dockerfile)
        self.assertIn("check-valid-until=no", self.dockerfile)
        install = self.dockerfile.split("apt-get install", 1)[1].split(
            "rm -rf /var/lib/apt/lists/*", 1
        )[0]
        self.assertIn('"git=1:2.39.5-0+deb12u3"', install)
        self.assertIn('"tini=0.19.0-1+b3"', install)
        self.assertIn("dpkg-query -W -f='${Version}' git", install)
        self.assertIn("dpkg-query -W -f='${Version}' tini", install)
        for forbidden in ("node", "npm", "pnpm", "postgresql-client", "curl"):
            self.assertNotRegex(install, rf"\b{re.escape(forbidden)}\b")

    def test_fixed_process_accounts_and_socket_groups_are_declared(self) -> None:
        flattened = re.sub(r"\\\n\s*", " ", self.dockerfile)
        expected_groups = {
            "1201": "buffalo-gateway",
            "1202": "buffalo-synthetic",
            "1203": "buffalo-research",
            "2301": "buffalo-synthetic-socket",
            "2302": "buffalo-research-socket",
            "2303": "buffalo-control-socket",
        }
        for gid, name in expected_groups.items():
            self.assertIn(f"groupadd --gid {gid} {name}", flattened)
        expected_users = {
            "1101": ("1201", "gateway", "buffalo-gateway"),
            "1102": ("1202", "synthetic", "buffalo-synthetic"),
            "1103": ("1203", "research", "buffalo-research"),
        }
        for uid, (gid, home, name) in expected_users.items():
            expression = re.compile(
                rf"useradd --uid {uid} --gid {gid} --no-create-home\s+"
                rf"--home-dir /run/buffalo-staging/{home}\s+"
                rf"--shell /usr/sbin/nologin {name}"
            )
            self.assertRegex(flattened, expression)
        self.assertIn(
            "buffalo-synthetic-socket,buffalo-research-socket,"
            "buffalo-control-socket",
            flattened,
        )

    def test_image_preserves_the_repo_shaped_runtime_root(self) -> None:
        self.assertIn("'/app/procurement/src'", self.dockerfile)
        self.assertIn("buffalo-procurement-os.pth", self.dockerfile)
        self.assertIn("COPY procurement/src /app/procurement/src", self.dockerfile)
        self.assertIn(
            "COPY procurement/config /app/procurement/config", self.dockerfile
        )
        self.assertIn("COPY procurement/db /app/procurement/db", self.dockerfile)
        self.assertNotRegex(self.dockerfile, r"(?m)^COPY\s+\.\s")
        self.assertNotIn("procurement/tests", self.dockerfile)
        self.assertNotIn("procurement/tools", self.dockerfile)
        for relative, digest in RESEARCH_AUTHORITY_FILES.items():
            source = ROOT / relative
            destination = f"/app/{relative}"
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
            self.assertIn(f"COPY {relative} {destination}", self.dockerfile)

    def test_build_context_is_default_deny(self) -> None:
        meaningful = tuple(
            line.strip()
            for line in self.dockerignore.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        self.assertEqual(meaningful[0], "**")
        self.assertEqual(
            frozenset(line for line in meaningful if line.startswith("!")),
            frozenset(
                {
                    "!pyproject.toml",
                    "!uv.lock",
                    "!procurement/",
                    "!procurement/src/",
                    "!procurement/src/**",
                    "!procurement/config/",
                    "!procurement/config/**",
                    "!procurement/db/",
                    "!procurement/db/**",
                    "!procurement/seed/",
                    "!procurement/seed/variant_aliases.csv",
                    "!procurement/review/",
                    "!procurement/review/phase4_identity_manifest_corrected.csv",
                    "!procurement/review/phase4_terminal_disposition_manifest.csv",
                }
            ),
        )
        for forbidden in (".env", "*.dump", "*.tar", "*credential*", "*secret*"):
            self.assertIn(forbidden, self.dockerignore)

    def test_pid1_execs_only_the_root_bootstrap(self) -> None:
        entrypoint = (
            'ENTRYPOINT ["/usr/bin/tini", "-g", "--", '
            '"/opt/buffalo-venv/bin/python", "-I", "-B", "-m", '
            '"procurement_os.staging_bootstrap"]'
        )
        self.assertIn("USER 0:0", self.dockerfile)
        self.assertIn("WORKDIR /app", self.dockerfile)
        self.assertIn("STOPSIGNAL SIGTERM", self.dockerfile)
        self.assertIn(entrypoint, self.dockerfile)
        self.assertNotRegex(self.dockerfile, r"(?m)^CMD\s")
        self.assertNotIn("ENTRYPOINT [\"/bin/sh\"", self.dockerfile)

    def test_image_contains_no_embedded_runtime_authority(self) -> None:
        for name in (
            "BUFFALO_STAGING_OWNER_VERIFIER",
            "BUFFALO_STAGING_SYNTHETIC_DATABASE_PASSWORD",
            "DATABASE_URL",
            "PGPASSWORD",
            "SHOPIFY_ACCESS_TOKEN",
        ):
            self.assertNotIn(name, self.dockerfile)
        self.assertNotRegex(self.dockerfile, r"(?im)^\s*(?:ARG|ENV)\s+.*(?:TOKEN|SECRET|PASSWORD)=")

    def test_railway_build_and_health_contract_is_exact(self) -> None:
        self.assertEqual(
            self.railway["$schema"], "https://railway.com/railway.schema.json"
        )
        self.assertEqual(
            self.railway["build"],
            {"builder": "DOCKERFILE", "dockerfilePath": "Dockerfile"},
        )
        deploy = self.railway["deploy"]
        self.assertEqual(deploy["numReplicas"], 1)
        self.assertEqual(deploy["healthcheckPath"], "/health")
        self.assertEqual(deploy["healthcheckTimeout"], 300)
        self.assertFalse(deploy["sleepApplication"])
        self.assertEqual(deploy["requiredMountPath"], "/data")

    def test_railway_runtime_is_single_instance_and_fail_closed(self) -> None:
        deploy = self.railway["deploy"]
        self.assertEqual(deploy["restartPolicyType"], "NEVER")
        self.assertIsNone(deploy["restartPolicyMaxRetries"])
        self.assertEqual(deploy["overlapSeconds"], 0)
        self.assertEqual(deploy["drainingSeconds"], 120)
        self.assertFalse(deploy["ipv6EgressEnabled"])
        self.assertIsNone(deploy["cronSchedule"])
        self.assertIsNone(deploy["preDeployCommand"])
        self.assertIsNone(deploy["startCommand"])
        self.assertIsNone(deploy["multiRegionConfig"])

    def test_railway_memory_limit_satisfies_the_sampler_contract(self) -> None:
        containers = self.railway["deploy"]["limitOverride"]["containers"]
        self.assertEqual(containers, {"memoryBytes": 5 * 1024**3 - 4096})
        self.assertLess(containers["memoryBytes"], 5 * 1024**3)

    def test_only_the_persistent_volume_is_declared(self) -> None:
        self.assertIn('ENV BUFFALO_STAGING_VOLUME_ROOT=/data', self.dockerfile)
        self.assertNotRegex(self.dockerfile, r"(?m)^VOLUME\s")
        self.assertNotRegex(self.dockerfile, r"(?m)^EXPOSE\s")
        self.assertIn("install --directory --owner=0 --group=0 --mode=0755", self.dockerfile)


if __name__ == "__main__":
    unittest.main()
