"""Errors that deliberately stop work when intent classification is unsafe."""

from __future__ import annotations


class ClassificationError(RuntimeError):
    """A retryable failure to produce a trustworthy intent classification."""
