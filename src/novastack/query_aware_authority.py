"""Deterministic Source-Intent Extraction & Query-Aware Authority Preservation — Phase 4K-B.

Identifies when a user query explicitly requests observational or lower-authority
source types (e.g. triage channel notes, support tickets, engineering notes, meeting minutes)
to prevent the Authority Downgrade Paradox during Evidence Assembly (Stage 7).
"""

from __future__ import annotations

import re
from typing import Final

# Deterministic regex patterns mapping normalized source_type to explicit intent phrases
SOURCE_TYPE_INTENT_PATTERNS: Final[dict[str, list[re.Pattern[str]]]] = {
    "conversation": [
        re.compile(r"\b(?:triage\s+(?:channel\s+)?notes?|triage\s+(?:chat|channel)|slack(?:\s+channel|\s+thread|\s+notes?|\s+transcript)?|chat(?:\s+logs?|\s+notes?|\s+transcript)?)\b", re.IGNORECASE),
    ],
    "support_ticket": [
        re.compile(r"\b(?:support\s+tickets?|customer\s+(?:support\s+)?tickets?|(?:opened|filed|customer)\s+tickets?|ticket\s+reports?)\b", re.IGNORECASE),
        re.compile(r"\btickets?\s+report(?:ed)?\b", re.IGNORECASE),
    ],
    "engineering_note": [
        re.compile(r"\b(?:engineering\s+notes?|investigation\s+notes?|dev\s+notes?|developer\s+notes?)\b", re.IGNORECASE),
    ],
    "meeting": [
        re.compile(r"\b(?:meeting\s+notes?|meeting\s+minutes|sync\s+notes?|sync\s+minutes|retrospective\s+notes?|minutes\s+of\s+meeting)\b", re.IGNORECASE),
    ],
    "pull_request_note": [
        re.compile(r"\b(?:pull\s+request\s+notes?|pr\s+notes?)\b", re.IGNORECASE),
    ],
    "deployment_note": [
        re.compile(r"\b(?:deployment\s+notes?|deploy\s+notes?|deploy\s+logs?|deployment\s+logs?)\b", re.IGNORECASE),
    ],
}


def extract_requested_source_types(query: str) -> set[str]:
    """Extract normalized source types explicitly requested by the user's query.

    Args:
        query: Raw query string.

    Returns:
        A set of matching source_type strings (e.g., {'conversation', 'support_ticket'}).
        Returns an empty set if no specific source types are explicitly requested.
    """
    if not query or not query.strip():
        return set()

    requested: set[str] = set()
    for source_type, patterns in SOURCE_TYPE_INTENT_PATTERNS.items():
        for pat in patterns:
            if pat.search(query):
                requested.add(source_type)
                break

    return requested
