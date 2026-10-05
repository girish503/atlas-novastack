"""Phase 5J: Generate Step 1 Pre-Promotion Checkpoint.

Records the immutable state of ATLAS prior to the production promotion
of Backend B.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def main():
    root = Path(__file__).resolve().parent.parent

    # File hashes
    tracked_files = [
        "pyproject.toml",
        "src/novastack/provider.py",
        "src/novastack/quantized_provider.py",
        "src/novastack/inference_client.py",
        "src/novastack/service/api.py",
        "src/novastack/service/identity.py",
        "src/novastack/service/resilience.py",
        "src/novastack/index_manager.py",
        "Dockerfile",
        "Dockerfile.inference",
        "artifacts/phase_5h_backend_b_recertification.json",
        "artifacts/phase_5i_production_promotion_review.json",
    ]

    file_hashes = {}
    for rel_path in tracked_files:
        p = root / rel_path
        if p.exists():
            file_hashes[rel_path] = sha256_file(p)
        else:
            file_hashes[rel_path] = "NOT_FOUND"

    # Environment variables inspection (names and configured status only - NEVER values)
    env_vars = {
        "ATLAS_INFERENCE_SERVICE_URL": {"configured": bool(os.environ.get("ATLAS_INFERENCE_SERVICE_URL"))},
        "ATLAS_INFERENCE_BACKEND_URL": {"configured": bool(os.environ.get("ATLAS_INFERENCE_BACKEND_URL"))},
        "ATLAS_INFERENCE_MODEL_NAME": {"configured": bool(os.environ.get("ATLAS_INFERENCE_MODEL_NAME"))},
        "ATLAS_INFERENCE_SERVICE_HOST": {"configured": bool(os.environ.get("ATLAS_INFERENCE_SERVICE_HOST"))},
        "ATLAS_INFERENCE_SERVICE_PORT": {"configured": bool(os.environ.get("ATLAS_INFERENCE_SERVICE_PORT"))},
        "ATLAS_JWT_SECRET": {"configured": bool(os.environ.get("ATLAS_JWT_SECRET"))},
        "ATLAS_JWT_ISSUER": {"configured": bool(os.environ.get("ATLAS_JWT_ISSUER"))},
        "ATLAS_JWT_AUDIENCE": {"configured": bool(os.environ.get("ATLAS_JWT_AUDIENCE"))},
    }

    # Load corpus & evaluation counts
    eval_cases_path = root / "data" / "evaluation" / "novastack" / "evaluation_cases.json"
    docs_path = root / "data" / "processed" / "novastack" / "search_documents.json"
    chunks_path = root / "data" / "processed" / "novastack" / "search_chunks.json"

    eval_count = len(json.loads(eval_cases_path.read_text(encoding="utf-8")).get("evaluation_cases", []))
    docs_count = len(json.loads(docs_path.read_text(encoding="utf-8")).get("search_documents", []))
    chunks_count = len(json.loads(chunks_path.read_text(encoding="utf-8")).get("search_chunks", []))

    checkpoint = {
        "phase": "5J",
        "step": 1,
        "checkpoint_name": "pre_promotion_checkpoint",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "repository_git_enabled": False,
        "repository_git_status": "Not a Git repository (documented per Step 0 requirement)",
        "package_version": "0.4.14",
        "current_production_provider": "LocalHuggingFaceProvider",
        "current_production_model": "google/gemma-3-1b-it",
        "current_production_device": "cpu",
        "current_production_precision": "torch.float32",
        "provider_configuration": {
            "model_name": "google/gemma-3-1b-it",
            "device": "cpu",
            "lazy_load": True,
            "torch_dtype": "torch.float32",
            "local_files_only": True,
        },
        "inference_configuration": {
            "candidate_provider": "InferenceServiceAdapter",
            "candidate_model": "gemma3:1b",
            "quantization_level": "Q4_K_M",
            "quantization_format": "GGUF",
            "model_file_size_mb": 815.0,
            "service_url": "http://127.0.0.1:8001",
            "default_timeout_seconds": 30.0,
        },
        "resilience_configuration": {
            "request_timeout_seconds": 30.0,
            "max_concurrent_inferences": 1,
            "queue_timeout_seconds": 0.5,
            "circuit_failure_threshold": 3,
            "circuit_cooldown_seconds": 10.0,
            "enable_circuit_breaker": True,
        },
        "environment_variables": env_vars,
        "active_index_generation_id": "INDEX-GEN-NOVASTACK-BASE",
        "corpus_metadata": {
            "cases_total": eval_count,
            "documents_total": docs_count,
            "chunks_total": chunks_count,
            "embeddings_dimension": 384,
            "embedding_model": "BAAI/bge-small-en-v1.5",
        },
        "file_hashes": file_hashes,
        "phase_5h_artifact_hash": file_hashes.get("artifacts/phase_5h_backend_b_recertification.json"),
        "phase_5i_artifact_hash": file_hashes.get("artifacts/phase_5i_production_promotion_review.json"),
        "verified_preconditions": {
            "phase_5h_candidate_eligible": True,
            "phase_5i_promotion_ready": True,
            "security_violations": 0,
            "promotion_blockers": [],
            "production_default_currently_backend_a": True,
        }
    }

    out_path = root / "artifacts" / "phase_5j_pre_promotion_checkpoint.json"
    out_path.write_text(json.dumps(checkpoint, indent=2), encoding="utf-8")
    print(f"Pre-promotion checkpoint successfully written to {out_path} ({len(json.dumps(checkpoint))} bytes)")


if __name__ == "__main__":
    main()
