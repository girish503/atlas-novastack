"""Phase 4T: fail-closed cryptographic caller identity for the HTTP boundary.

This module intentionally implements a small, strict HS256 JWT verifier with
only standard-library dependencies.  It does not issue tokens, discover OIDC
metadata, or accept unverified client-supplied caller claims.  Production
deployments must provide the trusted issuer, audience, and sufficiently strong
shared verification secret through the environment.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import math
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

from novastack.service.schemas import CallerContext


_MINIMUM_SECRET_BYTES = 32
_MAX_TOKEN_CHARS = 8192


class IdentityError(Exception):
    """Base class for a non-sensitive caller identity rejection."""


class IdentityConfigurationError(IdentityError):
    """The service has no safe token-verification configuration."""


class IdentityAuthenticationError(IdentityError):
    """Bearer credential failed structural or cryptographic verification."""


class IdentityContextMismatchError(IdentityError):
    """Client-supplied routing context conflicts with verified token claims."""


def _clean_env(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


@dataclass(frozen=True)
class IdentityConfig:
    """Trusted, validated verifier configuration sourced from the environment."""

    issuer: Optional[str]
    audience: Optional[str]
    hs256_secret: Optional[bytes]
    clock_skew_seconds: int = 30
    configuration_error: Optional[str] = None

    @classmethod
    def from_env(cls) -> "IdentityConfig":
        issuer = _clean_env("ATLAS_AUTH_ISSUER")
        audience = _clean_env("ATLAS_AUTH_AUDIENCE")
        secret = _clean_env("ATLAS_AUTH_HS256_SECRET")
        skew_raw = _clean_env("ATLAS_AUTH_CLOCK_SKEW_SECONDS") or "30"

        try:
            clock_skew_seconds = int(skew_raw)
        except ValueError:
            return cls(issuer, audience, None, configuration_error="invalid_clock_skew")

        if clock_skew_seconds < 0 or clock_skew_seconds > 300:
            return cls(issuer, audience, None, configuration_error="invalid_clock_skew")
        if not issuer or not audience or not secret:
            return cls(issuer, audience, None, clock_skew_seconds, "missing_required_configuration")

        secret_bytes = secret.encode("utf-8")
        if len(secret_bytes) < _MINIMUM_SECRET_BYTES:
            return cls(issuer, audience, None, clock_skew_seconds, "verification_secret_too_short")
        return cls(issuer, audience, secret_bytes, clock_skew_seconds)

    @property
    def is_configured(self) -> bool:
        return (
            self.configuration_error is None
            and bool(self.issuer)
            and bool(self.audience)
            and self.hs256_secret is not None
        )


@dataclass(frozen=True)
class VerifiedIdentity:
    """Trusted identity derived exclusively from validated JWT claims."""

    subject: str
    tenant_id: str
    roles: tuple[str, ...]
    departments: tuple[str, ...]
    primary_role: Optional[str] = None
    primary_department: Optional[str] = None

    def to_caller_context(self) -> CallerContext:
        return CallerContext(
            tenant_id=self.tenant_id,
            user_id=self.subject,
            user_role=self.primary_role,
            roles=list(self.roles),
            user_department=self.primary_department,
            departments=list(self.departments),
        )


def _decode_json_segment(segment: str, label: str) -> dict[str, Any]:
    if not segment or len(segment) > _MAX_TOKEN_CHARS:
        raise IdentityAuthenticationError("invalid_token")
    try:
        raw = base64.b64decode(
            (segment + "=" * (-len(segment) % 4)).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, UnicodeEncodeError, binascii.Error) as exc:
        raise IdentityAuthenticationError("invalid_token") from exc

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise IdentityAuthenticationError("invalid_token")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, IdentityAuthenticationError) as exc:
        raise IdentityAuthenticationError("invalid_token") from exc
    if not isinstance(value, dict):
        raise IdentityAuthenticationError("invalid_token")
    return value


def _required_text(payload: dict[str, Any], claim: str) -> str:
    value = payload.get(claim)
    if not isinstance(value, str) or not value.strip():
        raise IdentityAuthenticationError("invalid_token_claims")
    return value.strip()


def _optional_text(payload: dict[str, Any], claim: str) -> Optional[str]:
    value = payload.get(claim)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise IdentityAuthenticationError("invalid_token_claims")
    return value.strip()


def _optional_text_list(payload: dict[str, Any], claim: str) -> tuple[str, ...]:
    value = payload.get(claim)
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise IdentityAuthenticationError("invalid_token_claims")
    return tuple(item.strip() for item in value)


def _numeric_date(payload: dict[str, Any], claim: str, required: bool) -> Optional[float]:
    value = payload.get(claim)
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise IdentityAuthenticationError("invalid_token_claims")
    return float(value)


class JwtIdentityVerifier:
    """Strict verifier for a preconfigured trusted HS256 JWT issuer."""

    def __init__(self, config: IdentityConfig, clock: Any = time.time) -> None:
        self.config = config
        self._clock = clock

    def verify_authorization_header(self, authorization: Optional[str]) -> VerifiedIdentity:
        if not self.config.is_configured:
            raise IdentityConfigurationError("identity_verification_not_configured")
        if not authorization or len(authorization) > _MAX_TOKEN_CHARS + 16:
            raise IdentityAuthenticationError("missing_bearer_token")

        scheme, separator, compact_token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator or not compact_token or " " in compact_token:
            raise IdentityAuthenticationError("invalid_bearer_token")
        return self.verify_compact_token(compact_token)

    def verify_compact_token(self, compact_token: str) -> VerifiedIdentity:
        if not self.config.is_configured or self.config.hs256_secret is None:
            raise IdentityConfigurationError("identity_verification_not_configured")
        if not compact_token or len(compact_token) > _MAX_TOKEN_CHARS:
            raise IdentityAuthenticationError("invalid_token")

        parts = compact_token.split(".")
        if len(parts) != 3 or any(not part for part in parts):
            raise IdentityAuthenticationError("invalid_token")
        encoded_header, encoded_payload, encoded_signature = parts
        header = _decode_json_segment(encoded_header, "header")
        payload = _decode_json_segment(encoded_payload, "payload")

        if header.get("alg") != "HS256" or header.get("typ") != "JWT":
            raise IdentityAuthenticationError("invalid_token_algorithm")
        try:
            supplied_signature = base64.b64decode(
                (encoded_signature + "=" * (-len(encoded_signature) % 4)).encode("ascii"),
                altchars=b"-_",
                validate=True,
            )
        except (ValueError, UnicodeEncodeError, binascii.Error) as exc:
            raise IdentityAuthenticationError("invalid_token") from exc
        signed_content = f"{encoded_header}.{encoded_payload}".encode("ascii")
        expected_signature = hmac.digest(self.config.hs256_secret, signed_content, "sha256")
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise IdentityAuthenticationError("invalid_token_signature")

        issuer = _required_text(payload, "iss")
        if issuer != self.config.issuer:
            raise IdentityAuthenticationError("invalid_token_issuer")
        audience = payload.get("aud")
        if isinstance(audience, str):
            audience_matches = audience == self.config.audience
        elif isinstance(audience, list) and all(isinstance(item, str) for item in audience):
            audience_matches = self.config.audience in audience
        else:
            audience_matches = False
        if not audience_matches:
            raise IdentityAuthenticationError("invalid_token_audience")

        now = float(self._clock())
        skew = float(self.config.clock_skew_seconds)
        expiration = _numeric_date(payload, "exp", required=True)
        not_before = _numeric_date(payload, "nbf", required=False)
        issued_at = _numeric_date(payload, "iat", required=False)
        if expiration is None or now >= expiration + skew:
            raise IdentityAuthenticationError("expired_token")
        if not_before is not None and now + skew < not_before:
            raise IdentityAuthenticationError("not_yet_valid_token")
        if issued_at is not None and issued_at > now + skew:
            raise IdentityAuthenticationError("invalid_token_claims")

        return VerifiedIdentity(
            subject=_required_text(payload, "sub"),
            tenant_id=_required_text(payload, "tenant_id"),
            roles=_optional_text_list(payload, "roles"),
            departments=_optional_text_list(payload, "departments"),
            primary_role=_optional_text(payload, "role"),
            primary_department=_optional_text(payload, "department"),
        )


def assert_context_matches_identity(
    requested_context: CallerContext,
    identity: VerifiedIdentity,
) -> None:
    """Reject client routing claims that conflict with verified identity claims."""
    if requested_context.tenant_id != identity.tenant_id:
        raise IdentityContextMismatchError("tenant_mismatch")
    if requested_context.user_id is not None and requested_context.user_id != identity.subject:
        raise IdentityContextMismatchError("subject_mismatch")
    if requested_context.user_role is not None and requested_context.user_role != identity.primary_role:
        raise IdentityContextMismatchError("role_mismatch")
    if requested_context.roles and tuple(requested_context.roles) != identity.roles:
        raise IdentityContextMismatchError("roles_mismatch")
    if requested_context.user_department is not None and requested_context.user_department != identity.primary_department:
        raise IdentityContextMismatchError("department_mismatch")
    if requested_context.departments and tuple(requested_context.departments) != identity.departments:
        raise IdentityContextMismatchError("departments_mismatch")
