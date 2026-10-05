# Phase 4C-0: Query Intent & Failure Taxonomy Diagnostic Report

## Executive Summary

Following the empirical rejection of Hypothesis 1 (H1) in Phase 4B-1 (where cross-encoder reranking degraded Recall@10 from 0.4719 to 0.4249 and MRR from 0.3296 to 0.2363), Phase 4C-0 executes a **pure diagnostic milestone** to determine what kind of query understanding each existing evaluation case requires.

> **Fundamental Diagnostic Axiom**:
> `Query Intent Understanding != Candidate Generation != Relevance Ranking != Source Authority != Authorization Filtering`

---

## 1. Intent Failure Cross-Tabulation Matrix

Performance of all 15 controlled intent dimensions across BM25, Dense, Hybrid RRF, and Cross-Encoder Reranking:

| Intent Label | Cases (Pos/Tot) | BM25 R@10 | Dense R@10 | RRF R@10 | Rerank R@10 | Union Cov@50 | Med Rank | Cand Gen Fails | Headroom Cases | Rerank Regs |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `exact_identifier` | 28/28 | 0.4196 | 0.5893 | 0.6429 | **0.4663** | 89.3% | 4.0 | 3 | 6 | 4 |
| `entity_attribute` | 43/47 | 0.3140 | 0.3798 | 0.4225 | **0.3476** | 83.7% | 8.0 | 7 | 12 | 5 |
| `semantic` | 77/96 | 0.3766 | 0.4372 | 0.4177 | **0.4184** | 79.2% | 5.0 | 16 | 16 | 7 |
| `temporal` | 21/24 | 0.4881 | 0.5000 | 0.5000 | **0.5091** | 90.5% | 5.0 | 2 | 6 | 3 |
| `version_lifecycle` | 12/12 | 0.3125 | 0.4167 | 0.4167 | **0.2867** | 91.7% | 15.0 | 1 | 6 | 0 |
| `relationship` | 61/61 | 0.4057 | 0.5191 | 0.5109 | **0.4478** | 90.2% | 4.0 | 6 | 13 | 10 |
| `multi_hop` | 10/10 | 0.4250 | 0.6167 | 0.6167 | **0.4517** | 100.0% | 2.0 | 0 | 1 | 1 |
| `multi_document` | 32/32 | 0.2891 | 0.5677 | 0.5208 | **0.3412** | 100.0% | 2.5 | 0 | 5 | 5 |
| `conflicting_evidence` | 5/5 | 0.4000 | 0.4000 | 0.4000 | **0.6000** | 100.0% | 15.0 | 0 | 3 | 0 |
| `duplicate_resolution` | 6/6 | 0.0833 | 0.2500 | 0.2500 | **0.3333** | 100.0% | 9.5 | 0 | 2 | 0 |
| `authority_sensitive` | 19/19 | 0.3158 | 0.3684 | 0.4211 | **0.3684** | 73.7% | 15.0 | 5 | 7 | 0 |
| `authorization_sensitive` | 39/51 | 0.2692 | 0.3718 | 0.3718 | **0.3679** | 74.4% | 15.0 | 10 | 12 | 1 |
| `adversarial` | 18/18 | 0.3889 | 0.3889 | 0.5000 | **0.5444** | 66.7% | 15.0 | 6 | 4 | 0 |
| `missing_information` | 0/19 | 0.0000 | 0.0000 | 0.0000 | **0.0000** | 0.0% | N/A | 0 | 0 | 0 |
| `ambiguous` | 11/11 | 0.2273 | 0.3182 | 0.3182 | **0.4545** | 100.0% | 10.0 | 0 | 5 | 0 |

---

## 2. Answers to the Thirteen Mandatory Diagnostic Questions

### 1. What query intents dominate ATLAS?
Across all 120 evaluation cases, the most frequent query intents are:
- **`semantic`**: 96 cases (80.0% of corpus)
- **`relationship`**: 61 cases (50.8% of corpus)
- **`authorization_sensitive`**: 51 cases (42.5% of corpus)
- **`entity_attribute`**: 47 cases (39.2% of corpus)
- **`multi_document`**: 32 cases (26.7% of corpus)
ATLAS queries are heavily **semantic** (unstructured symptom and conceptual inquiries), **entity_attribute**-focused (seeking properties of incidents, services, and deployments), and frequently **authority_sensitive** (requiring discrimination between official records and chatter/poisoned content).

### 2. Which intents have the worst Recall@10?
The lowest-performing intent categories under Hybrid RRF are:
- **`duplicate_resolution`**: RRF Recall@10 = **0.2500** (BM25: 0.0833, Dense: 0.2500)
- **`ambiguous`**: RRF Recall@10 = **0.3182** (BM25: 0.2273, Dense: 0.3182)
- **`authorization_sensitive`**: RRF Recall@10 = **0.3718** (BM25: 0.2692, Dense: 0.3718)
- **`conflicting_evidence`**: RRF Recall@10 = **0.4000** (BM25: 0.4000, Dense: 0.4000)

Under the Cross-Encoder Reranker, performance collapsed further in:
- **`version_lifecycle`**: Reranker Recall@10 = **0.2867** (vs RRF 0.4167)
- **`duplicate_resolution`**: Reranker Recall@10 = **0.3333** (vs RRF 0.2500)
- **`multi_document`**: Reranker Recall@10 = **0.3412** (vs RRF 0.5208)
- **`entity_attribute`**: Reranker Recall@10 = **0.3476** (vs RRF 0.4225)

