# ATLAS CS-01-GHA-FIX-01 — Canary Harness Fixture Remediation Report

## 1. Executive Summary

| Attribute | Authoritative Value |
| :--- | :--- |
| **Phase** | `CS-01-GHA-FIX-01` |
| **Original Canary Run ID** | `37441579408` (Run #1) |
| **Original Decision** | `CANARY_FAIL` |
| **Failure Classification** | `CANARY_HARNESS_FIXTURE_MISMATCH` |
| **Production Source Modified** | **NO** (`src/novastack/` remains 100% frozen) |
| **Canary Workflow Modified** | **YES** (`.github/workflows/canary.yml`) |
| **Focused Tests Status** | **100% PASS** (38/38 pytest tests passed, AST validation clean) |
| **Release Artifacts State** | **IMMUTABLE & UNCHANGED** (Bit-for-bit SHA256 match) |
| **Canary Re-execution Status** | **NOT RE-EXECUTED** (Awaiting formal operator authorization) |
| **Phase Decision** | **`FIX_READY_FOR_REVIEW`** |

---

## 2. Failure Forensics & Diagnosis

### 2.1 The Observed Failure in Run #1
In GitHub Actions Run `#37441579408`, all infrastructure preflight steps succeeded completely:
- Ubuntu 24.04 runner provisioned
- Target commit `77c3c6b1ca48d10a99413fe3f3e03c696133e2f1` checked out
- Python 3.11.16 environment initialized
- Ollama service daemon installed and operational on port `11434`
- Gemma 3 1B Q4_K_M model (815 MB, digest `8648f39daa8fbf5b18c7b4e6a8fb4990c692751d49917417b8842ca5758e7ffc`) pulled and verified
- `QuantizedLocalProvider` initialized and reported ready

The failure occurred immediately upon entering Step 7 (Execute CS-01 Isolated Canary Probes), before any probe query was submitted to the local model:

```text
Traceback (most recent call last):
  File "<stdin>", line 123, in <module>
  File "<stdin>", line 77, in dict_to_evidence_pkg
TypeError: RecordPermissions.__init__() got an unexpected keyword argument 'required_clearance'
```

### 2.2 Root Cause Analysis
The inline script in `.github/workflows/canary.yml` defined a local conversion helper `dict_to_evidence_pkg`:

```python
# FAILING IMPLEMENTATION IN RUN #1:
p_raw = it.get("permissions", {})
perms = RecordPermissions(
    allowed_roles=p_raw.get("allowed_roles", ["engineer"]),
    allowed_departments=p_raw.get("allowed_departments", []),
    required_clearance=p_raw.get("required_clearance", "internal"),  # <-- DEFECT
)
```

1. **`RecordPermissions` Contract Definition**:
   In `src/novastack/models.py` (lines 222-230):
   ```python
   @dataclass
   class RecordPermissions:
       """Access control metadata for an observational source record."""
       allowed_roles: list[str] = field(default_factory=list)
       allowed_departments: list[str] = field(default_factory=list)
       allowed_teams: list[str] = field(default_factory=list)
       allowed_user_ids: list[str] = field(default_factory=list)
   ```
   `RecordPermissions` models identity-based access control lists (roles, departments, teams, user IDs).

2. **Classification/Clearance Separation**:
   In Project ATLAS, data security clearance / classification is modeled independently as an attribute of the record itself (`classification: str` on `SourceRecord` and `EvidenceItem`), taking values from `CLASSIFICATION_LEVELS = {"public", "internal", "confidential", "restricted"}`. In the canary harness, `EvidenceItem` already receives `classification=it.get("classification", "internal")`. Passing `required_clearance` into `RecordPermissions` was an erroneous attribute duplication.

3. **Secondary Contract Hazards Identified & Remediated**:
   Upon deep inspection of the inline canary harness, secondary argument mismatches were detected:
   - `EvidenceItem`: `canary.yml` passed `rank=...` and `metadata=...`, while omitting required fields (`retrieval_rank`, `retrieval_channels`, `updated_at`, `valid_from`, `valid_until`, `parent_id`, `supersedes_id`).
   - `EvidencePackage`: `canary.yml` omitted required positional arguments `evaluation_id`, `tenant_id`, and `user_context`.
   - Probes 4 and 5: Synthetic `EvidenceItem` constructions omitted positional attributes.

All three hazards have been brought into strict alignment with the canonical contracts established by `novastack.evidence` and verified by `scripts/run_phase_05_m8_experiment.py`.

---

## 3. Classification

- **Classification**: **`Case A: CANARY_HARNESS_FIXTURE_MISMATCH`**
- **Production Package / Source Scope**: Zero lines in `src/novastack/` are defective. Production source code is 100% compliant with architectural specifications and passed all certification gates.
- **Remediation Scope**: Strictly isolated to `.github/workflows/canary.yml`.

---

## 4. Minimal Remediation Applied

In `.github/workflows/canary.yml`:

1. **Import `EvidenceConflict`**: Added `EvidenceConflict` to the import list from `novastack.evidence`.
2. **Align `RecordPermissions` Instantiation**:
   ```python
   perms = RecordPermissions(
       allowed_roles=p_raw.get("allowed_roles", ["engineer"]),
       allowed_departments=p_raw.get("allowed_departments", []),
       allowed_teams=p_raw.get("allowed_teams", []),
       allowed_user_ids=p_raw.get("allowed_user_ids", []),
   )
   ```
3. **Align `EvidenceItem` and `EvidencePackage` Construction**:
   Populated all required dataclass fields (`retrieval_rank`, `retrieval_channels`, `evaluation_id`, `tenant_id`, `user_context`, `conflicts`).
4. **Introduced `make_evidence_item` Helper**:
   Clean factory providing standard baseline defaults for synthetic probe fixtures in Probes 4 and 5.

### Git Diff Stat
```text
 .github/workflows/canary.yml | 84 +++++++++++++++++++++++++++++++++++++++-----
 1 file changed, 75 insertions(+), 9 deletions(-)
```

---

## 5. Security & Authorization Impact Assessment

- **Authorization Semantics**: Completely preserved. Permissions continue to enforce RBAC, tenant isolation, and classification boundaries.
- **Fail-Closed Guarantees**: Intact. Probes 2 (`EVAL-0058`), 3 (`EVAL-0054`), 4 (Cross-tenant), and 5 (Adversarial injection) retain their exact evaluation conditions.
- **Production Isolation**: Zero modifications to production source code.

---

## 6. Local Focused & Regression Test Evidence

### 6.1 AST & Syntax Validation
- AST parser executed across the canary inline script: **PASS** (433 lines parsed cleanly).

### 6.2 Deterministic Fixture Conversion Verification
- `EVAL-0001` conversion: **PASS** (`PKG-EVAL-0001-1789038084`, 10 items)
- `EVAL-0058` conversion: **PASS** (`PKG-EVAL-0058-1789038089`, 10 items)
- `EVAL-0054` conversion: **PASS** (`PKG-EVAL-0054-1789038088`, 10 items)
- Probe 4 fixtures: **PASS** (1 selected, 1 excluded, cross-tenant boundary preserved)
- Probe 5 fixtures: **PASS** (1 selected, injection quarantined)

### 6.3 Pytest Regression Test Suite
Executed targeted authorization and M8 regression suite:
```text
tests/test_phase_4m_auth_fail_closed.py
tests/test_phase_05_m8_answerability.py
```
Result:
```text
======================== 38 passed, 1 warning in 1.66s ========================
```

---

## 7. Certified Release Artifacts Verification

Bit-for-bit SHA-256 verification against the immutable baseline manifests:

| Release Artifact | Expected SHA-256 | Actual SHA-256 | Verification |
| :--- | :--- | :--- | :--- |
| `dist/atlas-novastack-0.5.0-rc1.tar.gz` | `f9fe791595282bdd6e961e1383de69ee1a63cc52cb16ccdfed9645677b914936` | `f9fe791595282bdd6e961e1383de69ee1a63cc52cb16ccdfed9645677b914936` | **VERIFIED** |
| `dist/atlas-novastack-0.4.14-rc1.tar.gz` | `382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3` | `382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3` | **VERIFIED** |

---

## 8. Hard Safety Boundary Compliance

- [x] Remote CI / canary was **NOT** triggered
- [x] `workflow_dispatch` was **NOT** executed
- [x] Run #2 was **NOT** created
- [x] Production code (`src/novastack/`) was **NOT** modified
- [x] Production baseline `0.4.14` was **NOT** modified
- [x] Release candidates were **NOT** regenerated
- [x] Billing remains at `$0.00`

---

## 9. Final Decision

```text
FIX_READY_FOR_REVIEW
```
