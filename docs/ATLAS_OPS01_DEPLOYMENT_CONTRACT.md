# ATLAS OPS-01 — Deployment & Configuration Contract

**Date:** 2026-10-06  
**Repository / HEAD:** `girish503/atlas-novastack` / `d325e5a82681456ebaca57f2f27c1900f17bd415`  
**Decision:** `SECURITY_REVIEW_REQUIRED`

## Decision

The API authorization boundary is implemented correctly: a verified HS256 JWT produces the trusted tenant, user, role, and department context before retrieval; tenant filtering and the `EvidenceResolver` operate before prompt construction. The inference service is deliberately not an authorization service and receives only the approved rendered prompt, generation controls, optional model name, and request ID.

However, the active runbook starts that unauthenticated inference service using `-p 8001:8001`, which normally publishes it on every host interface. Because `/generate` deliberately has no application authentication, this is a material unresolved deployment security boundary. OPS-01 therefore stops for a security-governance decision; it does not change source, workflows, archives, or the runbook.

## Canonical service topology

```text
Authenticated client
        |
        v
API :8000 — approved TLS/ingress boundary
        |  JWT verification; token-derived tenant/user/roles
        |  tenant-filtered retrieval; EvidenceResolver authorization
        v
Inference :8001 — loopback or private Docker bridge only
        |  rendered approved prompt only; no JWT or caller context
        v
Ollama :11434 — host-loopback or private runtime network only
```

The LLM is not and must never become the authorization boundary. Inference must not be internet-facing.

## Endpoint exposure contract

| Endpoint | Actual application auth | Contract exposure | Recommendation |
|---|---|---|---|
| API `GET /healthz` | None | Local/private liveness probe | Do not publish through public ingress. |
| API `GET /ready` | None | Private operator/orchestrator | Do not publish; it returns component state and active generation ID. |
| API `GET /metrics` | None | Private metrics collector/operator | Restrict by network/ingress policy. |
| API `POST /query` | Required HS256 Bearer JWT | Authenticated user-facing API | The sole internet-facing application endpoint, only behind an approved TLS/ingress boundary. |
| Inference `GET /healthz`, `GET /ready` | None | Private API/operator network | Do not publish. |
| Inference `POST /generate` | None by design | Strictly API-to-inference private network | Never publish or route through public ingress. |

The current code has no route-level access gate for health, readiness, or metrics. The contract therefore relies on an ingress/firewall/network policy. There is no repository compose file or deployment policy that enforces it today.

## Inference network truth table

| Deployment form | Who can reach inference port 8001? | Contract status |
|---|---|---|
| `-p 127.0.0.1:8001:8001` | Host-local clients only | Safe supported local topology. |
| `-p 8001:8001` | All host interfaces by Docker default; another machine may reach it if network/firewall permits | Unsafe for unauthenticated `/generate`; current runbook uses this. |
| Private Docker bridge, no `-p` | Only containers connected to that bridge | Safe supported container topology. Configure API with internal inference URL. |
| Host networking with service bind `0.0.0.0` | Host-wide; firewall-dependent | Not an acceptable inference default. |
| `EXPOSE 8001` alone | No host publication by itself | Not a security control; publishing controls exposure. |

`Dockerfile.inference` starts uvicorn on `0.0.0.0:8001`. This is acceptable only inside a private container namespace or with explicit loopback publishing. `docs/OPERATIONS_RUNBOOK.md:255-270` instead uses `-p 8001:8001`; that is the exact boundary drift requiring review.

### Minimum safe local deployment

- API: host loopback on `127.0.0.1:8000`, or a separately approved ingress that exposes only `/query` with TLS.
- Inference: `127.0.0.1:8001:8001` host publication, never unqualified `-p 8001:8001`.
- API value: `ATLAS_INFERENCE_SERVICE_URL=http://127.0.0.1:8001`.
- Ollama: loopback/private only on `11434`.

### Minimum safe container deployment

- Place API and inference containers on one private Docker bridge.
- Publish no inference port to the host.
- Give the API an internal inference URL such as `http://<private-inference-service-name>:8001`.
- The repository has no compose file; this is a supported network pattern, not a ready-to-run repository artifact.

