# ATLAS RLS-00 — Release Identity Reconciliation

**Date:** 2026-10-06  
**Decision:** `RELEASE_IDENTITY_REVIEW_REQUIRED`

## Scope and controls

RLS-00 independently hashed the two local release archives and read the release records. It did not rebuild, overwrite, move, or otherwise modify a release archive. It did not modify production source, retrieval, inference, frontend code, or workflows, and it did not trigger the manual canary.

## Independent artifact verification

| Release | Archive | Size (bytes) | Computed SHA-256 | Result |
|---|---|---:|---|---|
| 0.4.14-rc1 | `dist/atlas-novastack-0.4.14-rc1.tar.gz` | 3,475,452 | `382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3` | Matches all existing repository release records. Does not match the new directive value. |
| 0.5.0-rc1 | `dist/atlas-novastack-0.5.0-rc1.tar.gz` | 3,476,740 | `f9fe791595282bdd6e961e1383de69ee1a63cc52cb16ccdfed9645677b914936` | Matches the new directive and repository release records. |

## Directive-value assessment

The RLS-00 directive supplied this purported authoritative 0.4.14 value:

```text
382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539239208a9d4
```

It is **65 characters**, not a valid SHA-256 digest, and has zero repository occurrences. The same directive labels this valid 64-character value as erroneous:

```text
382cde6c9385acb8f06b80c07895af498b98347a16f1f124bf16539208a9d4a3
```

That labeled value is the independently computed 0.4.14 archive digest. It appears in 104 lines across 69 files: 31 artifacts, 30 documentation files, one workflow, three tests, three scripts, and one root release record. Every such occurrence is therefore classified as a consistent repository baseline reference, not as a stale or contradictory record.

The valid 0.5.0 digest appears in 49 lines across 42 files—18 artifacts, 23 documents, and the canary workflow—and is consistent with the independently computed archive digest.

## Manifest comparison

| Record | 0.4.14 identity | 0.5.0 identity | Assessment |
|---|---|---|---|
| `artifacts/phase_5k_release_manifest.json` | Identifies `0.4.14-rc1`; does not store an archive SHA | N/A | No conflicting checksum. |
| `artifacts/phase_5m_sha256_manifest.json` | Records `382c…08a9d4a3` for the matching filename | N/A | Matches local archive bytes. |
| `artifacts/phase_5m_release_artifact_manifest.json` | Same filename, hash, and 3,475,452-byte size | N/A | Matches local archive bytes. |
| `artifacts/phase_gh08_publication.json` | Records `382c…08a9d4a3` | Records `f9fe…914936` | Both match local archives. |
| `artifacts/phase_cs01_gha_fix01_publication.json` | Records `382c…08a9d4a3` | Records `f9fe…914936` | Both match local archives. |
| `.github/workflows/canary.yml` | Expected value `382c…08a9d4a3` | Expected value `f9fe…914936` | Manual-only workflow; not run. |

## Repository state

- HEAD: `d325e5a82681456ebaca57f2f27c1900f17bd415`
- HEAD subject: `fix: remediate canary harness fixture contracts`
- Remote: `https://github.com/girish503/atlas-novastack.git`
- No tracked changes under `src`, `.github`, or `dist` were present or introduced by RLS-00.
- Release archives remain byte-identical to the hashes recorded above.
- The only RLS-00 writes are this report and `artifacts/phase_rls00_release_identity.json`.

## Conclusion

The repository's archive bytes and existing release records are internally consistent. However, they cannot satisfy the RLS-00 directive as written because its stated 0.4.14 authoritative value is not a valid SHA-256 digest and does not match the actual artifact.

Release governance must provide a valid 64-character authoritative 0.4.14 checksum, or explicitly affirm the currently computed and repository-recorded value, before the status can be changed to `RELEASE_IDENTITY_RECONCILED`. No certified artifact or existing release record was changed.
