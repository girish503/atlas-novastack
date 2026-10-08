# ATLAS — Empirical Engineering Progression & Evolution Log

**Document Purpose**: Chronological audit of the hypothesis-driven development methodology that evolved ATLAS from baseline retrieval to final certification.
**Methodology**: `Problem Discovered` &rarr; `Hypothesis` &rarr; `Architectural Change` &rarr; `Measurement` &rarr; `Decision`.
**Repository Root**: . (relative workspace root)
**Certified HEAD Commit**: `6b8018b1e3d0864bcd2d2cae1319712bc13ae9d4`

---

## Evolution Timeline

```
[Phase 4A-D: Baseline Multi-Channel Hybrid Retrieval]
                      │
                      ▼ Entity Starvation Detected (Recall@10 = 0.6716, Entity Recall = 0.5182)
[Phase RET-EVAL-01..07: H1 to H4 Entity & Depth Ablations]
                      │
                      ▼ Shorthand & Alias Gaps Discovered
[Phase RET-EVAL-08: H5 / H5.1 Query Understanding Overlay]
                      │ (Entity Recall: 0.5182 ──> 0.7820, MRR: 0.4804 ──> 0.5525)
                      ▼
[Phase RET-EVAL-08S: H5.1 Shadow Evaluation & Failure Corpus Analysis]
                      │ (Proved Gemma 3 1B out-performed peer models on failure corpus)
                      ▼
[Phase RET-EVAL-09: Production Canary Integration & Live HTTP Gate]
                      │ (Integrated CanaryRouter into real FastAPI /query execution path)
                      ▼
[Phase SEC-OPS-02 / 03: Operational Security Hardening]
                      │ (SEC-OPS-02 loopback verified; SEC-OPS-03 LAN knocking unverified; Canary held at 0%)
                      ▼
[Phase 5: Release Verification & Demonstration Certification]
                      │ (94 pytest + 3 live scenarios = 97 verified cases; UI Truth separation)
                      ▼
[FINAL CERTIFIED SYSTEM: 6b8018b]
```

---

## Milestone 1: Multi-Channel Hybrid Baseline (Phase 4A – 4D)

- **Problem Discovered**: Dense semantic search alone failed to retrieve documents containing specific alphanumeric incident identifiers (`INC-NS-0001`), while pure BM25 failed on natural-language operational descriptions.
- **Hypothesis**: Fusing BM25 lexical retrieval and MiniLM dense semantic vectors via Reciprocal Rank Fusion ($k=60$) will capture both exact tokens and conceptual synonyms.
- **Architectural Change**: Created `BM25Index` (`src/novastack/bm25.py`), `DenseIndex` (`src/novastack/dense.py`), and `fuse_rrf_sum` (`src/novastack/depth_fusion_ablation.py`).
- **Measurement**: Baseline Positive Recall@10 reached `0.6716`, Mean Reciprocal Rank (MRR) was `0.4804`.
- **Decision**: **ACCEPTED**. Established multi-channel hybrid search as the core retrieval architecture.

---

## Milestone 2: Identification of Entity Starvation (RET-EVAL-01 – 07)

- **Problem Discovered**: On operational incident queries, retrieval accuracy dropped sharply. Analysis revealed that user queries frequently used shorthand (e.g., *"checkout outage"*), whereas source documents used canonical service identifiers (`checkout-service`) or postmortem incident IDs (`INC-NS-0001`). Baseline Expected Entity Recall was only `0.5182`.
- **Hypothesis (H1 – H4)**: Expanding queries with structured entity catalog lookups and alias mapping will increase candidate retrieval recall.
- **Measurement**: Iterative ablations (H1 through H4) improved entity capture but suffered from noise over-expansion on ambiguous general queries.
- **Decision**: **REJECTED UNCONSTRAINED EXPANSION**. Designed a controlled, catalog-bounded overlay (H5).

---

## Milestone 3: H5 / H5.1 Controlled Query Understanding Overlay (RET-EVAL-08)

