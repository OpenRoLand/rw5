# Changelog

## [Unreleased]

### Added

- ``make init``/``init-d`` now create and use a local ``venv`` (Python 3.14)
  automatically instead of installing into whatever interpreter happens to
  be active; delete ``venv/`` to force a rebuild.
- Public GitHub Actions CI workflow (``.github/workflows/ci.yml``) running
  lint, typecheck, and test.
- Public release workflow (``.github/workflows/python-publish.yml``)
  publishing to PyPI on a published GitHub release (needs the
  ``PYPI_API_TOKEN`` repository secret).
- Parse SurvCE operator ``{PN}-{code}…`` free-text notes (for example
  ``603-ST DRUM DREAPTA``): attach to matching GPS/SP ``comment``/``code``
  when empty; keep unmatched notes on ``Rw5Parser.orphan_point_notes``
  (parse-only; never bind to the previous unrelated shot).
- Acknowledge SurvCE COGO ``Calculate area of polyline …: Area = …`` notes
  (store last note text parse-only; no DEBUG).
- Parse TDS/X-change job notes ``Date (creation)``,
  ``Date (last modification)``, ``Instrument Model``, and
  ``Antenna height`` (applied to following ``SP``/``GPS`` points);
  recognize ``TDS RW5`` / ``GPS Survey`` banners and embedded
  ``GPS Reference station,SP,…`` base points (parse-only).
- Parse SurvCE averaged-SP notes ``Averaged Points`` / ``StdDev N|E|Z`` and
  point-projection ``Station`` (parse-only); silence empty custom labelled
  notes such as ``ABC:``.
- `Rw5Parser` job-header fields ``ts_angles``, ``reference_system``, and
  ``localization_type`` from SurvCE ``--`` note lines (parse-only; not
  exported to canonical source metadata).
- `Rw5GpsPoint` South/Cube observation note fields (``gnss_statistics_rt``,
  ``gnss_statistics_pp``, ``pp_time``, ``antenna_note``,
  ``instrument_selected``, ``gnss_profile_tolerance_rt``,
  ``gnss_profile_tolerance_pp``, ``initialization_time``); parse-only, not
  exported by the extractor.
- ``Rw5GpsPoint`` occupation stats ``age_*``, ``nrms_*``, and ``erms_*`` from
  SurvCE quality notes (parse-only; ``HSDV Avg`` / ``VSDV Avg`` reuse
  ``hrms_*`` / ``vrms_*``).
- ``Rw5GpsPoint.attribute_note`` from SurvCE GIS ``Attribute:`` notes
  (parse-only).
- SurvX job-header parse: ``SurvX`` banner → ``survce_version``,
  ``Gnss Device`` → ``gnss_device``, ``CS`` → ``cs_zone``, ``ES`` →
  ``ellipsoid`` (parse-only).
- `Rw5BasePoint` now carries projected `north`/`east`/`height`, `local_time`,
  `configured_by_gps_position`, and `entered_base_hr` from SurvCE base-setup
  blocks (`BP` + `--GS … --Base`, `Base Configuration by Reading GPS
  Position`, `Entered Base HR`, `DT`/`TM`).
- `Rw5Extractor` emits canonical survey points with `kind=base` for bases
  that have projected NEH.
- Initial `openroland-rw5` package: a low-level RW5 (Trimble/Carlson/SurvCE)
  parser (`openroland_rw5.parser`, `openroland_rw5.models`) migrated and
  refactored from `cad_server.support.rw5lib`, preserving English/Romanian
  label recognition, base/GPS observation grouping, and every previously
  supported record type (job/mode setup, base point, GPS/G0/G1/G2/G3/GS/GT,
  offset shots, antenna configuration, quality summaries, and stakeout
  data), while removing the `Point3D`/DXF/`cad_server` dependencies and
  hard-coded paths of the original implementation and fixing a locale bug
  that previously skipped line processing whenever an explicit locale was
  supplied.
- `Rw5Extractor`, the `openroland_survey.extractors`-registered adapter that
  maps raw RW5 observations onto the canonical `SurveyPointRecord` model,
  plus the package-level `extract_points()`, `export_to_xlsx()`, and
  `export_to_database()` convenience wrappers.
- Synthetic-fixture test suite covering locale detection/fallback, encoding
  fallback, base-station grouping and splitting, offset shots, baseline
  records, stakeout data, quality/averaged statistics, canonical field
  mapping, and XLSX/database export.

### Changed