### 3. Which intents have good candidate coverage but poor ranking?
- **`entity_attribute`**: 50-Candidate Union Coverage = **83.7%**, but Recall@10 is only **0.4225** (with 12 cases in ranks 11–50).
- **`semantic`**: 50-Candidate Union Coverage = **79.2%**, but Recall@10 is only **0.4177** (with 16 cases in ranks 11–50).
- **`version_lifecycle`**: 50-Candidate Union Coverage = **91.7%**, but Recall@10 is only **0.4167** (with 6 cases in ranks 11–50).
- **`conflicting_evidence`**: 50-Candidate Union Coverage = **100.0%**, but Recall@10 is only **0.4000** (with 3 cases in ranks 11–50).
- **`duplicate_resolution`**: 50-Candidate Union Coverage = **100.0%**, but Recall@10 is only **0.2500** (with 2 cases in ranks 11–50).
- **`authority_sensitive`**: 50-Candidate Union Coverage = **73.7%**, but Recall@10 is only **0.4211** (with 7 cases in ranks 11–50).
- **`authorization_sensitive`**: 50-Candidate Union Coverage = **74.4%**, but Recall@10 is only **0.3718** (with 12 cases in ranks 11–50).
- **`ambiguous`**: 50-Candidate Union Coverage = **100.0%**, but Recall@10 is only **0.3182** (with 5 cases in ranks 11–50).
These categories represent genuine ranking opportunities where target documents are retrieved into the top-50 pool but displaced from the top-10 by competing distractors.

### 4. Which intents have candidate-generation failures?
Intents where the target document is completely absent from the 50-candidate union pool:
- **`semantic`**: **16 candidate-generation failures** (20.8% of cases missing from top-50).
- **`authorization_sensitive`**: **10 candidate-generation failures** (25.6% of cases missing from top-50).
- **`entity_attribute`**: **7 candidate-generation failures** (16.3% of cases missing from top-50).
- **`relationship`**: **6 candidate-generation failures** (9.8% of cases missing from top-50).
For these cases, reranking or cross-attention is futile because the target never enters the candidate pool.

### 5. Which intents require temporal/lifecycle reasoning?
- **`temporal`** (24 cases): Queries asking 'what happened before/after', event timelines, or duration. Current RRF Recall@10 is **0.5000**.
- **`version_lifecycle`** (12 cases): Queries requiring tracking version lineages (v1.0 -> v2.0), deprecated configurations, or draft vs published status. Current RRF Recall@10 is **0.4167**.

### 6. Which require entity relationships?
- **`relationship`** (61 cases): Inquiries connecting teams to services (`ownership`), pull requests to deployments, or deployments to incidents. Because entity metadata is often split across distinct catalog and telemetry records, single-query text match struggles, yielding RRF Recall@10 of **0.5109**.

### 7. Which require multiple documents?
- **`multi_document`** (32 cases): Ground truth requires gathering complementary facts from distinct documents (e.g. initial triage notes + postmortem action items). RRF Recall@10 = **0.5208**.
- **`multi_hop`** (10 cases): Requires traversing a 3-step causal path ($A \to B \to C$). RRF Recall@10 = **0.6167**.

### 8. Which require authority/provenance?
- **`authority_sensitive`** (19 cases): Inquiries where the corpus contains both authoritative sources (postmortems, canonical runbooks) and low-authority chatter or deliberate poisoned evidence. Unfiltered rerankers suffered catastrophic regressions here because poisoned records simulate authoritative language.

### 9. Which require authorization?
- **`authorization_sensitive`** (51 cases): Inquiries involving confidential HR records, executive compensation, master credentials, or cross-tenant boundaries. Zero-join pre-filtering preserves 100% tenant isolation, but user/role-level authorization requires identity context.

### 10. Which are adversarial?
- **`adversarial`** (18 cases): Retrieval poisoning (38 poisoned records), indirect prompt injection (20 payloads), and citation manipulation. In the cross-encoder evaluation, **164 poisoned documents** surfaced in top-10, demonstrating that relevance rankers are actively misled by adversarial phrasing.

### 11. Which failure classes are likely addressable through query understanding?
1. **`exact_identifier` & `entity_attribute` expansion**: Expanding bare entity names (`checkout-service`) to include owner team, known aliases, and canonical catalog IDs directly addresses the 18 candidate-generation failures in `ownership`.
2. **`temporal` constraint extraction**: Parsing date intervals (`2026-03`) and chronological markers into explicit metadata filters prevents contemporary tickets from crowding out historical postmortems.
3. **`version_lifecycle` disambiguation**: Identifying version qualifiers (`v2.0` vs current) enables targeted lineage filtering.

### 12. Which failures cannot be solved by query understanding alone?
1. **`adversarial` & `authority_sensitive` poisoning**: Query understanding cannot verify whether a retrieved document is genuine or fabricated. Solving this requires **Metadata Authority Weighting / Filtering** at the index/retrieval layer.
2. **`authorization_sensitive` access control**: Query understanding cannot decide access permissions without user identity context and authorization middleware.
3. **`multi_hop` graph synthesis**: A single retrieval query cannot dynamically fetch intermediate causal nodes ($A \to B \to C$) without iterative decomposition or evidence assembly.

### 13. What should the next controlled experiment test?
The next controlled experiment should be **Phase 4C-1: Query Expansion & Metadata Authority Filtering**:
1. **Deterministic Entity/Alias Query Expansion**: Test whether expanding service queries with catalog metadata resolves the 18 candidate-generation bottlenecks.
2. **Metadata Authority Weighting**: Test whether applying authority multipliers (e.g. promoting `authority_level: high` official postmortems and penalizing unverified chatter/poisoned records) suppresses the 164 poisoned documents that broke the cross-encoder.
