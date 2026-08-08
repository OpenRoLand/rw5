# siscadro-rw5

RW5 (Trimble/Carlson/SurvCE) survey file parser and canonical extractor.

This is private, unpublished code. Do not upload it to a public package
index.

## Status

This package provides three layers of API surface:

- `siscadro_rw5.parser`/`siscadro_rw5.models`: a low-level, format-specific
  parser that turns RW5 text into raw `Rw5BasePoint`/`Rw5GpsPoint` records,
  independent of any canonical model, database, or CAD library.
- `siscadro_rw5.extractor.Rw5Extractor`: the adapter that maps those raw
  records onto `siscadro-survey`'s canonical `SurveyPointRecord` model, and
  the package registered under the `siscadro_survey.extractors` entry-point
  group.
- This package's `extract_points()`, `export_to_xlsx()`, and
  `export_to_database()`: direct convenience wrappers that delegate database
  and workbook mechanics to `siscadro-survey`, so this package is usable
  on its own without duplicating that logic.

## RW5 parsing

RW5 files are line-oriented and comma-separated. Every line starts with a
two-or-three-letter record code (`GPS`, `G0`, `GS`, `G1`/`G2`/`G3`, `GT`, ...)
followed by fields tagged with a two-character sign (for example
`LA46.35192` is the tag `LA` with SurvCE packed `DD.MMSSsssss` value
`46.35192`, decoded to decimal degrees). Free-text metadata (equipment
name, antenna type, and similar) is instead written as `--`-prefixed note
lines whose label text depends on the file's locale, which is why the
parser's label-driven dispatch supports both English and Romanian SurvCE
installs.
The locale is auto-detected from the first lines of the file, or can be
supplied explicitly to `Rw5Parser(locale="en" | "ro")`.

Observations are grouped by base station: consecutive `GPS`/`G0`/`GS`/`GT`
records sharing the same base-ID (read from the `G0` record's base-ID text)
are grouped under one `Rw5BasePoint`; a base-ID change starts a new group,
even mid-file.

## Canonical extraction

`Rw5Extractor.extract()` parses one RW5 file and maps each observation with
usable projected coordinates onto a `SurveyPointRecord`. A record missing
its north/east/height coordinates is skipped and reported as a `ParseIssue`
instead of raising. RW5-specific values that have no canonical column
(averaged/range quality statistics, baseline deltas, offset-shot data,
stakeout data, and similar) are preserved in `source_values`.

## Installation

```bash
python -m pip install -e "D:\prog\__py_libs__\siscadro-survey[dev]"
python -m pip install -e .
```

## Usage

```python
from siscadro_rw5 import extract_points, export_to_xlsx, export_to_database

result = extract_points("job.rw5")
export_to_xlsx("job.rw5")
export_to_database("job.rw5", "survey.sqlite")
```

## Development

Start by creating a virtual environment and installing the development
dependencies:

```bash
python -m venv venv
venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e "D:\prog\__py_libs__\siscadro-survey[dev]"
python -m pip install -e .[dev]
```

Or, if you have `make` available:

```bash
make init-d
```

Run the standard checks before committing:

```bash
make delint
make lint
make typecheck
make test
```