If private/loopback network enforcement cannot be demonstrated, a separately approved service-authentication design is required before deploying inference.

## Canonical configuration

| Variable | Canonical | Required | Default | Secret | Used by | Meaning |
|---|---:|---:|---|---:|---|---|
| `ATLAS_AUTH_ISSUER` | Yes | Yes | — | No | API verifier | Expected JWT issuer. |
| `ATLAS_AUTH_AUDIENCE` | Yes | Yes | — | No | API verifier | Expected JWT audience. |
| `ATLAS_AUTH_HS256_SECRET` | Yes | Yes | — | **Yes** | API verifier | HS256 key; minimum 32 bytes. |
| `ATLAS_AUTH_CLOCK_SKEW_SECONDS` | Yes | No | `30` | No | API verifier | JWT skew; valid range 0–300. |
| `ATLAS_INFERENCE_SERVICE_URL` | Yes | No | `http://127.0.0.1:8001` | No | API adapter | API-to-inference private URL. |
| `ATLAS_INFERENCE_PROVIDER` | Yes | No | `inference_service` | No | API factory | Certified provider selection. |
| `ATLAS_MAX_CONCURRENT_QUERIES` | Yes, actual runtime key | No | `1` | No | ResilienceConfig | Runtime limiter override. |
| `ATLAS_REQUEST_TIMEOUT_SECONDS` | Yes | No | `30.0` | No | ResilienceConfig | API request deadline. |
| `ATLAS_QUEUE_TIMEOUT_SECONDS` | Yes | No | `0.5` | No | ResilienceConfig | Queue deadline before 429. |
| `ATLAS_ENABLE_CIRCUIT_BREAKER` | Yes | No | `true` | No | ResilienceConfig | Circuit breaker switch. |
| `ATLAS_CIRCUIT_FAILURE_THRESHOLD` | Yes | No | `3` | No | ResilienceConfig | Failures before circuit-open. |
| `ATLAS_CIRCUIT_COOLDOWN_SECONDS` | Yes | No | `10.0` | No | ResilienceConfig | Circuit recovery wait. |
| `ATLAS_WORKSPACE_ROOT` | Yes | No | auto-discovered | No | API factory | Root containing certified corpus/index assets. |
| `ATLAS_INFERENCE_BACKEND_URL` | Yes, inference-only | No | `http://127.0.0.1:11434` | No | Inference service | Inference-to-Ollama URL; **not** API-to-inference. |
| `ATLAS_INFERENCE_MODEL_NAME` | Yes, inference-only | No | `gemma3:1b` | No | Inference service | Ollama model. |
| `ATLAS_INFERENCE_CONNECT_TIMEOUT_SECONDS` | Yes, inference-only | No | `2.0` | No | Inference service | Backend readiness/connect deadline. |
| `ATLAS_INFERENCE_READ_TIMEOUT_SECONDS` | Yes, inference-only | No | `25.0` | No | Inference service | Backend generation deadline. |
| `ATLAS_INFERENCE_SERVICE_HOST`, `ATLAS_INFERENCE_SERVICE_PORT` | No for packaged Docker | No | `0.0.0.0`, `8001` | No | Config object | Docker CMD fixes host/port independently, so these do not reconfigure that container launch. |

Legacy aliases `INFERENCE_*` are accepted only by the inference-service config and should not appear in new deployment instructions. `ATLAS_MAX_CONCURRENT_INFERENCES` is documented but not read by runtime; it is inert. `ATLAS_JWT_*` is historical documentation only; production runtime uses `ATLAS_AUTH_*`.

### Exact documentation/configuration drift

