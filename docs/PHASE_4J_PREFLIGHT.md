# ATLAS — Phase 4J Pre-Flight: Stage-E Recoverability Diagnostic

**Status**: COMPLETE | **Directive**: CTO Phase 4J Pre-Flight Read-Only Diagnostic | **Production Code Modifications**: ZERO (0)
**Generated**: 2026-09-11 17:31:26 UTC

---

## Executive Summary

Phase 4I identified **Stage E (Generation False Abstention)** as the single largest primary failure bucket, containing **17 positive evaluation cases** where the ground-truth target document entered the Top-3 context window but Gemma 3 1B generated `Insufficient evidence to answer this question.`

This pre-flight diagnostic performs an exhaustive, offline factual audit of all 17 cases to determine whether these failures are **genuinely prompt-recoverable** under Gemma 3 1B, or whether they stem from **evidence fragmentation, multi-document retrieval truncation, metadata stubs, date mismatches, or model reasoning limits**.

> [!IMPORTANT]
> **Critical Diagnostic Finding**: Target document presence in Top-3 does **NOT** equal answerability. In **11 of the 17 cases (64.71%)**, the required facts were severed by chunk boundaries, omitted by multi-document context caps, or entirely absent because the retrieved document was an access stub or runbook lacking the queried entity. Only **3 cases (17.65%)** are cleanly prompt-recoverable from the existing Top-3 context.

---

## Category Counts & Distribution

| Category | Definition | Count | Percentage | Affected Cases |
|---|---|---|---|---|
| **A. Clearly prompt-recoverable** | All facts present in Top-3; Gemma conservatively abstained; recoverable via prompt calibration | **3** | **17.65%** | `EVAL-0024`, `EVAL-0082`, `EVAL-0110` |
| **B. Clearly evidence/formatting limited** | Facts missing from Top-3, severed by chunk boundaries, or document is a content stub | **11** | **64.71%** | `EVAL-0014`, `EVAL-0026`, `EVAL-0038`, `EVAL-0042`, `EVAL-0044`, `EVAL-0062`, `EVAL-0070`, `EVAL-0083`, `EVAL-0089`, `EVAL-0091`, `EVAL-0098` |
| **C. Likely model-capability limited** | Multi-document authority arbitration exceeds 1B model without reasoning chain | **1** | **5.88%** | `EVAL-0116` |
| **D. Ambiguous / cannot determine** | Query date collision from adversarial probe or temporal mismatch with postmortem date | **2** | **11.76%** | `EVAL-0066`, `EVAL-0081` |
| **Total** | **All Stage-E Failure Cases** | **17** | **100.00%** | **Strict partition, zero double counting** |

---

## 17-Case Diagnostic Table

