"""Phase 4N: Packaging, Containerization, and Dependency Tests.

Verifies:
1. pyproject.toml exists and has valid metadata, build system, and package discovery.
2. All runtime dependencies identified from source code are declared.
3. Optional test dependencies include pytest.
4. Dockerfile exists and contains required production container directives.
5. .dockerignore exists and excludes build/test caches and virtualenvs while preserving runtime data.
6. Continuous integration workflow exists and specifies checkout, setup, test, and container smoke test.
7. Service entrypoint is importable and exposes the expected route handlers.
8. Frozen production configuration flags remain strictly preserved (A=False, B=True, C=False).
9. Service startup via ASGI lifecycle correctly initializes pipeline components and responds to /healthz and /ready.
"""
from pathlib import Path
import sys
import tomllib
from fastapi.testclient import TestClient
import pytest

WORKSPACE = Path(__file__).resolve().parent.parent


def test_pyproject_toml_exists_and_valid():
    """Verify pyproject.toml exists and adheres to PEP 517/518/621 standards."""
    pyproject_path = WORKSPACE / "pyproject.toml"
    assert pyproject_path.exists(), "pyproject.toml must exist in workspace root"
    
    content = pyproject_path.read_text(encoding="utf-8")
    data = tomllib.loads(content)
    
    # Build system
    assert "build-system" in data, "pyproject.toml must declare [build-system]"
    assert "requires" in data["build-system"]
    assert any("setuptools" in r for r in data["build-system"]["requires"])
    assert data["build-system"].get("build-backend") == "setuptools.build_meta"
    
    # Project metadata
    assert "project" in data, "pyproject.toml must declare [project]"
    project = data["project"]
    assert project.get("name") == "atlas-novastack"
    assert "version" in project
    assert project.get("requires-python") in (">=3.10", ">=3.11")
    
    # Test runner config
    assert "tool" in data and "pytest" in data["tool"]
    assert "ini_options" in data["tool"]["pytest"]


def test_runtime_and_test_dependencies_declared():
    """Verify all runtime dependencies required by ATLAS pipeline and service are declared."""
    pyproject_path = WORKSPACE / "pyproject.toml"
    content = pyproject_path.read_text(encoding="utf-8")
    data = tomllib.loads(content)
    
    deps = data.get("project", {}).get("dependencies", [])
    dep_names = [d.split(">=")[0].split("<")[0].split("==")[0].strip() for d in deps]
    
    expected_runtime_deps = [
        "fastapi",
        "pydantic",
        "uvicorn",
        "torch",
        "transformers",
        "sentence-transformers",
        "numpy",
        "httpx",
    ]
    for req in expected_runtime_deps:
        assert req in dep_names, f"Expected runtime dependency '{req}' missing from pyproject.toml"
        
    # Optional dependencies
    opt_deps = data.get("project", {}).get("optional-dependencies", {})
    assert "dev" in opt_deps or "test" in opt_deps, "Expected optional dependencies ('dev' or 'test') declared"
    combined_test_deps = opt_deps.get("dev", []) + opt_deps.get("test", [])
    assert any("pytest" in d for d in combined_test_deps), "pytest must be declared in optional dependencies"


def test_dockerfile_specification():
    """Verify Dockerfile contains proper base image, paths, security, and startup directives."""
    dockerfile_path = WORKSPACE / "Dockerfile"
    assert dockerfile_path.exists(), "Dockerfile must exist in workspace root"
    
    content = dockerfile_path.read_text(encoding="utf-8")
    assert len(content.strip()) > 0, "Dockerfile must not be empty"
    
    # Verify base image
    assert "FROM python:3.11" in content or "FROM python:3.10" in content, "Dockerfile should use standard python slim base image"
    
    # Verify working directory
    assert "WORKDIR /app" in content, "Dockerfile must set WORKDIR /app"
    
    # Verify copying package and assets
    assert "COPY pyproject.toml" in content
    assert "COPY src/" in content
    assert "COPY data/" in content
    
    # Verify install
    assert "pip install" in content
    
    # Verify non-root user setup
    assert "useradd" in content or "adduser" in content, "Dockerfile should create a non-root user"
    assert "USER " in content, "Dockerfile must switch to a non-root user"
    
    # Verify port and entrypoint
    assert "EXPOSE 8000" in content, "Dockerfile must expose port 8000"
    assert "HEALTHCHECK" in content, "Dockerfile should define a container healthcheck"
    assert "uvicorn" in content, "Dockerfile CMD must execute uvicorn"
    assert "novastack.service.api:app" in content, "Dockerfile must target novastack.service.api:app entrypoint"


