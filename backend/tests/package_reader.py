"""Test helper that reads an export ZIP relative to its single archive root folder."""

from __future__ import annotations

import zipfile
from pathlib import Path


class ExportPackage(zipfile.ZipFile):
    """Open an export ZIP and expose entry names without the archive root folder.

    Raises AssertionError when the ZIP does not have exactly one root folder, which is the
    layout contract of ADR 0013. `raw_names()` returns the unprefixed ZIP entry names.
    """

    def __init__(self, path: Path) -> None:
        """Open the ZIP and derive the single root folder from its entries."""
        super().__init__(path)
        roots = {name.split("/", 1)[0] for name in super().namelist()}
        assert len(roots) == 1, f"Expected one archive root folder, found {sorted(roots)}"
        self.root = roots.pop()

    def raw_names(self) -> list[str]:
        """Return the ZIP entry names exactly as stored, including the root folder."""
        return super().namelist()

    def namelist(self) -> list[str]:
        """Return entry names relative to the archive root folder."""
        prefix = f"{self.root}/"
        return [name.removeprefix(prefix) for name in super().namelist()]

    def read(self, name: str | zipfile.ZipInfo, pwd: bytes | None = None) -> bytes:  # type: ignore[override]
        """Read an entry by its path relative to the archive root folder."""
        return super().read(f"{self.root}/{name}", pwd)


def open_package(path: Path) -> ExportPackage:
    """Open an export ZIP for reading through root-relative paths."""
    return ExportPackage(path)