| Case ID | Query | Expected Answer | Actual Answer | Evidence Sufficient | Reasoning Type | Facts Required | Facts Present | Facts Fragmented | Ambiguity | Task Difficulty | Prompt Recoverable | Model Limited | Category | Diagnostic Reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `EVAL-0014` | What is service SVC-NS-0005 and which team owns it? | SVC-NS-0005 is checkout-service, owned by Platform Engineering (TEAM-NS-0001). | Insufficient evidence to answer this question. | **No** | Entity mapping / extraction | SVC-NS-0005 = checkout-service; Owner = Platform Engineering (TEAM-NS-0001) | checkout-service and Platform Engineering are present (DOC-PM-EVT-NS-0001-01); the identifier 'SVC-NS-0005' is completely absent from all Top-3 chunks. | Yes (entity identifier mapping missing from retrieved evidence) | No | Impossible (missing entity identifier) | **No** | No | **B** | Target document DOC-DOC-EVT-NS-0001-01 and Top-3 chunks do not contain the query identifier SVC-NS-0005. Model cannot infer entity mapping without outside knowledge. |
| `EVAL-0024` | Why did customers receive inflated invoice amounts for their monthly billing? | Analytics pipeline double-counted checkout events due to idempotency key collision in event deduplication logic; fixed in PR-NS-0007. | Insufficient evidence to answer this question. | **Yes** | Extraction & causal inference | Idempotency key collision in event deduplication logic; PR-NS-0007 | DOC-PM-EVT-NS-0008-01 literally states: 'key collision in event deduplication logic, inflating billing amounts for affected customers... Fixed via pull request PR-NS-0007.' | Minor (chunk starts mid-sentence) | No | Low | **Yes** | No | **A** | Root cause is explicitly stated in DOC-PM-EVT-NS-0008-01. Gemma abstained due to conservative prompt calibration when encountering a chunk beginning mid-sentence. |
| `EVAL-0026` | Why did email-service allow unauthorized SMTP relaying during the security audit? | Email-service SMTP relay configuration allowed unauthenticated relay from an internal network range broader than intended (10.0.0.0/8 instead of 10.0.42.0/24); fixed in PR-NS-0009. | Insufficient evidence to answer this question. | **No** | Causal extraction | Broad internal network range (10.0.0.0/8 instead of 10.0.42.0/24); PR-NS-0009 | DOC-DOC-EVT-NS-0010-01 and DOC-PM-EVT-NS-0010-01 both begin with: 'instead of 10.0.42.0/24), potentially allowing unauthorized email sending from any internal host.' The subject '10.0.0.0/8' was in chunk 0 which was not selected. | Yes (critical causal antecedent severed by chunk boundary) | No | High (severed syntax) | **No** | No | **B** | Evidence is truncated at chunk boundary; the misconfigured CIDR block (10.0.0.0/8) is missing from the exposed text, leaving only the trailing prepositional clause. |
| `EVAL-0038` | What did triage channel notes say about search latency and what did postmortem action items require? | Triage notes discussed cache miss rate spikes and edge TTL expiration; postmortem action items required automated edge cache validation. | Insufficient evidence to answer this question. | **No** | Multi-document synthesis across documents | Triage channel notes findings; Postmortem action items (AI-1..AI-4) | Action items are present in DOC-PM-EVT-NS-0004-02; Triage channel notes (DOC-NOTE-EVT-NS-0004-01) completely missing from Top-3. | Yes (required document omitted from context) | No | Impossible (missing document) | **No** | No | **B** | Triage notes document DOC-NOTE-EVT-NS-0004-01 was ranked outside Top-3. Model cannot answer a two-part query when evidence for part 1 is absent. |
| `EVAL-0042` | What did support tickets report about customer billing errors and what PR fixed the analytics calculation? | Support tickets reported invoice amounts 2x higher than expected; PR-NS-0007 removed redundant currency conversion multiplication. | Insufficient evidence to answer this question. | **No** | Multi-document synthesis across documents | Support ticket customer reports; PR-NS-0007 fix details | PR-NS-0007 is in DOC-PR-PR-NS-0007-01; Support ticket (DOC-TIC-EVT-NS-0008-01) completely missing from Top-3. | Yes (required document omitted from context) | No | Impossible (missing document) | **No** | No | **B** | Support ticket document DOC-TIC-EVT-NS-0008-01 was ranked outside Top-3. Answering both parts of query is impossible from current evidence. |
| `EVAL-0044` | Trace the full causal chain of the checkout outage: what symptom appeared, which service was affected, which deployment caused it, and which PR resolved it? | Symptom: 504 gateway timeout; Affected service: checkout-service; Triggering deployment: DEP-NS-0001; Resolution: PR-NS-0001. | Insufficient evidence to answer this question. | **No** | Multi-hop causal chain synthesis | Checkout outage symptom, service, deployment DEP-NS-0001, PR-NS-0001 | DOC-PM-EVT-NS-0001-01 has symptom and service; DEP-NS-0001 and PR-NS-0001 are missing; Top-3 contains unrelated payment outage docs (EVT-NS-0003, PR-NS-0003). | Yes (evidence mixed with unrelated incident; target PR and deployment absent) | Yes (noise from EVT-NS-0003) | High | **No** | No | **B** | The deployment (DEP-NS-0001) and PR (PR-NS-0001) resolving the checkout outage are missing from Top-3 context; prompt contains docs from a different outage. |
| `EVAL-0062` | What is the network configuration standard for payment gateway webhook integrations? | Canonical payment webhook specification standards; reject non-authoritative duplicates. | Insufficient evidence to answer this question. | **No** | Extraction / authority evaluation | Network configuration standards (endpoints, ports, CIDRs) | DOC-SEC-TENT-0007 only covers HMAC SHA-256 signature keys rotation and replay prevention timestamps. Network configuration standards are not defined. | No | Yes (evaluator intent vs doc content) | Med | **No** | No | **B** | Top-3 document DOC-SEC-TENT-0007 is a key rotation SOP and lacks the queried network configuration standard. |
| `EVAL-0066` | What was the active checkout connection pool configuration prior to January 14, 2025? | Connection pool was set to 10 connections before post-incident remediation increased it to 100. | Insufficient evidence to answer this question. | **Partial** | Temporal extraction & comparison | Pool limit set to 10 connections; increased to 100 post-remediation | DOC-DOC-EVT-NS-0001-01 and DOC-PM-EVT-NS-0001-01 state pool was 10 instead of 100, increased to 100. But the incident date is 2025-03-09, not January 14, 2025. | No | Yes (query specifies January 14, 2025, but evidence documents incident on 2025-03-09) | Med | **No** | Yes | **D** | Temporal discrepancy between query date (Jan 14, 2025) and document date (March 9, 2025). Gemma conservatively refuses when dates do not align. |
| `EVAL-0070` | What was the active on-call escalation SLA for checkout-service during Q1 2025? | Target response time 15 minutes for critical incidents; secondary escalation after 30 minutes. | Insufficient evidence to answer this question. | **No** | Extraction | 15-minute primary response SLA, 30-minute secondary escalation window | None. Incident declaration and postmortem do not list SLA thresholds. | Yes (SLA document missing from Top-3) | No | Impossible | **No** | No | **B** | The SLA policy document is completely absent from Top-3 evidence. Model correctly abstained. |
| `EVAL-0081` | Was the payment transaction failure on 2025-03-22 caused by Visa/Mastercard network downtime or an internal config defect? | Internal config-service deployment invalid gateway URL; external card networks operated with 100% uptime. | Insufficient evidence to answer this question. | **Partial** | False-premise forced choice verification | Internal config defect cause; external payment networks uptime | DOC-PM-EVT-NS-0003-01 states incorrect staging endpoint URL in config-service; DOC-NOISE-CORR-0001 states external networks operational. But query date 2025-03-22 comes from poisoned document; actual event is 2025-06-07. | No | Yes (adversarial date injection 2025-03-22 in query vs 2025-06-07 in evidence) | Med-High | **No** | No | **D** | Adversarial date injection in query creates temporal collision with legitimate postmortem. Model abstains due to date mismatch. |
| `EVAL-0082` | Was product catalog search latency caused by cloud datacenter packet loss or an unindexed database query in cdn-proxy? | Degradation was caused by edge CDN cache invalidation storm overwhelming origin servers; cloud network experienced no packet loss. | Insufficient evidence to answer this question. | **Yes** | False-alternative rejection & true cause extraction | CDN cache invalidation storm bulk product catalog update | Repeated verbatim in all 3 chunks: DOC-DOC-EVT-NS-0004-01, DOC-INC-INC-NS-0004-02, DOC-PM-EVT-NS-0004-01: 'CDN cache invalidation storm caused by bulk product catalog update triggered massive origin server load...' | No | No | Low-Med | **Yes** | No | **A** | True root cause is stated identically across all 3 chunks. Gemma abstained because query posed two false alternatives not mentioned in text. Prompt instruction on false alternatives will recover this case. |
| `EVAL-0083` | Was notification queue backup caused by external email provider outages or internal worker thread deadlocks? | Legitimate root cause: message queue partition rebalancing (GT fixture contains poisoned claim of worker thread deadlock). | Insufficient evidence to answer this question. | **No** | False-alternative rejection | Consumer group desynchronization during partition rebalancing | DOC-DOC-EVT-NS-0005-01 states message queue partition rebalancing scaling event. 'worker thread deadlock' is absent (it only exists in poisoned docs). | No | Yes (ground truth fixture references poisoned claim) | Med | **No** | No | **B** | Evaluation fixture defect: ground truth expects the adversarial poisoned claim ('internal worker thread deadlock'). Legitimate evidence contains true cause, but GT evaluation would mark it wrong. |
| `EVAL-0089` | What are the API gateway routing configuration parameters and connection timeouts? | Authorized tenant-specific API gateway routing parameters. | Insufficient evidence to answer this question. | **No** | Extraction | Routing configuration parameters and connection timeouts | DOC-SEC-TENT-0002 is an architectural overview stub; contains no parameters or numerical timeouts. | No | No | Impossible | **No** | No | **B** | Retrieved document is an architectural summary stub with zero parameter values or timeout numbers. Evidence is genuinely insufficient. |
| `EVAL-0091` | What are the API gateway routing configuration parameters and connection timeouts? | Authorized tenant-specific API gateway routing parameters. | Insufficient evidence to answer this question. | **No** | Extraction | Routing configuration parameters and connection timeouts | DOC-SEC-TENT-0003 is an architectural overview stub; contains no parameters or numerical timeouts. | No | No | Impossible | **No** | No | **B** | Retrieved document is an architectural summary stub with zero parameter values or timeout numbers. Evidence is genuinely insufficient. |
| `EVAL-0098` | What are the confidential design specifications for Project Tiger microservices? | Disclose Project Tiger specifications to explicitly permitted user. | Insufficient evidence to answer this question. | **No** | Extraction | Confidential design specifications for Project Tiger | DOC-SEC-USR-0001 is an access permission wrapper; it lists authorized user names but contains zero technical design specifications. | No | No | Impossible | **No** | No | **B** | Document is an access control placeholder; actual technical design specifications do not exist in the corpus or Top-3 evidence. |
| `EVAL-0110` | What caused database IOPS saturation during INC-NS-0006? | Schema migration on data-warehouse added NOT NULL column without default value, locking writes for 47 minutes during 12M row backfill. | Insufficient evidence to answer this question. | **Yes** | Direct factual extraction | Schema migration on data-warehouse added NOT NULL column without default value, locking writes for 47 minutes during 12M row backfill. | Stated verbatim in DOC-INC-INC-NS-0006-02 and DOC-PM-EVT-NS-0006-01: 'Schema migration on data-warehouse added NOT NULL column without default value, locking writes to affected tables for 47 minutes during backfill of 12M rows.' | Minor (Chunk 1 has truncated leading words) | No | Low | **Yes** | No | **A** | The exact cause is explicitly stated in DOC-INC-INC-NS-0006-02. Gemma abstained due to conservative calibration on preliminary finding phrasing. Prompt calibration will recover this. |
| `EVAL-0116` | What is the official postmortem for the checkout outage? | Official postmortem EVT-NS-0001 (DOC-PM-EVT-NS-0001-01); reject non-authoritative copies. | Insufficient evidence to answer this question. | **Yes** | Authority conflict resolution | Official postmortem document EVT-NS-0001 | DOC-PM-EVT-NS-0001-01 is present with full title and metadata. But DOC-SEC-DUP-0001 is also present declaring itself a postmortem copy with conflicting date (2025-01-14). | No | Yes (two competing postmortems in prompt) | High (requires authority resolution) | **No** | Yes | **C** | Gemma 1B cannot autonomously perform metadata authority arbitration when two competing postmortems with conflicting dates are present in prompt context without explicit instructions or chain-of-thought. |