- SurvCE ``SP`` (stored / keyed-in / design) points import as
  ``kind=imported`` with null ``method`` and ``status`` (import-ness is
  Type only; was previously ``method=Stored`` / ``Imported``). Measured
  GPS/GS paths still set ``status`` from quality notes only.
- End-of-parse DEBUG summary uses the shared wording
  ``parsed N survey points from path`` (was ``GPS records``).
- Introduced a dedicated `_to_int()` helper for purely numeric fields
  (`sat_count`, `nr_of_sat_avg/min/max`, `valid_readings`, `fixed_readings`,
  `float_readings`, `dgps_readings`) instead of the alphanumeric-tolerant
  `_to_int_or_str()`, matching their `Optional[int]` model typing and
  satisfying `mypy`.

### Fixed

- Measured GPS/GS rover occupations now export ``kind=gps`` (GPS field
  observation) instead of leaving Type empty; SurvCE ``SP`` imports still
  use ``kind=imported``.
- RW5 observations without ``GT`` now get ``observed_at_utc`` from ``G0``
  moment, or from ``--DT``/``--TM`` local time when no moment exists.
- ``--DT``/``--TM`` before an offset GPS block attach to the next rover,
  not the previous completed shot.
- Do not invent a placeholder rover from base-only ``--GT`` lines that
  follow ``BP`` / ``--GS … --Base`` (SurvCE base-setup occupation
  windows). Those previously created a ``?`` GPS stub with no NEH and
  triggered ``missing projected north/east/height`` extractor warnings
  on base-only jobs such as ``MUNTENI-101-L.rw5``.
- Treat empty SurvCE ``G0`` ``Base ID read at rover:`` values as missing
  (keep any prior ``base_id``) instead of DEBUG-logging them as
  unrecognized.
- Isolate each SurvCE ``CTSS``/``CTSD`` stakeout block on a pending buffer,
  assign it to the matching ``Stake Pt#`` / ``Design Pt#`` observation, and
  keep trailing empty Ref columns on the final values fragment. Repeated
  dumps with different column schemas no longer produce labels/values
  length mismatches or clobber an already-matched stakeout with orphan
  alignment (``PP``) rows.
- Recognize SurvCE total-station job notes ``TS Scale``, ``EDM Mode``, and
  ``P.C. mm Applied``; acknowledge ``OC``/``BK``/``BD``/``SS`` records and
  ``Calculated``/``Measured``/``Delta`` backsight notes without DEBUG noise
  (polar reduction to NEH not implemented yet).
- Parse SurvCE ``Point Used`` and ``Base Configuration by Previously
  Surveyed`` onto the matching ``BP`` (before or after the base record).
- Parse SurvCE ``SP North: …, SP East: …, Elv: …`` base-setup notes onto
  the current base's projected NEH, and recognize ``Base Configuration by
  Entering State Plane Coordinates``.
- Accept English note labels even when the file is detected as Romanian
  (mixed-locale SurvCE jobs such as Romanian ``Definit de utilizator``
  with English ``Equipment``).
- Parse SurvCE ``HSIG Avg`` / ``VSIG Avg`` as aliases of ``HSDV Avg`` /
  ``VSDV Avg``; ignore quality-summary ``NSIG`` / ``ESIG`` like NSDV/ESDV.
- Parse localization ``Translate: dy=…, dx=…, dz=…`` onto job-level
  ``localization_translate_*`` fields; ignore ``From Pt…`` report lines.
- Parse SurvCE ``AUTO Readings`` occupation notes onto
  ``Rw5GpsPoint.auto_readings`` (same ``N of M`` shape as Valid/Fixed).
- Ignore job-header ``CTSS``/``CTSD`` stakeout dumps that appear before the
  first rover GPS observation so they no longer invent a placeholder ``?``
  point (and the extractor silently skips any residual ``?`` without NEH).
- Parse SurvCE quality-summary ``AGE`` onto ``Rw5GpsPoint.age_avg`` (was
  logged as ``unknown quality item``).
- Ignore trailing bare ``None`` / ``null`` fragments on ``--RTK Method``
  notes (for example ``Device: None, None``) instead of DEBUG-logging them
  as unknown parts.
- Parse empty SurvCE ``Network:`` RTK sub-fields (whitespace-only value)
  without logging ``unknown RTK method part``; ``rtk_network`` is stored as
  an empty string.
- Recognize South/Cube ``Initialization time`` free-text lines and labelled
  GNSS observation notes without ``unknown label`` / ``unrecognized RW5
  record code`` DEBUG noise.
- Parse SurvCE ``HSDV Avg`` / ``VSDV Avg`` (into horizontal/vertical RMS
  stats), ``AGE Avg``, ``NRMS Avg``, and ``ERMS Avg`` occupation notes.
