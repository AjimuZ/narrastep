"""Pure builders for export names: project slug, ZIP or folder name and screenshot file names."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

SLUG_MAX_LENGTH = 48
SLUG_FALLBACK = "project"

# Letters NFKD does not decompose into an ASCII base letter; without this map they would vanish.
_TRANSLITERATION = str.maketrans(
    {
        "ß": "ss",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "ø": "o",
        "Ø": "O",
        "đ": "d",
        "Đ": "D",
        "ð": "d",
        "Ð": "D",
        "þ": "th",
        "Þ": "TH",
        "ł": "l",
        "Ł": "L",
        "ı": "i",
    }
)
_NON_SLUG = re.compile(r"[^a-z0-9]+")
_WINDOWS_RESERVED = re.compile(r"(con|prn|aux|nul|com[1-9]|lpt[1-9])")
_TYPE_PATTERN = re.compile(r"[a-z][a-z0-9_]*")


def slugify_project_name(name: str | None) -> str:
    """Return an ASCII, lowercase, hyphen-separated slug of at most 48 characters.

    Accents are transliterated; emoji, symbols and path syntax are dropped. An empty result falls
    back to `project`, and a Windows reserved device name (`con`, `nul`, `com1`...) gets a
    `-project` suffix so the slug is safe as a file or folder name on every platform. Never raises.
    """
    text = unicodedata.normalize("NFKD", (name or "").translate(_TRANSLITERATION))
    ascii_text = text.encode("ascii", "ignore").decode("ascii").lower()
    slug = _NON_SLUG.sub("-", ascii_text).strip("-")[:SLUG_MAX_LENGTH].strip("-")
    if not slug:
        return SLUG_FALLBACK
    if _WINDOWS_RESERVED.fullmatch(slug):
        return f"{slug}-{SLUG_FALLBACK}"
    return slug


def export_archive_name(
    created_at: date | datetime, project_type: str, project_name: str | None, sequence: int
) -> str:
    """Return the ZIP base name `{date}_{type}_{slug}_export-{nn}` (no extension).

    `created_at` supplies the UTC calendar date (callers pass an aware UTC datetime or a date).
    `sequence` is the per-project export number (>= 1, zero-padded to two digits). `project_type`
    must be a lowercase identifier from ADR 0009. Raises ValueError for an invalid type or sequence.
    """
    if not _TYPE_PATTERN.fullmatch(project_type):
        raise ValueError("project_type must be a lowercase identifier")
    if sequence < 1:
        raise ValueError("sequence must be at least 1")
    day = created_at.date() if isinstance(created_at, datetime) else created_at
    slug = slugify_project_name(project_name)
    return f"{day.isoformat()}_{project_type}_{slug}_export-{sequence:02d}"


def export_zip_file_name(
    created_at: date | datetime, project_type: str, project_name: str | None, sequence: int
) -> str:
    """Return the download name of the archive: `export_archive_name(...)` plus `.zip`."""
    return f"{export_archive_name(created_at, project_type, project_name, sequence)}.zip"


def screenshot_file_name(step_number: int, start_seconds: float) -> str:
    """Return `step-{nnn}_{mm}m{ss}s.png` for a step and its video time.

    Seconds are truncated to whole seconds; minutes are not wrapped into hours, so 1h05m reads as
    `65m00s`. Raises ValueError for a step below 1 or a negative time.
    """
    if step_number < 1:
        raise ValueError("step_number must be at least 1")
    if start_seconds < 0:
        raise ValueError("start_seconds must not be negative")
    total = int(start_seconds)
    return f"step-{step_number:03d}_{total // 60:02d}m{total % 60:02d}s.png"
