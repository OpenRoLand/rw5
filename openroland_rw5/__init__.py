"""RW5 (Trimble/Carlson/SurvCE) survey file parsing and extraction.

This package provides three layers of API surface:

- :mod:`openroland_rw5.parser` and :mod:`openroland_rw5.models`: the raw,
  format-specific parser and record models, useful for CAD-oriented or
  other non-canonical consumers.
- :mod:`openroland_rw5.extractor`: :class:`~openroland_rw5.extractor.
  Rw5Extractor`, the adapter that maps raw records onto
  ``openroland-survey-core``'s canonical survey point model.
- This module's :func:`extract_points`, :func:`export_to_xlsx`, and
  :func:`export_to_database`: direct convenience wrappers that delegate
  database and workbook mechanics to ``openroland-survey-core``, so this package
  is independently usable without duplicating that logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from openroland_survey import database as survey_database
from openroland_survey import extractors as survey_extractors
from openroland_survey.records import ExtractionResult, ImportSummary, XlsxSummary
from sqlalchemy import Engine

from openroland_rw5.extractor import Rw5Extractor

__all__ = [
    "Rw5Extractor",
    "export_to_database",
    "export_to_xlsx",
    "extract_points",
]


def extract_points(source_path: Union[str, Path]) -> ExtractionResult:
    """Extract canonical survey points from one RW5 file.

    Args:
        source_path: Path of the RW5 file to read.

    Returns:
        The extracted records, source metadata, and diagnostics.
    """
    return survey_extractors.extract_file(source_path, Rw5Extractor())


def export_to_xlsx(
    source_path: Union[str, Path],
    output_path: Optional[Union[str, Path]] = None,
    *,
    overwrite: bool = False,
) -> XlsxSummary:
    """Extract one RW5 file and write it to a single XLSX workbook.

    Args:
        source_path: Path of the RW5 file to read.
        output_path: Destination ``.xlsx`` path. Defaults to the
            source's directory and stem with an ``.xlsx`` extension.
        overwrite: Whether to replace an existing file at ``output_path``.

    Returns:
        A summary of the written workbook.
    """
    return survey_extractors.export_file_to_xlsx(
        source_path, Rw5Extractor(), output_path, overwrite=overwrite
    )


def export_to_database(
    source_path: Union[str, Path],
    database: Union[str, Path, Engine],
    *,
    source_crs: Optional[str] = None,
) -> ImportSummary:
    """Extract one RW5 file and import it into the canonical database.

    Args:
        source_path: Path of the RW5 file to read.
        database: Either an existing SQLAlchemy engine, or a SQLite file
            path/SQLAlchemy URL used to create one.
        source_crs: Coordinate reference system to use for records that
            lack their own latitude/longitude.

    Returns:
        A summary of what was imported, linked, or skipped.
    """
    engine = (
        database
        if isinstance(database, Engine)
        else survey_database.create_engine(database)
    )
    return survey_extractors.import_file_to_database(
        source_path, Rw5Extractor(), engine, source_crs=source_crs
    )
