# Phase 4T — Cryptographic Caller Identity Boundary

`POST /query` is fail-closed.  A request is processed only when ATLAS is
configured with a trusted HS256 JWT issuer and the bearer token verifies.

Set these environment variables before starting the service:

- `ATLAS_AUTH_ISSUER` — exact expected issuer.
- `ATLAS_AUTH_AUDIENCE` — exact expected audience.
- `ATLAS_AUTH_HS256_SECRET` — shared verification secret of at least 32 UTF-8
  bytes.  Supply it through the deployment secret mechanism; do not place it
  in source code, images, logs, or command-line arguments.
- `ATLAS_AUTH_CLOCK_SKEW_SECONDS` — optional integer from `0` to `300`;
  defaults to `30`.

The verifier accepts only compact JWTs whose header declares `alg` as `HS256`
and `typ` as `JWT`.
It validates the signature with constant-time comparison, `iss`, `aud`,
`exp`, and `nbf` / `iat` when present.  It requires `sub` and `tenant_id`.
Roles and departments are optional typed claims.  The required legacy body
`user_context` is only a compatibility cross-check: a disagreement with
verified tenant, user, role, or department claims is rejected with `403`, and
the pipeline receives the claims derived from the token.

Without valid configuration, `/ready` reports
`components.authentication = false` and returns `503`; `/query` also returns
`503`.  Missing, malformed, expired, wrongly signed, wrong-issuer, or
wrong-audience tokens return `401` with `WWW-Authenticate: Bearer`.

The service records only bounded authentication outcomes
(`configuration`, `authentication`, or `context_mismatch`).  It does not log
bearer tokens, raw claims, credentials, queries, documents, prompts, or
answers.

This phase deliberately does not add OIDC discovery, asymmetric JWKS key
rotation, mTLS, identity provisioning, or an authorization-policy engine.
Those remain integration work for the deployment identity provider.
