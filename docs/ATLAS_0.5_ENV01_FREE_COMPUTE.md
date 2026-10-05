# ATLAS 0.5 — Free Canary Execution Environment Discovery (ENV-01)

**Role:** ATLAS Release Engineer / CTO  
**Date:** 2026-10-05  
**Governing Reviews:** PRR-01 (`PROMOTION_READY`), CR-01 (`CANARY_READY`), CS-01 Local Feasibility (`RED`)  
**Release Candidate:** `dist/atlas-novastack-0.5.0-rc1.tar.gz` (SHA256: `f9fe791595282bdd6e961e1383de69ee1a63cc52cb16ccdfed9645677b914936`)  
**Official Decision:** **`FREE_ENVIRONMENT_FOUND`**  

---

## 1. Executive Summary & Verdict

Following the `RED` feasibility determination on the local Windows workstation (1.31 GB free physical RAM available against an execution envelope requiring $\ge 2.5\text{ GB}$), an exhaustive, zero-cost environment discovery was conducted.

### Official Verdict: **`FREE_ENVIRONMENT_FOUND`**

A fully zero-cost, unbilled, and architecturally aligned execution environment is available: **GitHub Actions Hosted Linux Runner (`ubuntu-latest`)**.

### Invariants Maintained
- **ATLAS source code:** Unmodified.
- **Production baseline (0.4.14):** Unmodified and frozen.
- **Release candidate (0.5.0-rc1):** Intact, immutable, and SHA256-verified.
- **Benchmarks & Reviews:** Zero reruns of M8, PRR-01, or CR-01.
- **Canary status:** CS-01 was **NOT** executed during this discovery phase.
- **Financial impact:** **$0.00**. No cloud resources provisioned, no payment information submitted.

---

## 2. ATLAS-Specific Execution Envelope

The required runtime envelope is defined strictly by empirical measurements recorded during prior ATLAS phases:

| Component | Observed Footprint | Role in CS-01 |
|---|---:|---|
| **Gemma 3 1B Q4_K_M Weight File** | 815.0 MB | Static model artifact |
| **Active In-Memory Model Footprint** | ~850–1,024 MB | Live weights + KV cache during generation |
| **Inference Service Adapter / Container** | ~350–400 MB | HTTP boundary proxy (`atlas-inference-5d`) |
| **ATLAS FastAPI Service & Core Pipeline** | ~400–500 MB | BM25/Dense indexes, catalog, tokenizers |
| **OS Swapping & Paging Margin** | $\ge 1,000\text{ MB}$ | Headroom to prevent OOM/thrashing |
| **Minimum Pre-Launch Free RAM** | $\mathbf{\ge 2,500\text{ MB}}$ | **Required baseline before canary startup** |

---

## 3. Options Evaluation

### Option A — Existing Local / Remote Machine
- **Inspection:** Scanned all repository documentation, configuration, and environment files.
- **Finding:** No authorized second workstation, physical server, or staging host is documented or accessible.
- **Verdict:** Unavailable.

### Option B — Existing CI Environment (`.github/workflows/ci.yml`)
- **Inspection:** Analyzed `.github/workflows/ci.yml`.
- **Finding:** Targets `ubuntu-latest`. The workflow currently runs fast unit tests and a Docker build smoke test. It does not configure Ollama or Gemma 3 1B.
- **Hardware Capacity:** Standard GitHub-hosted `ubuntu-latest` provides 2 vCPUs, **7.0 GB RAM** (~6.3 GB free unallocated memory at start), and 14 GB SSD storage.
- **Verdict:** Highly capable, satisfies all hardware and software criteria.

### Option C — External Free Compute Services
Multiple free-tier compute providers were analyzed against zero-cost, zero-credit-card, Docker, Ollama, and RAM criteria:

- **Rejected (Billing Verification Barriers):**
  - *Oracle Cloud "Always Free":* Requires credit card identity verification during registration.
  - *GitLab CI/CD Shared Runners:* Mandates credit card verification for free pipeline compute.
