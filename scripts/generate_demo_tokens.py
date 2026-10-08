#!/usr/bin/env python3
"""Generates isolated, pre-signed HS256 demo tokens for the 4 ATLAS demo personas.

Tokens have exp=2030 (January 2030) so they can be safely used for demonstrations
without requiring client-side signing secrets.
"""
import base64
import hashlib
import hmac
import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE / "src"))

from novastack.service.identity import IdentityConfig, JwtIdentityVerifier

ISSUER = "https://identity.atlas.example/issuer"
AUDIENCE = "atlas-query-api"
SECRET = "atlas-demo-secret-key-32-bytes-long-2026"

PERSONAS = [
    {
        "id": "USR-NS-0008",
        "name": "Zara Reyes",
        "role": "engineer",
        "department": "Engineering",
        "tenant_id": "TENANT-NOVASTACK",
    },
    {
        "id": "USR-ENG-42",
        "name": "Alex Chen",
        "role": "engineer",
        "department": "Engineering",
        "tenant_id": "TENANT-NOVASTACK",
    },
    {
        "id": "USR-ACME-01",
        "name": "Alice Vance",
        "role": "auditor",
        "department": "Finance",
        "tenant_id": "TENANT-ACME-EXTERNAL",
    },
    {
        "id": "USR-INTERN-01",
        "name": "Sam Taylor",
        "role": "intern",
        "department": "Engineering",
        "tenant_id": "TENANT-NOVASTACK",
    },
]


def make_token(p: dict[str, str]) -> str:
    now = 1700000000
    exp = 1893456000  # January 2030
    h = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": p["id"],
        "tenant_id": p["tenant_id"],
        "roles": [p["role"]],
        "departments": [p["department"]],
        "role": p["role"],
        "department": p["department"],
        "iat": now,
        "exp": exp,
    }
    part1 = base64.urlsafe_b64encode(json.dumps(h, separators=(",", ":")).encode("utf-8")).rstrip(b"=").decode("ascii")
    part2 = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).rstrip(b"=").decode("ascii")
    signed = f"{part1}.{part2}"
    sig = hmac.new(SECRET.encode("utf-8"), signed.encode("ascii"), hashlib.sha256).digest()
    part3 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")
    return f"{signed}.{part3}"


def main():
    verifier = JwtIdentityVerifier(IdentityConfig(ISSUER, AUDIENCE, SECRET.encode("utf-8")))
    tokens = {}
    for p in PERSONAS:
        tok = make_token(p)
        ident = verifier.verify_compact_token(tok)
        tokens[p["id"]] = tok
        print(f"Verified {p['id']} ({p['name']}): subject={ident.subject}, role={ident.primary_role}")
        print(f"  Token: {tok}\n")

    output_path = WORKSPACE / "ui" / "demo_tokens.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=2)
    print(f"Saved demo tokens to {output_path}")


if __name__ == "__main__":
    main()