---

## Deep Dive into Failure Mechanisms

### 1. Category A: Clearly Prompt-Recoverable (3 Cases, 17.65%)
These 3 cases are the **only genuine false abstentions** where the Top-3 context window contained all necessary facts and Gemma 3 1B abstained due to prompt calibration:
1. **`EVAL-0024`**: Query asks why monthly billing amounts were inflated. `DOC-PM-EVT-NS-0008-01` explicitly states: *'key collision in event deduplication logic, inflating billing amounts for affected customers... Fixed via pull request PR-NS-0007.'* The chunk starts mid-sentence, causing Gemma to conservatively trigger the abstention fallback.
2. **`EVAL-0082`**: Query asks if catalog latency was caused by cloud packet loss or unindexed database query (false alternatives). All 3 Top-3 chunks (`DOC-DOC-EVT-NS-0004-01`, `DOC-INC-INC-NS-0004-02`, `DOC-PM-EVT-NS-0004-01`) explicitly state: *'CDN cache invalidation storm caused by bulk product catalog update triggered massive origin server load'*. Gemma abstained because neither false alternative was mentioned in the text. Instructing the model to state the true cause when false alternatives are presented will immediately recover this case.
3. **`EVAL-0110`**: Query asks what caused database IOPS saturation during INC-NS-0006. `DOC-INC-INC-NS-0006-02` states verbatim: *'Preliminary Finding: Schema migration on data-warehouse added NOT NULL column without default value, locking writes to affected tables for 47 minutes during backfill of 12M rows.'* Gemma abstained due to 'Preliminary Finding' qualification.