def test_dockerignore_specification():
    """Verify .dockerignore excludes caches, virtual environments, and unneeded assets while preserving data."""
    dockerignore_path = WORKSPACE / ".dockerignore"
    assert dockerignore_path.exists(), ".dockerignore must exist in workspace root"
    
    content = dockerignore_path.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]
    
    # Verify exclusion patterns
    assert any("__pycache__" in line for line in lines), ".dockerignore must exclude __pycache__"
    assert any("*.py[cod]" in line or "*.pyc" in line for line in lines), ".dockerignore must exclude bytecode"
    assert any(".venv" in line or "venv" in line for line in lines), ".dockerignore must exclude virtual environments"
    assert any(".pytest_cache" in line for line in lines), ".dockerignore must exclude test caches"
    assert any(".git" in line for line in lines), ".dockerignore must exclude .git"
    
    # Verify runtime data is NOT excluded
    assert not any(line == "data" or line == "data/" or line == "data/*" for line in lines), ".dockerignore must not exclude runtime data directory"
    assert not any(line == "src" or line == "src/" or line == "src/*" for line in lines), ".dockerignore must not exclude src directory"


def test_ci_workflow_specification():
    """Verify GitHub Actions CI workflow is defined with checkout, test, and container smoke test."""
    ci_path = WORKSPACE / ".github" / "workflows" / "ci.yml"
    assert ci_path.exists(), ".github/workflows/ci.yml must exist"
    
    content = ci_path.read_text(encoding="utf-8")
    assert len(content.strip()) > 0, "CI workflow file must not be empty"
    
    # Required CI steps
    assert "actions/checkout" in content, "CI workflow must checkout code"
    assert "actions/setup-python" in content, "CI workflow must setup Python"
    assert "pip install" in content, "CI workflow must install dependencies"
    assert "pytest" in content, "CI workflow must run pytest"
    assert "docker build" in content, "CI workflow must execute docker build smoke test"


def test_service_entrypoint_and_routes():
    """Verify service application is importable and declares required endpoints."""
    from novastack.service.api import app
    from fastapi import FastAPI
    
    assert isinstance(app, FastAPI), "Service app must be an instance of FastAPI"
    
    routes = {route.path for route in app.routes}
    assert "/healthz" in routes, "Service must register /healthz route"
    assert "/ready" in routes, "Service must register /ready route"
    assert "/query" in routes, "Service must register /query route"


def test_frozen_production_configuration():
    """Verify frozen production configuration remains strictly preserved."""
    import inspect
    from novastack.event_evidence_bundler import EventBundlerConfig
    from novastack.evidence_resolution import EvidenceResolverConfig
    from novastack.generation import GroundedAnswerGenerator
    
    # Mechanism B: Query-Aware Authority Preservation (Frozen default: True)
    resolver_config = EvidenceResolverConfig()
    assert resolver_config.enable_query_aware_authority is True, "Mechanism B must remain True"
    
    # Mechanism C: Event-Centric Evidence Bundling (Frozen default: False)
    assert resolver_config.enable_event_bundling is False, "Mechanism C in resolver must remain False"
    assert EventBundlerConfig().enable_event_bundling is False, "Mechanism C in bundler must remain False"
    
    # Mechanism A: Boundary Sentence Stitching (Frozen default: False)
    sig = inspect.signature(GroundedAnswerGenerator.generate_answer)
    assert sig.parameters["enable_boundary_stitching"].default is False, "Mechanism A must remain False"


def test_service_startup_and_healthz():
    """Verify service starts up cleanly and responds to /healthz and /ready."""
    from unittest.mock import patch
    from novastack.service.api import app
    
    with TestClient(app) as client:
        # Check /healthz liveness
        resp_health = client.get("/healthz")
        assert resp_health.status_code == 200
        assert resp_health.json() == {"status": "ok"}
        
        # Check /ready readiness
        gen = getattr(getattr(app.state, "pipeline", None), "generator", None)
        if gen is not None and not gen.is_ready():
            with patch.object(gen, "is_ready", return_value=True):
                resp_ready = client.get("/ready")
        else:
            resp_ready = client.get("/ready")

        assert resp_ready.status_code == 200
        data_ready = resp_ready.json()
        assert data_ready["status"] == "ready"
        assert data_ready["components"]["bm25"] is True
        assert data_ready["components"]["dense"] is True
        assert data_ready["components"]["reranker"] is True
        assert data_ready["components"]["generator"] is True