- Treat repeated RTK device names (``Phone Internet: …``, ``Device
  Internet: …``) as the network mountpoint sub-key.
- Skip blank / whitespace-only RW5 lines without ``unrecognized RW5
  record code`` DEBUG noise.
- Split SurvCE notes glued onto tagged records without a newline
  (``G3,...YZ0.12--Number of Satellites Avg: …``); real ``,--comment``
  fields stay intact.
- UTF-8 → Windows-1252 RW5 decode fallback no longer attaches
  ``exc_info`` (degree-symbol files no longer dump a traceback at DEBUG).
- Ignore SurvX ``GNSS Position Adjustment`` notes at trace level.
- Treat SurvCE ``CTSD`` trailing commas as line-wrap markers, not empty
  stakeout fields. Files such as stakeout jobs that wrap labels after
  ``Stake Nor,`` previously got a labels/values length mismatch and the
  extractor dropped every affected point as an "invalid coordinate".
  Wrap markers are dropped when the next ``CTSD`` fragment arrives; a
  final fragment may still end with empty Ref columns. Stakeout pairing
  failures no longer discard an otherwise valid observation.
- Parse SurvCE `--HDOP Avg: … Min: … Max: …` notes without raising (indexes
  assumed the Romanian `m Min … to …` shape and produced thousands of
  `failed to parse line` warnings).
- Read SurvCE `BP` elevation from `ET` as well as `EL`/`HT`.
- Do not invent `?` rover stubs from `DT`/`TM` on base-only configuration
  blocks; timestamp the base instead. Projected base `--GS` coords are
  stored on `Rw5BasePoint`.
- Silently ignore SurvCE ``GPS`` / ``GS`` lines whose point name is the
  RTK/VRS network mountpoint (for example ``RO_VRS_3.1_GG``) or the
  preceding ``BP`` number without projected coordinates. Those base echoes
  previously produced repeated ``missing projected north/east/height``
  warnings and are not rover survey observations.
- Decode SurvCE/Cube-a ``LA``/``LN`` values from packed ``DD.MMSSsssss``
  into decimal degrees on ``BP`` and ``GPS`` records. Cube-a exports such
  as ``LA46.35192`` (``46°35'19″``) were previously stored as if they were
  already decimal, so imports preferred wrong WGS84 positions (~25 km off)
  while projected ``GS`` north/east stayed correct. South/Cube ``EP``
  geographic fields are left as true decimal degrees.
- Accept European ``DD-MM-YYYY`` order in SurvCE/SurvX ``JB`` job stamps
  and ``--DT`` local-date notes (for example ``DT29-08-2020``). The parser
  previously only tried US ``MM-DD-YYYY``, so day values above 12 failed
  with a DEBUG traceback and left ``job_datetime`` / ``local_time`` empty.
  Ambiguous stamps (both parts ≤ 12) still prefer US order for SurvCE
  compatibility.
- Parse South/Cube RW5 rover observations written as ``EP``/``BL``/``GS``
  blocks without classic ``GPS``/``G0`` records. The parser now materializes
  one point per rover ``BL``/``GS`` pair, copies geographic coordinates from
  ``EP``, and ignores base-station projected ``GS`` lines whose ``PN`` matches
  the preceding ``BP`` record.
- Parse SurvCE ``Valid Readings`` / ``Fixed Readings`` (and float/DGPS)
  note values written as ``N of M`` (for example ``2 of 2``). The parser
  previously tried to convert the whole string to ``int``, logged a DEBUG
  miss per observation, and left the count empty; it now keeps the leading
  count.
- Recognize Stonex Cube-a software banners
  (``--Stonex Cube-a v6.3.20.2024.07.24``) as the collector version note,
  the same way SurvCE version banners are handled.
- Parse SurvCE ``SP`` stored grid-point records
  (``SP,PN…,N …,E …,EL…``) as standalone projected points. They were
  previously logged as unrecognized and dropped, so keyed-in/design
  points never reached the canonical extract.
- Parse SurvCE ``--GS`` / ``--GT`` (and other ``--``-prefixed tagged record
  codes) as real projected-coordinate and GPS-time records. Older SurvCE
  exports often comment those lines; they were previously treated as free
  text, so every observation lacked north/east/height and was skipped.
- Fixed a `[tools.black]` typo in `pyproject.toml` (should be `[tool.black]`)
  that silently made `make lint`/`make delint` fall back to Black's default
  88-column line length instead of the repository's 80-column standard.