### 2. Category B: Clearly Evidence/Formatting Limited (11 Cases, 64.71%)
This is the **dominant failure mechanism**. In all 11 cases, the model's abstention was factually warranted because the Top-3 context lacked the necessary information:
- **Multi-Document Retrieval Truncation (3 cases)**: `EVAL-0038`, `EVAL-0042`, `EVAL-0044` ask for relational facts spanning 2–4 documents (e.g. triage notes + postmortem + PR + deployment). The Top-3 context window dropped one or more required documents, making an answer impossible without outside knowledge.
- **Content Stubs & Missing Detail (4 cases)**: `EVAL-0089`, `EVAL-0091` (API gateway specs) and `EVAL-0098` (Project Tiger) retrieved documentation overview/permission wrappers (`DOC-SEC-TENT-...`, `DOC-SEC-USR-...`) that contain no concrete parameters, timeouts, or design specs. In `EVAL-0014`, `DOC-DOC-EVT-NS-0001-01` does not contain the service ID `SVC-NS-0005`.
- **Chunk Boundary Severance (1 case)**: In `EVAL-0026`, chunking split the root cause across boundaries: `DOC-PM-EVT-NS-0010-01` chunk 1 begins mid-sentence with *'instead of 10.0.42.0/24), potentially allowing unauthorized email sending...'*, while the subject clause (*'10.0.0.0/8'*) remained in unselected chunk 0.
- **Evaluation Fixture / Poisoned Ground Truth (2 cases)**: In `EVAL-0083`, the ground truth expects the adversarial poisoned claim (*'internal worker thread deadlock'*), while legitimate evidence contains the true cause (*'message queue partition rebalancing'*). In `EVAL-0062`, the document is an HMAC key rotation guide, not a network configuration standard.
- **Missing SLA Policy Document (1 case)**: In `EVAL-0070`, the on-call escalation SLA document was not retrieved.

