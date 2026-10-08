# ATLAS — Public Proof & Verification Evidence Matrix

**Document Purpose**: Maps every primary architectural and security claim made about ATLAS to concrete test suites, benchmarks, and repository artifacts.
**Rule**: No assertion is published without reproducible empirical evidence.
**Repository Root**: . (relative workspace root)
**Certified HEAD Commit**: `6b8018b1e3d0864bcd2d2cae1319712bc13ae9d4`

---

## Authoritative Claim-to-Evidence Matrix

| Category | Claim / Assertion | Empirical Evidence | Artifact / Code Location | Verified Status |
| :--- | :--- | :--- | :--- | :--- |
| **Retrieval** | Hybrid BM25+Dense retrieval outperforms single channels | Incompatible score distributions fused via RRF ($k=60$); captures both exact tokens and synonyms | [`artifacts/canonical_retrieval_benchmark.json`](../artifacts/canonical_retrieval_benchmark.json) | ✅ **VERIFIED** |
| **Retrieval** | H5.1 boosts entity recall by +50.9% | Expected Entity Recall increased from 0.5182 to 0.7820; Positive Recall@10 from 0.6716 to 0.7277 | [`artifacts/ret_eval_08_h5_1_results.json`](../artifacts/ret_eval_08_h5_1_results.json) | ✅ **VERIFIED** |
| **Retrieval** | H5.1 improves Mean Reciprocal Rank (MRR) | MRR improved from 0.4804 to 0.5525 (+15.0% relative) across canonical evaluation queries | [`scripts/ret_eval_08_h5_1_experiment.py`](../scripts/ret_eval_08_h5_1_experiment.py) | ✅ **VERIFIED** |
| **Security** | Authentication fails closed on invalid credentials | Missing, expired, forged, or malformed JWT tokens return HTTP 401 Unauthorized | [`tests/test_phase_4t_identity_boundary.py`](../tests/test_phase_4t_identity_boundary.py) | ✅ **VERIFIED** |
| **Security** | Client context cannot override server JWT claims | Server-side verified claims unconditionally override client JSON body; role escalation returns HTTP 403 | [`tests/security/test_red_team_harness.py`](../tests/security/test_red_team_harness.py) (7.2, 7.9) | ✅ **VERIFIED** |
| **Security** | Strict cross-tenant isolation (0% leakage) | Queries targeting foreign tenant documents produce 0 chunks in context and 0 unauthorized citations | [`tests/test_phase_4m_auth_fail_closed.py`](../tests/test_phase_4m_auth_fail_closed.py) | ✅ **VERIFIED** |
| **Security** | Direct prompt injection neutralized | Instruction override payloads treated strictly as passive query text; privileges preserved | [`tests/security/test_red_team_harness.py`](../tests/security/test_red_team_harness.py) (7.4) | ✅ **VERIFIED** |
| **Security** | Indirect prompt injection demarcated | Corpus documents enclosed inside untrusted `<evidence_data>` tags (Rule 4) | [`src/novastack/generation.py`](../src/novastack/generation.py#L120) | ✅ **VERIFIED** |
| **Security** | Retrieval poisoning rejected by authority model | Trusted catalog metadata overrides self-declared deceptive document claims | [`tests/security/test_red_team_harness.py`](../tests/security/test_red_team_harness.py) (7.6) | ✅ **VERIFIED** |
| **Security** | Zero browser signing secrets in client code | AST and regex audit confirmed no HS256 secret keys or private keys in client JavaScript | [`tests/test_frontend_integration.py`](../tests/test_frontend_integration.py#L22) | ✅ **VERIFIED** |
| **Generation** | 100% negative safety on unanswerable queries | 19 / 19 unanswerable or out-of-scope evaluation queries produced safe abstention | [`artifacts/canonical_generation_benchmark.json`](../artifacts/canonical_generation_benchmark.json) | ✅ **VERIFIED** |
| **Generation** | 100% citation precision | 122 / 122 emitted citations verified by C2 engine as existing, grounded, and authorized | [`artifacts/canonical_citation_audit.json`](../artifacts/canonical_citation_audit.json) | ✅ **VERIFIED** |
| **Generation** | Positive answer yield = 73.27% | 74 / 101 positive answerable queries answered accurately with grounded citations | [`docs/FINAL_RELEASE_CERTIFICATION.md`](FINAL_RELEASE_CERTIFICATION.md) | ✅ **VERIFIED** |
| **Performance**| Pre-LLM retrieval executes in < 60ms | Hybrid retrieval p50 = 41.36ms (p95 = 57.71ms); Metadata reranking p50 = 0.32ms | [`artifacts/canonical_performance_report.json`](../artifacts/canonical_performance_report.json) | ✅ **VERIFIED** |
| **Performance**| CPU generation latency dominates response time | Gemma 3 1B token generation on CPU averages 13.36s (p50 = 13.92s, p95 = 21.84s) | [`docs/FINAL_TIMEOUT_ANALYSIS.md`](FINAL_TIMEOUT_ANALYSIS.md) | ✅ **VERIFIED** |
| **Performance**| PyTorch CPU cold-start weight loading takes ~33.5s | Initial instantiation of MiniLM model tensors takes 33.51s; warm queries execute in < 55ms | [`docs/FINAL_TIMEOUT_ANALYSIS.md`](FINAL_TIMEOUT_ANALYSIS.md) | ✅ **DISCLOSED & VERIFIED** |
| **Operations** | Canary router integrated into live /query path | Live HTTP canary routing functional; kill-switch returns 100% baseline routing | [`tests/test_live_http_canary.py`](../tests/test_live_http_canary.py) | ✅ **VERIFIED** |
| **Operations** | Production canary traffic held at 0.0% | Traffic percentage configured to 0.0%; H5.1 is evaluated candidate, not production authority | [`src/novastack/canary.py`](../src/novastack/canary.py) | ✅ **VERIFIED (0.0%)** |
| **Operations** | SEC-OPS-02 loopback network binding | FastAPI and inference services bind strictly to `127.0.0.1` | [`tests/test_sec_ops02_network_contract.py`](../tests/test_sec_ops02_network_contract.py) | ✅ **VERIFIED** |
| **Operations** | SEC-OPS-03 remote LAN ingress knocking | Physical multi-machine external port knocking could not be executed on single-machine testbed | [`docs/FINAL_RELEASE_CERTIFICATION.md`](FINAL_RELEASE_CERTIFICATION.md) | ⚠️ **UNVERIFIED (HONEST DISCLOSURE)** |
| **End-to-End** | 94 unit/integration tests passing | 94 distinct pytest tests collected and executed with 0 failures | [`docs/FINAL_RELEASE_CERTIFICATION.md`](FINAL_RELEASE_CERTIFICATION.md) | ✅ **VERIFIED (94/94)** |
| **End-to-End** | 3 live demonstration scenarios passing | Real FastAPI service pipeline executed all 3 demo scenarios with HTTP 200 OK | [`scripts/verify_live_scenarios.py`](../scripts/verify_live_scenarios.py) | ✅ **VERIFIED (3/3)** |
| **Total**      | 97 combined test cases passing | 94 pytest tests + 3 live FastAPI scenarios = 97 verified cases | Single-invocation test execution | ✅ **VERIFIED (97/97)** |
