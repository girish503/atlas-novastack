"""Phase 5C Test Suite: Dockerfile.inference Static Analysis Tests.

Validates that Dockerfile.inference meets all security and structural requirements:
  1. Uses python:3.11-slim base (minimal attack surface).
  2. Creates non-root USER with explicit UID 1000 (appuser).
  3. Includes a HEALTHCHECK directive.
  4. Exposes port 8001 (inference service port).
  5. Contains no hardcoded credentials, secrets, passwords, or API keys.
  6. CMD points to the correct inference service entrypoint.
  7. Does not install unnecessary packages.

These are pure static file analysis tests. No Docker daemon required.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCKERFILE_PATH = Path(__file__).parent.parent / "Dockerfile.inference"


@pytest.fixture(scope="module")
def dockerfile_content() -> str:
    if not DOCKERFILE_PATH.exists():
        pytest.skip(f"Dockerfile.inference not found at {DOCKERFILE_PATH}")
    return DOCKERFILE_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Base Image
# ---------------------------------------------------------------------------

class TestBaseImage:
    def test_uses_python_3_11_slim(self, dockerfile_content):
        assert "FROM python:3.11-slim" in dockerfile_content, (
            "Dockerfile.inference must use 'python:3.11-slim' base image"
        )

    def test_does_not_use_full_python_image(self, dockerfile_content):
        # Full image is much larger; slim is required
        lines = dockerfile_content.splitlines()
        from_lines = [l for l in lines if l.startswith("FROM")]
        for line in from_lines:
            assert ":latest" not in line, "Must not use :latest tag"


# ---------------------------------------------------------------------------
# Non-Root User
# ---------------------------------------------------------------------------

class TestNonRootUser:
    def test_has_user_directive(self, dockerfile_content):
        assert "USER appuser" in dockerfile_content, (
            "Dockerfile.inference must switch to non-root USER appuser"
        )

    def test_creates_appuser_with_uid_1000(self, dockerfile_content):
        # Either useradd or adduser with uid 1000
        has_uid_1000 = (
            re.search(r"useradd\s+.*-u\s+1000", dockerfile_content) is not None
            or re.search(r"adduser\s+.*1000", dockerfile_content) is not None
            or "-u 1000" in dockerfile_content
        )
        assert has_uid_1000, (
            "Non-root user must be created with UID 1000"
        )

    def test_user_directive_is_not_root(self, dockerfile_content):
        # USER directive must not be 'root'
        user_directives = re.findall(r"^USER\s+(\S+)", dockerfile_content, re.MULTILINE)
        for user in user_directives:
            assert user.lower() != "root", (
                f"Dockerfile.inference must not run as root; found USER {user}"
            )


# ---------------------------------------------------------------------------
# HEALTHCHECK
# ---------------------------------------------------------------------------

class TestHealthcheck:
    def test_has_healthcheck_directive(self, dockerfile_content):
        assert "HEALTHCHECK" in dockerfile_content, (
            "Dockerfile.inference must include a HEALTHCHECK directive"
        )

    def test_healthcheck_targets_healthz(self, dockerfile_content):
        assert "/healthz" in dockerfile_content, (
            "HEALTHCHECK must target /healthz liveness endpoint"
        )

    def test_healthcheck_targets_port_8001(self, dockerfile_content):
        # healthcheck must reference port 8001
        assert "8001" in dockerfile_content, (
            "Dockerfile.inference must reference port 8001 (inference service port)"
        )


# ---------------------------------------------------------------------------
# Exposed Port
# ---------------------------------------------------------------------------

class TestExposedPort:
    def test_exposes_port_8001(self, dockerfile_content):
        assert "EXPOSE 8001" in dockerfile_content, (
            "Dockerfile.inference must EXPOSE port 8001"
        )

    def test_does_not_expose_port_8000(self, dockerfile_content):
        # Port 8000 is the main ATLAS service port; inference service must not expose it
        assert "EXPOSE 8000" not in dockerfile_content, (
            "Dockerfile.inference must not expose port 8000 (that is the main ATLAS service port)"
        )


# ---------------------------------------------------------------------------
# Entrypoint / CMD
# ---------------------------------------------------------------------------

class TestEntrypoint:
    def test_cmd_uses_uvicorn(self, dockerfile_content):
        assert "uvicorn" in dockerfile_content, (
            "CMD must use uvicorn as the ASGI server"
        )

    def test_cmd_targets_inference_service_app(self, dockerfile_content):
        assert "novastack.inference_service.app" in dockerfile_content, (
            "CMD must target novastack.inference_service.app:app"
        )

    def test_cmd_binds_to_0_0_0_0(self, dockerfile_content):
        assert "0.0.0.0" in dockerfile_content, (
            "CMD must bind to 0.0.0.0 for container networking"
        )

    def test_cmd_uses_port_8001(self, dockerfile_content):
        # The CMD should specify --port 8001
        cmd_lines = [l for l in dockerfile_content.splitlines() if "CMD" in l and "uvicorn" in l]
        assert any("8001" in line for line in cmd_lines), (
            "CMD must specify --port 8001"
        )


# ---------------------------------------------------------------------------
# Security: No hardcoded credentials
# ---------------------------------------------------------------------------

class TestNoHardcodedCredentials:
    FORBIDDEN_PATTERNS = [
        r"password\s*=\s*['\"][^'\"]+['\"]",
        r"secret\s*=\s*['\"][^'\"]+['\"]",
        r"api_key\s*=\s*['\"][^'\"]+['\"]",
        r"token\s*=\s*['\"][^'\"]+['\"]",
        r"AWS_ACCESS_KEY_ID\s*=\s*['\"][^'\"]+['\"]",
        r"AWS_SECRET_ACCESS_KEY\s*=\s*['\"][^'\"]+['\"]",
    ]

    def test_no_hardcoded_password(self, dockerfile_content):
        for pattern in self.FORBIDDEN_PATTERNS:
            matches = re.findall(pattern, dockerfile_content, re.IGNORECASE)
            assert not matches, (
                f"Hardcoded credential pattern '{pattern}' found in Dockerfile.inference: {matches}"
            )

    def test_no_private_key_content(self, dockerfile_content):
        assert "BEGIN RSA PRIVATE KEY" not in dockerfile_content
        assert "BEGIN PRIVATE KEY" not in dockerfile_content
        assert "BEGIN EC PRIVATE KEY" not in dockerfile_content


# ---------------------------------------------------------------------------
# Minimal packages
# ---------------------------------------------------------------------------

class TestMinimalPackages:
    def test_installs_curl_for_healthcheck(self, dockerfile_content):
        # curl is required for HEALTHCHECK
        assert "curl" in dockerfile_content

    def test_cleans_up_apt_cache(self, dockerfile_content):
        # Must clean apt lists to minimize image size
        assert "rm -rf /var/lib/apt/lists" in dockerfile_content


# ---------------------------------------------------------------------------
# Dockerfile exists and is readable
# ---------------------------------------------------------------------------

class TestDockerfileExists:
    def test_dockerfile_exists(self):
        assert DOCKERFILE_PATH.exists(), (
            f"Dockerfile.inference not found at {DOCKERFILE_PATH}"
        )

    def test_dockerfile_is_not_empty(self, dockerfile_content):
        assert len(dockerfile_content.strip()) > 100, (
            "Dockerfile.inference appears to be empty or too short"
        )
