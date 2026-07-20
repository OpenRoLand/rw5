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

### Fixed