- **Problem Discovered**: Shorthand queries required exact entity resolution across service aliases, incident n-grams, and deployment sequences without diluting general queries.
- **Hypothesis (H5.1)**: Building an in-memory catalog index of canonical entities, service descriptions, and incident tokens—and applying structural expansion only when entity anchors match—will significantly improve recall without hurting precision.
- **Architectural Change**: Implemented `H5_1EntityResolver` and `H5_1QueryUnderstandingOverlay` in `scripts/ret_eval_08_h5_1_experiment.py`.
- **Empirical Measurement**:
  - Expected Entity Recall: **0.5182 &rarr; 0.7820** (+50.9% relative increase)
  - Positive Recall@10: **0.6716 &rarr; 0.7277** (+8.35% relative increase)
  - Mean Reciprocal Rank (MRR): **0.4804 &rarr; 0.5525** (+15.0% relative increase)
  - Multi-Aspect Recall@10: **0.8167 &rarr; 0.9000** (+10.2% relative increase)
  - H2 Contrastive Recall@10: **0.6389 &rarr; 0.9167** (+43.5% relative increase)
- **Decision**: **ACCEPTED AS EVALUATED CANDIDATE (`0.4.14-rc1+h5.1`)**. Retained baseline `0.4.14-rc1` as immutable authority.

---

## Milestone 4: Shadow Evaluation & Peer Model Diagnosis (RET-EVAL-08S)

- **Problem Discovered**: Did remaining generation failures stem from retrieval defects or intrinsic LLM model capability limitations?
- **Hypothesis**: Testing identical evidence packages across competing small models (Gemma 3 1B vs. Qwen 2.5 1.5B) will isolate model vs. retrieval limitations.
- **Measurement**:
  - Diagnostic comparison on the failure corpus demonstrated that Gemma 3 1B answered multiple difficult multi-hop and causal queries (e.g., `EVAL-0018`, `EVAL-0024`) where Qwen 2.5 1.5B abstained.
  - Overall Positive Answer Yield reached **74 / 101 (73.27%)** with **100% Negative Safety (19/19)** and **100% Citation Precision (122/122)**.
- **Decision**: **REFUTED INTRINSIC BACKEND LIMITATION**. Confirmed failures were driven by prompt density and context dilution, not model capacity. Retained Gemma 3 1B.

---

## Milestone 5: Production Canary Integration (RET-EVAL-09)

- **Problem Discovered**: The H5.1 resolver existed as an offline evaluation script but was not reachable through the live FastAPI HTTP request path.
- **Hypothesis**: Injecting a deterministic `CanaryRouter` into the real `/query` path after JWT authentication and tenant verification will enable live HTTP routing between baseline and H5.1 resolvers without compromising fail-closed security.
- **Architectural Change**: Created `src/novastack/canary.py` and integrated `CanaryRouter` into `src/novastack/service/api.py`.
- **Measurement**: 13/13 live HTTP tests passed, verifying deterministic routing, kill-switch behavior, and that auth failures never reach canary routing.
- **Decision**: **CANARY ROUTING VERIFIED — TRAFFIC FROZEN AT 0.0%**.

---

## Milestone 6: Operational Security Hardening (SEC-OPS-02 / SEC-OPS-03)

- **Problem Discovered**: Standalone inference containers must not be reachable from unauthorized network interfaces.
- **Remediation**:
  - Bound all services strictly to `127.0.0.1` (`SEC-OPS-02: VERIFIED`).
  - Audited remote ingress knocking (`SEC-OPS-03`). Because only one physical machine was available on the local network, remote port knocking could not be executed from an independent physical host.
- **Decision**: **STRICT ENGINEERING INTEGRITY**. SEC-OPS-03 was honestly recorded as **`UNVERIFIED`**. Production canary traffic was strictly held at **`0.0%`**.

---

## Milestone 7: Final Release Certification & Demonstration Suite

- **Actions Taken**:
  - Eradicated all browser signing secrets; implemented pre-signed isolated demo credentials (`ui/demo_tokens.json`).
  - Added masked token preview and decoupled Display Title from Authorization Role.
  - Resolved the 33.85s cold-start PyTorch CPU weight loading analysis (`docs/FINAL_TIMEOUT_ANALYSIS.md`).
  - Reconciled all test counts: **94 distinct collected pytest tests + 3 live FastAPI scenarios = 97 verified cases passing**.
- **Final Decision**: **SYSTEM FROZEN AT COMMIT `6b8018b1e3d0864bcd2d2cae1319712bc13ae9d4`**. Ready for final demonstration and technical defense.
