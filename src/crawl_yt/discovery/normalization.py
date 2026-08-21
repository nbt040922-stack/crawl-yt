"""Canonical normalization for persisted discovery keywords."""

from __future__ import annotations


def normalize_discovery_keyword(value: str) -> str:
    """Trim, collapse whitespace, and casefold without changing punctuation."""
    return " ".join(value.split()).casefold()