- [Resilience runtime](/C:/Users/Adusumalli%20Girish/.gemini/antigravity/scratch/ATLAS/src/novastack/service/resilience.py:99) reads `ATLAS_MAX_CONCURRENT_QUERIES`; [operations runbook](/C:/Users/Adusumalli%20Girish/.gemini/antigravity/scratch/ATLAS/docs/OPERATIONS_RUNBOOK.md:186) documents inert `ATLAS_MAX_CONCURRENT_INFERENCES`.
- [Operations runbook](/C:/Users/Adusumalli%20Girish/.gemini/antigravity/scratch/ATLAS/docs/OPERATIONS_RUNBOOK.md:255) publishes the inference port broadly.
- [Phase 5K release manifest](/C:/Users/Adusumalli%20Girish/.gemini/antigravity/scratch/ATLAS/artifacts/phase_5k_release_manifest.json:90) calls `ATLAS_INFERENCE_BACKEND_URL` the API service URL, while current source uses it for the inference-to-Ollama URL.
- [Architecture review](/C:/Users/Adusumalli%20Girish/.gemini/antigravity/scratch/ATLAS/docs/ATLAS_0.5_ARCHITECTURE_REVIEW.md:198) references obsolete `ATLAS_JWT_*` names.

No documentation was edited in OPS-01; the listed release records remain historical provenance.

## Authentication, authorization, and data flow

`POST /query` verifies a Bearer JWT signature, issuer, audience, expiry, and claims. Missing/invalid authentication produces 503/401; a client context that conflicts with verified claims produces 403. The API then overwrites the body context with trusted claims, tenant-filters retrieval, and applies RBAC/ABAC/evidence authorization before generation.

Inference receives only the rendered evidence-grounded prompt, request ID, token limit, temperature, and optional model name. Its schema excludes JWTs, bearer credentials, caller context, tenant records, and raw retrieval collections. Inference response parsing is schema-validated; malformed responses become controlled failures rather than user answers. C2 citation validation remains downstream of generation.

## Fail-closed behavior

- Missing/short identity configuration: `/ready` 503 and `/query` 503 before retrieval.
- Invalid JWT: 401 before retrieval.
- Tenant/user/role/department mismatch: 403 before retrieval.
- Missing inference service or model: readiness failure and bounded 503 behavior.
- Timeout: 504; saturated configured capacity: 429.
- Invalid inference response: validation/error path; no ungrounded response is returned.
- A nonempty malformed inference URL fails readiness; an empty URL falls back only to loopback `127.0.0.1:8001`, not a public endpoint.

## Secrets

The sole runtime secret identified is `ATLAS_AUTH_HS256_SECRET`. Supply it through deployment-time secret injection to the API process/container. Do not log it, include it in client/frontend material, pass it to inference, embed it in URLs, commit it, or expose it in diagnostics. Existing structured logging and tracing redaction prohibit credential, prompt, query, document, and answer fields. No secret values are included in this report.

## Canary compatibility

The manual-only `workflow_dispatch` canary was inspected and not triggered. It verifies release hashes and uses a loopback Ollama daemon with a direct provider. It does not start the FastAPI API or inference-service container, so it does not validate this API/inference port boundary or its configuration naming. It remains compatible with the private-loopback principle but is not deployment-contract evidence.

## Resource envelope and non-production-ready items

Historical commissioning evidence supports a single CPU node (Intel Core i3-N305 class, approximately 8 GB RAM), Gemma 3 1B Q4_K_M, one concurrent inference, 0.5-second queue timeout, 30-second request timeout, 25-second inference read timeout, and 3-failure/10-second circuit breaker policy. OPS-01 did not rerun that commissioning.

Not production-ready for: public inference exposure; externally exposed API without an approved TLS/ingress and endpoint-access policy; multi-node/Kubernetes/multi-replica use; high-QPS/parallel generation; GPU claims; or any deployment that treats the LLM as an authorization layer.

## Deterministic checks performed

Read-only static inspection covered API routes, identity, resilience, provider and inference configurations, Dockerfiles, runbook, historical manifests, and the manual-only canary. Targeted in-memory checks passed: **43 passed** across `test_phase_4t_identity_boundary.py`, `test_phase_5c_inference_service.py`, and `test_phase_5c_inference_security.py`. No service was started and no network port was exposed.

## Required follow-up

**SEC-OPS-02 — Inference Boundary Remediation Decision:** formally choose and document an enforceable private/loopback-only inference network policy, or approve a minimum service-authentication design if that policy cannot be guaranteed. Then reconcile active operator documentation/config names in a separate documentation-only change. Do not alter certified release archives or historical provenance records.