- **Rejected (Architectural Incompatibility):**
  - *Google Colab (Free CPU):* 12.7 GB RAM available, but lacks native privileged Docker daemon required for the containerized inference adapter (`atlas-inference:5d`).

---

## 4. Top 3 Free Compute Candidates

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                RANKED CANDIDATES MATRIX                                │
├──────┬──────────────────────┬─────────────┬──────────────┬─────────────┬───────────────┤
│ Rank │ Candidate            │ Free RAM    │ Docker Daemon│ Ollama/Gemma│ Payment Card? │
├──────┼──────────────────────┼─────────────┼──────────────┼─────────────┼───────────────┤
│  1   │ GitHub Actions Linux │ ~6.3 GB     │ Native root  │ Fully supp. │ None ($0)     │
│  2   │ Google Colab CPU     │ ~11.5 GB    │ Restricted   │ Standalone  │ None ($0)     │
│  3   │ Hugging Face Spaces  │ ~15.0 GB    │ Custom Docker│ Contained   │ None ($0)     │
└──────┴──────────────────────┴─────────────┴──────────────┴─────────────┴───────────────┘
```

### Rank 1: GitHub Actions Hosted Linux Runner (`ubuntu-latest`) — **BEST CANDIDATE**
- **Provider:** GitHub (Microsoft Azure Infrastructure).
- **Free Tier Eligibility:** 2,000 free minutes/month for standard GitHub accounts on private repositories; unlimited minutes on public repositories. **No credit card required.**
- **Specifications:** 2 vCPU (x86_64), 7.0 GB RAM, 14 GB SSD.
- **Initial Free RAM:** **~6.3 GB** (exceeds the 2.5 GB requirement by 250%).
- **Container Support:** Pre-installed Docker 24+ with root privileges and active daemon.
- **Ollama & Gemma 3 1B:** Ollama installs in ~10 seconds via official script; model pulls in ~15 seconds across datacenter gigabit networking.
- **Why Best:** Matches existing repository layout (`.github/workflows/`), supports native Docker + Ollama + Python in the exact topology certified in CR-01, generates automated downloadable build/test artifacts, and provides an isolated, ephemeral single-tenant VM for each run.

### Rank 2: Google Colab (Free CPU Tier)
- **Provider:** Google.
- **Free Tier Eligibility:** Free with any standard Google account. No credit card required.
- **Specifications:** 2 vCPU, 12.7 GB RAM, ~100 GB disk.
- **Initial Free RAM:** **~11.5 GB**.
- **Limitations:** Lacks native Docker daemon (Colab runs in an unprivileged container). While Ollama and ATLAS can run as native Linux processes, testing the Dockerized container boundary (`atlas-inference:5d`) requires significant non-standard workarounds.

### Rank 3: Hugging Face Spaces (CPU Basic Free Tier)
- **Provider:** Hugging Face.
- **Free Tier Eligibility:** Free with standard Hugging Face account. No credit card required.
- **Specifications:** 2 vCPU, 16.0 GB RAM, 50 GB disk.
- **Initial Free RAM:** **~15.0 GB**.
- **Limitations:** Intended for hosted web services rather than one-shot test execution. Requires maintaining an external Space repository; space visibility must be explicitly set to Private to maintain zero-exposure security boundaries.

---

## 5. Security & Isolation Considerations

For Candidate 1 (GitHub Actions):
- **Ephemeral Sandbox:** Each GitHub Actions workflow executes in a newly provisioned, isolated Azure virtual machine that is completely destroyed after job completion.
- **No Shared State:** Zero disk or memory persistence across runs, eliminating cross-run artifact or credential contamination.
- **Secret Isolation:** Repository secrets (if any) are redacted from logs; CS-01 synthetic testing uses isolated test tokens only.
- **Production Isolation:** No network route or connection to any 0.4.14 production infrastructure.

---

## 6. Single Recommended Next Action

> **Publish the ATLAS repository to a GitHub remote and configure an automated one-shot workflow job in GitHub Actions to execute the bounded CS-01 canary on a standard `ubuntu-latest` runner.**