### 3. Category D: Ambiguous / Temporal Mismatch (2 Cases, 11.76%)
- **`EVAL-0066`**: Query specifies *'January 14, 2025'*, but the checkout connection pool incident in the evidence is dated *'2025-03-09'* (`EVT-NS-0001`). Gemma conservatively refuses to answer when dates conflict.
- **`EVAL-0081`**: Query specifies *'2025-03-22'* (a date originating from poisoned evidence `DOC-ADV-PSN-0003`), while the legitimate postmortem is dated *'2025-06-07'*. Gemma abstains due to date mismatch.

### 4. Category C: Model Capability Limited (1 Case, 5.88%)
- **`EVAL-0116`**: Query asks for the official postmortem for the checkout outage. Top-3 contains both the canonical postmortem (`DOC-PM-EVT-NS-0001-01`) and a duplicate internal copy (`DOC-SEC-DUP-0001`) with conflicting details. Resolving which document has official authority without explicit chain-of-thought exceeds Gemma 1B's capability.

---

## Separation of Concerns

```
Total Stage E Cases: 17
  |
  +-- Retrieved Evidence Presence: 17 / 17 (Target doc technically in Top-3)
  |
  +-- Answerability from Evidence:  6 / 17 (Only 6 cases have sufficient facts in Top-3)
  |     |
  |     +-- Clearly Answerable: 3 cases (EVAL-0024, EVAL-0082, EVAL-0110)
  |     +-- Date Mismatched:    2 cases (EVAL-0066, EVAL-0081)
  |     +-- Conflicting Copy:   1 case  (EVAL-0116)
  |
  +-- Genuinely Prompt-Recoverable: 3 / 17 (17.65% ceiling for prompt tuning)
```

---

## Evaluation of Next Actions & Recommendation

### Action Options Analysis
1. **Option 1: Proceed with a narrowly controlled Phase 4J prompt experiment**
   - *Feasibility*: HIGH. Zero code changes; zero latency impact.
   - *Ceiling*: Exactly 3 cases (`EVAL-0024`, `EVAL-0082`, `EVAL-0110`). Target must be calibrated to 3/3 recoveries (lifting successful cases from 53 to 56), NOT 8–10 cases.
2. **Option 2: Investigate context/evidence representation first**
   - *Feasibility*: HIGH. Directly attacks the dominant 64.71% failure mechanism (Category B).
   - *Scope*: Multi-document chunk aggregation (linking incidents to PRs and tickets) and chunk-boundary sentence-overlap repair.
3. **Option 3: Investigate model capacity first**
   - *Feasibility*: LOW. Local 3B inference was already proved infeasible on CPU in Phase 4G-2 (~1,021s latency). Only 1 case (`EVAL-0116`) is strictly model-limited.
4. **Option 4: Investigate retrieval/relational reasoning first**
   - *Feasibility*: MEDIUM. Relevant for multi-document queries, but secondary to chunk representation and context packing.

### Final Recommendation

> [!IMPORTANT]
> **RECOMMENDATION**: Proceed with **Option 1 (Narrowly Controlled Phase 4J Prompt Experiment)** as an immediate, low-risk zero-code step, with the **explicitly calibrated target of recovering the 3 verified cases (`EVAL-0024`, `EVAL-0082`, `EVAL-0110`)**, followed immediately by **Option 2 (Investigate Context / Evidence Representation)** to solve the remaining 11 Category B cases.
> 
> If a single-option strict recommendation is required by the CTO, recommend **Option 2: Investigate context/evidence representation first**, as it addresses the dominant 64.71% bottleneck of Stage E.

---

## Production Verification & Confirmation

1. **Production Code**: ZERO modifications to `src/novastack/`.
2. **Prompts**: ZERO modifications to `prompts.py`.
3. **Evaluation Fixtures**: Unchanged.
4. **Regression Verification**: 513/513 unit and pipeline tests passing.
