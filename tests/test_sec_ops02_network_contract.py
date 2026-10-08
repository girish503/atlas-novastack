"""SEC-OPS-02 deterministic deployment-boundary contract checks.

These tests are static by design: they must never start Docker, publish a
port, or claim a host firewall has been verified. A real deployment check can
optionally inspect a named running container through
ATLAS_SEC_OPS02_CONTAINER_NAME; otherwise it reports NOT_VERIFIED explicitly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
LOOPBACK_PUBLISH = "-p 127.0.0.1:8001:8001"
PUBLIC_OLLAMA_PUBLISH = re.compile(r"(?:\-p|\-\-publish)\s+(?:0\.0\.0\.0:)?11434:11434")
PUBLISH_FLAG_LINE = re.compile(r"^\s*-p\s+([^\s`]+)", re.MULTILINE)
FENCED_COMMAND_BLOCK = re.compile(r"```(?:bash|powershell)\s*(.*?)```", re.DOTALL)


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _fenced_command_blocks(content: str) -> list[str]:
    return FENCED_COMMAND_BLOCK.findall(content)


def _docker_binding_status() -> tuple[str, str]:
    """Return VERIFIED or explicit NOT_VERIFIED; never claim absent Docker is safe."""
    container_name = os.environ.get("ATLAS_SEC_OPS02_CONTAINER_NAME", "").strip()
    if not container_name:
        return "NOT_VERIFIED", "ATLAS_SEC_OPS02_CONTAINER_NAME is not configured"
    if shutil.which("docker") is None:
        return "NOT_VERIFIED", "docker CLI is unavailable"
    try:
        completed = subprocess.run(
            ["docker", "inspect", container_name],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        inspect_data = json.loads(completed.stdout)[0]
        bindings = inspect_data.get("NetworkSettings", {}).get("Ports", {}).get("8001/tcp") or []
        host_ips = {entry.get("HostIp") for entry in bindings}
        if host_ips == {"127.0.0.1"}:
            return "VERIFIED", "8001/tcp is published only to 127.0.0.1"
        return "NOT_VERIFIED", f"unexpected 8001/tcp HostIp bindings: {sorted(host_ips)}"
    except Exception as exc:
        return "NOT_VERIFIED", f"docker inspection unavailable: {type(exc).__name__}"


def test_active_runbook_publishes_inference_only_to_loopback():
    runbook = _read("docs/OPERATIONS_RUNBOOK.md")
    assert PUBLISH_FLAG_LINE.findall(runbook) == ["127.0.0.1:8001:8001", "127.0.0.1:8001:8001"]
    assert all("--network host" not in block for block in _fenced_command_blocks(runbook))
    assert "Never use an unqualified" in runbook


def test_historical_reference_paths_make_loopback_the_safe_default():
    checklist = _read("docs/PHASE_5D_RUNTIME_CHECKLIST.md")
    freeze_script = _read("scripts/phase_5k_release_freeze.py")
    assert PUBLISH_FLAG_LINE.findall(checklist) == ["127.0.0.1:8001:8001"]
    assert LOOPBACK_PUBLISH in freeze_script
    assert ' -p 8001:8001 ' not in freeze_script
    assert ' -p 0.0.0.0:8001:8001 ' not in freeze_script
    assert all("--network host" not in block for block in _fenced_command_blocks(checklist))
    assert "Historical/reference checklist" in checklist
    assert "SEC-OPS-02" in freeze_script


def test_api_to_inference_contract_remains_host_loopback():
    provider = _read("src/novastack/provider.py")
    runbook = _read("docs/OPERATIONS_RUNBOOK.md")
    assert 'os.environ.get("ATLAS_INFERENCE_SERVICE_URL", "http://127.0.0.1:8001")' in provider
    assert "ATLAS_INFERENCE_SERVICE_URL=http://127.0.0.1:8001" in runbook
    assert "host API process" in runbook


def test_no_active_or_reference_docker_launch_publishes_ollama():
    for relative_path in (
        "docs/OPERATIONS_RUNBOOK.md",
        "docs/PHASE_5D_RUNTIME_CHECKLIST.md",
        "scripts/phase_5k_release_freeze.py",
    ):
        assert not PUBLIC_OLLAMA_PUBLISH.search(_read(relative_path)), relative_path


def test_runnable_historical_harnesses_fail_closed_instead_of_public_ollama_startup():
    for relative_path in (
        "scripts/phase_5o_incident_recovery.py",
        "scripts/phase_5p_final_commissioning.py",
    ):
        content = _read(relative_path)
        assert 'env["OLLAMA_HOST"] = "0.0.0.0:11434"' not in content
        assert "SEC-OPS-02 READINESS BLOCKED" in content
        assert "automatic Ollama startup is disabled" in content


def test_container_host_binding_is_verified_or_explicitly_not_verified():
    status, detail = _docker_binding_status()
    assert status in {"VERIFIED", "NOT_VERIFIED"}
    if status == "VERIFIED":
        assert detail == "8001/tcp is published only to 127.0.0.1"
    else:
        assert detail


def test_ollama_platform_containment_is_explicitly_not_claimed_as_verified():
    runbook = _read("docs/OPERATIONS_RUNBOOK.md")
    assert "Without that platform verification, deployment readiness is **BLOCKED**." in runbook
    assert "No Docker port publication for `11434`" in runbook
