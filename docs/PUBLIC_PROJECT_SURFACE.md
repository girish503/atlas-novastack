# ATLAS — Public Project Surface & Security Disclosure Boundary

**Document Purpose**: Establishes the authoritative boundary between public technical review assets and private operational information.
**Audience**: Open-Source Maintainers, Security Reviewers, GitHub Visitors
**Project**: ATLAS — Evidence-Grounded Enterprise Search Platform
**Enterprise**: NovaStack

---

## 1. Public Disclosure Surface (Open for Public Review)

The following components and documentation are intentionally public, open-source, and fully verifiable in this repository:

1. **System Architecture & Design**: Complete ASCII diagrams, request lifecycles, and component interactions documented in [`README.md`](../README.md) and [`docs/REPOSITORY_GUIDE.md`](REPOSITORY_GUIDE.md).
2. **Core Source Code**: All implementation code located under [`src/novastack/`](../src/novastack/), including retrieval algorithms, reranking, evidence boundaries, generation abstractions, and citation validation.
3. **Automated Test Suite**: All 94 unit/integration tests and 13 red-team security tests located in [`tests/`](../tests/).
4. **Evaluation Methodology & Canonical Manifests**: The complete 120-case evaluation taxonomy, difficulty classifications, and benchmark manifests located in [`data/evaluation/novastack/`](../data/evaluation/novastack/) and [`artifacts/`](../artifacts/).
5. **Architectural Decisions**: Rationale, empirical trade-offs, and evaluated alternatives documented in [`docs/ENGINEERING_DECISIONS.md`](ENGINEERING_DECISIONS.md).
6. **Demonstration Interface**: The single-page application at [`ui/index.html`](../ui/index.html) and local static server at [`scripts/serve_ui.py`](../scripts/serve_ui.py).
7. **Known Limitations & Production Roadmap**: Honest, unvarnished disclosure of CPU latencies, cold-start model weight loading, unverified operational probes, and the future GPU production path.

---

## 2. Non-Disclosed & Protected Operational Boundaries (DO NOT EXPOSE)

To preserve corporate security and adhere to enterprise compliance standards, the following categories are strictly excluded from the public repository:

1. **Production Cryptographic Secrets**: Real HS256 / RS256 private keys, corporate KMS keys, and production API signing secrets must never be committed to Git.
2. **Real Enterprise Data & PII**: All organizations, users, incidents, and postmortems in ATLAS are **100% synthetic generated datasets** modeling NovaStack operations. Zero real customer data, employee PII, or internal credentials exist in the codebase.
3. **Host-Specific Machine Paths**: Configurations must use relative workspace paths (`ATLAS_WORKSPACE_ROOT=.`) rather than hardcoded machine directories.
4. **Direct Cloud Infrastructure Endpoints**: Internal VPC addresses, cloud database connection strings, and production load balancer IPs are omitted.
5. **Ollama Ingress Exposure**: Port `11434` is bound strictly to `127.0.0.1` and is never exposed to external or public networks.

---

## 3. Classification of Demonstration Credentials

| Asset | Classification | Purpose | Security Guardrail |
| :--- | :--- | :--- | :--- |
| [`ui/demo_tokens.json`](../ui/demo_tokens.json) | **SYNTHETIC DEMO CREDENTIAL** | Pre-signed JWTs enabling local demonstration of test personas | Expire Jan 2030; scoped only to `atlas-query-api`; zero access to real systems |
| [`.env.example`](../.env.example) | **SYNTHETIC CONFIGURATION TEMPLATE** | Placeholder environment variables for local testing | Contains no real secrets; masked strings |
| `tests/security/` | **SYNTHETIC ATTACK FIXTURES** | Adversarial payloads testing system bounds | Contained within automated pytest suite |

> [!WARNING]
> **Strict Demonstration Boundary**: Pre-signed demonstration tokens in `ui/demo_tokens.json` are **SYNTHETIC DEMO CREDENTIALS ONLY**. They are mathematically valid for the local test secret `atlas-demo-secret-key-32-bytes-long-2026`, but possess **zero authority** on any production system, cloud service, or corporate network.
