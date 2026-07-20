# Changelog

## [Unreleased]

### Added

- Initial `siscadro-rw5` package: a low-level RW5 (Trimble/Carlson/SurvCE)
  parser (`siscadro_rw5.parser`, `siscadro_rw5.models`) migrated and
  refactored from `cad_server.support.rw5lib`, preserving English/Romanian
  label recognition, base/GPS observation grouping, and every previously
  supported record type (job/mode setup, base point, GPS/G0/G1/G2/G3/GS/GT,
  offset shots, antenna configuration, quality summaries, and stakeout
  data), while removing the `Point3D`/DXF/`cad_server` dependencies and
  hard-coded paths of the original implementation and fixing a locale bug
  that previously skipped line processing whenever an explicit locale was
  supplied.
- `Rw5Extractor`, the `siscadro_survey.extractors`-registered adapter that
  maps raw RW5 observations onto the canonical `SurveyPointRecord` model,
  plus the package-level `extract_points()`, `export_to_xlsx()`, and
  `export_to_database()` convenience wrappers.
- Synthetic-fixture test suite covering locale detection/fallback, encoding
  fallback, base-station grouping and splitting, offset shots, baseline
  records, stakeout data, quality/averaged statistics, canonical field
  mapping, and XLSX/database export.

### Changed

- Replaced length-based slicing (`line[len(prefix):]`) with `str.removeprefix()`
  when stripping note-line prefixes, avoiding a Black/Flake8 `E203` conflict
  without disabling the check.
- Introduced a dedicated `_to_int()` helper for purely numeric fields
  (`sat_count`, `nr_of_sat_avg/min/max`, `valid_readings`, `fixed_readings`,
  `float_readings`, `dgps_readings`) instead of the alphanumeric-tolerant
  `_to_int_or_str()`, matching their `Optional[int]` model typing and
  satisfying `mypy`.

### Fixed

- Fixed a `[tools.black]` typo in `pyproject.toml` (should be `[tool.black]`)
  that silently made `make lint`/`make delint` fall back to Black's default
  88-column line length instead of the repository's 80-column standard.
