"""Project glossary: normalizes the terms a user saves and turns them into a Whisper hint."""

from __future__ import annotations

from collections.abc import Iterable

# Limits shared with the API schemas. Whisper reads at most 223 prompt tokens, so a long list is
# stored in full (it stays the user's data) but only a prefix reaches the model.
MAX_GLOSSARY_TERMS = 200
MAX_GLOSSARY_TERM_LENGTH = 80
MAX_HINT_CHARACTERS = 500


def normalize_glossary(terms: Iterable[str]) -> list[str]:
    """Return the terms trimmed, single-spaced, without blanks and without case-insensitive repeats.

    Order is kept because the first terms are the ones that survive the hint limit. Never fails;
    callers check `MAX_GLOSSARY_TERMS` and `MAX_GLOSSARY_TERM_LENGTH` on the result.
    """
    seen: set[str] = set()
    normalized: list[str] = []
    for term in terms:
        cleaned = " ".join(term.split())
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            normalized.append(cleaned)
    return normalized


def build_transcription_hint(
    terms: Iterable[str], *, max_characters: int = MAX_HINT_CHARACTERS
) -> str | None:
    """Join whole terms with commas into the text passed to Whisper as hotwords.

    Returns None for an empty glossary so the model receives no hint at all. Terms are added in
    order and the first one that would pass `max_characters` ends the list, so the hint is always
    a prefix of the glossary and no term is cut in half. The text has no wording of its own, so
    the same hint suits English, Italian or any other spoken language.
    """
    parts: list[str] = []
    length = 0
    for term in normalize_glossary(terms):
        added = len(term) + (2 if parts else 0)
        if length + added > max_characters:
            break
        parts.append(term)
        length += added
    return ", ".join(parts) or None
